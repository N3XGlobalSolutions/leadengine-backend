"""Tests for the production outbound features: real cost accounting, SQS campaign
execution, delivery/reply tracking, opt-outs, product media and analytics."""

import importlib
import json
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.models.outbound.campaign import Campaign, CampaignStatus
from app.models.outbound.outbound_entities import (
    ConversationStage, OutboundAgentSettings, OutboundAIMessage, OutboundCalendarSettings,
    OutboundConversation, OutboundMemory, OutboundProduct,
)
from app.models.outbound.outbound_lead import MessageStatus, OutboundLead
from app.services.ai.outbound import tool_router
from app.services.ai.outbound.agent_state import extract_state
from app.services.ai.outbound.conversation_pipeline import OutboundAISession
from app.services.outbound import pricing


def make_session(text="Hi", products=None, **settings):
    s = OutboundAISession(
        tenant_id="t1", campaign_id="c1", lead_id="l1", conversation_id="conv1",
        sender_phone="+917867879476", incoming_text=text, message_id="wamid.in",
    )
    s.agent_settings = OutboundAgentSettings(tenant_id="t1", **settings)
    s.calendar_settings = OutboundCalendarSettings(tenant_id="t1")
    s.memory = OutboundMemory(conversation_id="conv1")
    s.products = products or []
    s.history = [OutboundAIMessage(message_id="cur", conversation_id="conv1", role="user", content=text)]
    return s


def product(name="Gaming PC", category="PC", images=None, brochure=None, pid=None):
    return OutboundProduct(
        product_id=pid or f"p_{name.replace(' ', '_').lower()}", tenant_id="t1", name=name,
        description="desc", short_description="short", price=85000.0, currency="INR",
        category=category, status="ACTIVE", images=images or [], brochure_pdf=brochure,
    )


@pytest.fixture(autouse=True)
def no_real_catalog():
    with patch.object(tool_router, "_tenant_products", side_effect=lambda session: session.products):
        yield


# ── Pricing ────────────────────────────────────────────────────────────

def test_glm5_cost_uses_aws_list_prices():
    # 10K input * $0.0012/1K + 2K output * $0.00384/1K = $0.012 + $0.00768
    cost = pricing.llm_cost_usd("zai.glm-5", 10_000, 2_000)
    assert cost == pytest.approx(0.01968)
    assert pricing.to_inr(cost) == pytest.approx(0.01968 * pricing.DEFAULT_USD_INR_RATE)
    assert pricing.embedding_cost_usd("amazon.titan-embed-text-v2:0", 1000) == pytest.approx(0.000024)


def test_pricing_env_overrides(monkeypatch):
    monkeypatch.setenv("OUTBOUND_USD_INR_RATE", "90")
    monkeypatch.setenv("OUTBOUND_LLM_OUTPUT_PRICE_PER_1K", "0.01")
    assert pricing.usd_inr_rate() == 90
    assert pricing.llm_cost_usd("zai.glm-5", 0, 1000) == pytest.approx(0.01)
    info = pricing.pricing_info("zai.glm-5", "amazon.titan-embed-text-v2:0")
    assert info["prompt_caching_supported"] is False and info["usd_inr_rate"] == 90


def test_session_usage_accumulates_real_bedrock_usage():
    s = make_session()
    s.add_llm_usage({"prompt": 1000, "completion": 200, "cache_read": 300, "total": 1500}, 0.002)
    s.add_llm_usage({"prompt": 500, "completion": 100, "total": 600}, 0.001)
    s.token_usage["embedding"] = 1000
    assert s.llm_calls == 2
    assert s.token_usage["prompt"] == 1500 and s.token_usage["cache_read"] == 300
    assert s.total_cost_usd() == pytest.approx(0.003 + 0.000024)


# ── State: real score / objection ──────────────────────────────────────

def test_state_score_and_objection_are_validated():
    _, state = extract_state('Ok<state>{"score": 140, "score_reason": "Graduate", "objection": "price", "status": "in_progress"}</state>')
    assert state["score"] == 100 and state["objection"] == "PRICE" and state["status"] == "IN_PROGRESS"
    _, bad = extract_state('Ok<state>{"score": "high", "objection": "weather"}</state>')
    assert "score" not in bad and "objection" not in bad


# ── Product routing & media ────────────────────────────────────────────

def test_pc_pics_follow_up_gets_catalog_tools():
    """The exact chat that failed: 'product pics' then 'I need pc pics'."""
    s = make_session("I need pc pics", products=[product()])
    s.retrieved_knowledge = [{"text": "x", "source": "s"}]
    s.history = [
        OutboundAIMessage(message_id="u1", conversation_id="conv1", role="user", content="Hey can share me some of the product pics"),
        OutboundAIMessage(message_id="a1", conversation_id="conv1", role="assistant", content="Our team can share details."),
        OutboundAIMessage(message_id="u2", conversation_id="conv1", role="user", content="I need pc pics"),
    ]
    assert set(tool_router.CATALOG_TOOLS) <= set(tool_router.select_tools(s))


def test_catalog_name_mention_triggers_catalog():
    s = make_session("is the gaming one good for gta", products=[product()])
    s.retrieved_knowledge = [{"text": "x", "source": "s"}]
    assert set(tool_router.CATALOG_TOOLS) <= set(tool_router.select_tools(s))


@pytest.mark.anyio
async def test_send_product_images_by_name_queues_real_whatsapp_images():
    from app.services.ai.outbound.tool_definitions import send_product_images
    pc = product(images=["https://cdn.example.com/pc1.jpg", "https://cdn.example.com/pc2.jpg"])
    s = make_session("send pics", products=[pc])
    with patch("app.services.ai.outbound.tool_definitions.outbound_product_service") as svc:
        svc.get_product.return_value = None
        svc.list_products.return_value = [pc]
        result = await send_product_images(s, product="gaming pc")
    assert result["success"] and result["images_queued"] == 2
    assert [m["type"] for m in s.outgoing_media] == ["image", "image"]
    assert s.outgoing_media[0]["caption"] == "Gaming PC"
    assert "pc1.jpg" not in json.dumps({k: v for k, v in result.items() if k != "note"})  # no links pasted


@pytest.mark.anyio
async def test_send_images_reports_missing_photos_honestly():
    from app.services.ai.outbound.tool_definitions import send_product_images
    pc = product(images=[])
    s = make_session("pics", products=[pc])
    with patch("app.services.ai.outbound.tool_definitions.outbound_product_service") as svc:
        svc.get_product.return_value = None
        result = await send_product_images(s, product="Gaming PC")
    assert result["success"] is False and "No photos" in result["error"]
    assert s.outgoing_media == []


@pytest.mark.anyio
async def test_send_reply_step_sends_text_then_media():
    from app.services.ai.outbound.conversation_pipeline import SendReplyStep
    s = make_session("pics")
    s.final_reply = "Here are the photos!"
    s.tenant = MagicMock()
    s.outgoing_media = [{"type": "image", "link": "https://cdn.example.com/a.jpg", "caption": "PC"},
                        {"type": "document", "link": "https://cdn.example.com/b.pdf", "filename": "PC.pdf"}]
    calls = []

    async def ok(*args, **kwargs):
        calls.append(kwargs.get("caption") or kwargs.get("message") or args[1])
        return {"status": "sent", "message_id": "wamid.x"}

    with patch("app.services.outbound.whatsapp_sender.whatsapp_sender") as sender:
        sender.send_message.side_effect = ok
        sender.send_image.side_effect = ok
        sender.send_document.side_effect = ok
        await SendReplyStep().execute(s)
    assert sender.send_message.call_count == 1 and sender.send_image.call_count == 1 and sender.send_document.call_count == 1


# ── Campaign executor ──────────────────────────────────────────────────

def make_campaign(**kw):
    base = dict(campaign_id="c1", tenant_id="t1", name="Test", status=CampaignStatus.RUNNING,
                message_type="text", message_template="Hi {{name}}", batch_size=10,
                message_delay_seconds=0, batch_delay_seconds=60)
    base.update(kw)
    return Campaign(**base)


def make_lead(lid="l1", phone="+917867879476", name="Selvam"):
    return OutboundLead(lead_id=lid, campaign_id="c1", tenant_id="t1", name=name, phone=phone)


def test_template_params_resolve_per_lead():
    from app.services.outbound.campaign_executor_service import resolve_template_params, campaign_message_text
    c = make_campaign(message_type="template", template_name="intro", template_language="en",
                      template_body="Hi {{1}}, call us at {{2}}", template_params=["{{name}}", "080-123"])
    assert resolve_template_params(c, "Selvam", "+91") == ["Selvam", "080-123"]
    assert resolve_template_params(c, "", "+91")[0] == "there"
    assert campaign_message_text(c, "Selvam", "+91") == "Hi Selvam, call us at 080-123"


@pytest.mark.anyio
async def test_executor_never_sends_twice_and_skips_opted_out():
    mod = importlib.import_module("app.services.outbound.campaign_executor_service")
    ex = mod.CampaignExecutorService()
    c = make_campaign()
    tenant = MagicMock()
    with patch.object(mod, "outbound_lead_service") as leads, \
         patch.object(mod, "whatsapp_sender") as sender, \
         patch.object(mod, "campaign_service") as campaigns, \
         patch.object(mod, "message_service") as messages, \
         patch("app.services.outbound.outbound_db_service.outbound_db_service") as db:
        sender.normalize_phone.side_effect = lambda p: p
        sender.render_template.side_effect = lambda t, d: t.replace("{{name}}", d["name"])

        # Already claimed by another run -> nothing sent
        leads.claim_lead_for_sending.return_value = False
        assert await ex._send_to_lead(c, make_lead(), tenant) is False
        sender.send_message.assert_not_called()

        # Opted out -> SKIPPED, not sent
        leads.claim_lead_for_sending.return_value = True
        db.is_opted_out.return_value = True
        assert await ex._send_to_lead(c, make_lead(), tenant) is False
        args = leads.update_lead_message_status.call_args
        assert args.args == ("c1", "l1", MessageStatus.SKIPPED) and "Opted out" in args.kwargs["error_reason"]
        campaigns.increment_campaign_counter.assert_called_with("c1", "skipped_count")
        sender.send_message.assert_not_called()


@pytest.mark.anyio
async def test_executor_simulation_is_failed_not_sent_and_real_send_is_tracked():
    mod = importlib.import_module("app.services.outbound.campaign_executor_service")
    ex = mod.CampaignExecutorService()
    c = make_campaign()
    with patch.object(mod, "outbound_lead_service") as leads, \
         patch.object(mod, "whatsapp_sender") as sender, \
         patch.object(mod, "campaign_service") as campaigns, \
         patch.object(mod, "message_service") as messages, \
         patch("app.services.outbound.outbound_db_service.outbound_db_service") as db:
        leads.claim_lead_for_sending.return_value = True
        db.is_opted_out.return_value = False
        sender.normalize_phone.side_effect = lambda p: p
        sender.render_template.side_effect = lambda t, d: t
        messages.create_message.return_value = MagicMock(message_id="m1")

        async def simulated(*a, **k):
            return {"status": "simulated"}
        sender.send_message.side_effect = simulated
        assert await ex._send_to_lead(c, make_lead(), MagicMock()) is False
        campaigns.increment_campaign_counter.assert_called_with("c1", "failed_count")
        assert "not enabled" in leads.update_lead_message_status.call_args.kwargs["error_reason"]

        async def sent(*a, **k):
            return {"status": "sent", "message_id": "wamid.out"}
        sender.send_message.side_effect = sent
        assert await ex._send_to_lead(c, make_lead(), MagicMock()) is True
        db.save_wamid_link.assert_called_once()
        assert db.save_wamid_link.call_args.args[0] == "wamid.out"


@pytest.mark.anyio
async def test_batch_job_queues_next_batch_with_batch_delay():
    mod = importlib.import_module("app.services.outbound.campaign_executor_service")
    ex = mod.CampaignExecutorService()

    async def fake_batch(campaign_id, deadline=None):
        return 120

    with patch.object(ex, "process_next_batch", side_effect=fake_batch), \
         patch.object(mod.outbound_queue, "enqueue", return_value=True) as enqueue:
        await ex.run_batch_job("c1")
    enqueue.assert_called_once_with(mod.outbound_queue.MSG_CAMPAIGN_BATCH, {"campaign_id": "c1"}, 120)


# ── Webhook: delivery and reply tracking ───────────────────────────────

def test_status_webhooks_update_counters_once_and_forward_only():
    from app.routes.v1 import outbound_webhook as wh
    link = {"campaign_id": "c1", "lead_id": "l1", "message_id": "m1", "previous_status": "sent"}
    with patch.object(wh.outbound_db_service, "advance_wamid_status") as advance, \
         patch("app.services.outbound.campaign_service.campaign_service") as campaigns, \
         patch("app.services.outbound.outbound_lead_service.outbound_lead_service") as leads, \
         patch("app.services.outbound.message_service.message_service"):
        advance.return_value = dict(link)
        wh._apply_status_updates([{"id": "wamid.1", "status": "delivered"}])
        campaigns.increment_campaign_counter.assert_called_once_with("c1", "delivered_count")

        campaigns.reset_mock()
        advance.return_value = None  # duplicate / out-of-order webhook
        wh._apply_status_updates([{"id": "wamid.1", "status": "delivered"}])
        campaigns.increment_campaign_counter.assert_not_called()

        advance.return_value = dict(link, previous_status="delivered")
        wh._apply_status_updates([{"id": "wamid.1", "status": "failed", "errors": [{"code": 131047}]}])
        calls = [c.args for c in campaigns.increment_campaign_counter.call_args_list]
        assert ("c1", "sent_count", -1) in calls and ("c1", "failed_count") in calls
        assert "24 hours" in leads.update_lead_message_status.call_args.kwargs["error_reason"]


# ── Worker dispatch ────────────────────────────────────────────────────

def test_worker_routes_jobs_and_reports_only_failed_outbound_messages():
    from app.workers import worker
    records = [
        {"messageId": "a", "body": json.dumps({"type": "campaign_batch", "campaign_id": "c1"})},
        {"messageId": "b", "body": json.dumps({"type": "whatsapp_reply", "tenant_id": "t", "campaign_id": "c", "lead_id": "l",
                                               "conversation_id": "x", "sender_phone": "+91", "incoming_text": "hi", "message_id": "w"})},
    ]
    calls = []

    async def fake_job(job_type, body):
        calls.append(job_type)
        if job_type == "whatsapp_reply":
            raise RuntimeError("boom")

    with patch.object(worker, "_run_outbound_job", side_effect=fake_job), patch.object(worker, "_process_document") as docs:
        result = worker.handler({"Records": records}, None)
    assert calls == ["campaign_batch", "whatsapp_reply"]
    assert result == {"batchItemFailures": [{"itemIdentifier": "b"}]}
    docs.assert_not_called()


def test_worker_keeps_document_ingestion_behaviour():
    from app.workers import worker
    with patch.object(worker, "_process_document") as docs:
        worker.handler({"Records": [{"messageId": "d", "body": json.dumps({"tenant_id": "t", "s3_key": "k"})}]}, None)
    docs.assert_called_once()


# ── Analytics ──────────────────────────────────────────────────────────

def test_analytics_are_built_from_real_conversation_usage():
    from app.routes.v1 import outbound_ai
    now = datetime.now(timezone.utc)
    convs = [
        OutboundConversation(conversation_id="a", tenant_id="t1", lead_id="l1", campaign_id="c1", started_at=now,
                             last_message_at=now, stage=ConversationStage.QUALIFIED, response_count=4,
                             tokens_input=8000, tokens_output=1200, llm_calls=5, cost_usd=0.0142,
                             token_usage_sum=9200, lead_status="QUALIFIED", lead_score=85, objections=["PRICE"],
                             tool_counts={"send_product_images": 1}, lead_name="Selvam", customer_message_count=4),
        OutboundConversation(conversation_id="b", tenant_id="t1", lead_id="l2", campaign_id="c1", started_at=now,
                             last_message_at=now - timedelta(days=3), response_count=2, token_usage_sum=3000,
                             lead_status="IN_PROGRESS"),  # legacy: tokens without breakdown
    ]
    with patch.object(outbound_ai, "outbound_db_service") as db, \
         patch("app.services.outbound.campaign_service.campaign_service") as campaigns:
        db.list_tenant_conversations.return_value = convs
        db.list_tenant_meetings.return_value = []
        db.get_daily_usage.return_value = [{"day": now.date().isoformat(), "tokens_input": 8000, "cost_usd": 0.0142}]
        campaigns.list_campaigns.return_value = [SimpleNamespace(campaign_id="c1", name="Nov follow-up")]
        data = outbound_ai.build_outbound_analytics("t1", days=7)

    t = data["totals"]
    assert t["tokens_input"] == 8000 and t["tokens_output"] == 1200 and t["tokens_untracked"] == 3000
    assert t["cost_inr"] == pytest.approx(0.0142 * pricing.usd_inr_rate())
    assert data["leads"]["qualified"] == 1 and data["leads"]["avg_score"] == 85
    assert data["objections"] == {"PRICE": 1} and data["tools"] == {"send_product_images": 1}
    assert len(data["daily"]) == 7 and data["daily"][-1]["tokens_input"] == 8000
    assert data["campaigns"][0]["campaign_name"] == "Nov follow-up"
    assert data["conversations"][0]["cost_inr"] == pytest.approx(0.0142 * pricing.usd_inr_rate())


@pytest.mark.anyio
async def test_photos_are_not_resent_unless_customer_asks_again():
    from app.services.ai.outbound.tool_definitions import send_product_images
    pc = product(images=["https://cdn.example.com/pc1.jpg"])
    s = make_session("my budget is 1.5 lakhs", products=[pc])
    s.memory.preferences["media_sent"] = [pc.product_id]
    with patch("app.services.ai.outbound.tool_definitions.outbound_product_service") as svc:
        svc.get_product.return_value = None
        result = await send_product_images(s, product="Gaming PC")
        assert result["success"] is False and s.outgoing_media == []
        s.incoming_text = "send the pics again please"
        result = await send_product_images(s, product="Gaming PC")
    assert result["success"] is True and len(s.outgoing_media) == 1
