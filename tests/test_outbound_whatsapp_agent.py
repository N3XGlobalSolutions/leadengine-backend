"""Tests for the production WhatsApp sales agent: instructions-driven prompt,
per-turn tool routing, <state> parsing, reply validation and failure handling."""

import copy
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.models.outbound.outbound_entities import (
    ConversationStage,
    OutboundAgentSettings,
    OutboundAIMessage,
    OutboundCalendarSettings,
    OutboundMemory,
)
from app.services.ai.outbound import tool_router
from app.services.ai.outbound.agent_state import extract_state
from app.services.ai.outbound.conversation_pipeline import (
    FALLBACK_REPLY,
    OPT_OUT_REPLY,
    ApplyAgentStateStep,
    ConversationPipeline,
    OptOutStep,
    OutboundAISession,
    PersistConversationStep,
    PipelineStep,
    SendReplyStep,
)
from app.services.ai.outbound.enterprise_engine import ai_guardrails
from app.services.ai.outbound.prompt_builder import outbound_prompt_builder
from app.services.ai.outbound.response_validator import response_validator
from app.services.ai.outbound.sales_agent import HISTORY_WINDOW, outbound_sales_agent


def make_session(text="Hi", **settings_kwargs) -> OutboundAISession:
    session = OutboundAISession(
        tenant_id="tenant_1",
        campaign_id="camp_1",
        lead_id="lead_1",
        conversation_id="conv_1",
        sender_phone="+917867879476",
        incoming_text=text,
        message_id="wamid.test",
    )
    session.agent_settings = OutboundAgentSettings(tenant_id="tenant_1", **settings_kwargs)
    session.calendar_settings = OutboundCalendarSettings(tenant_id="tenant_1")
    session.memory = OutboundMemory(conversation_id="conv_1")
    session.history = [
        OutboundAIMessage(message_id="m_current", conversation_id="conv_1", role="user", content=text)
    ]
    return session


def msg(role, content="", tool_calls=None):
    return OutboundAIMessage(
        message_id=f"m_{role}_{content[:5]}", conversation_id="conv_1", role=role,
        content=content, tool_calls=tool_calls,
    )


def fake_product(name="Gaming PC", category="PC", images=None, brochure=None):
    from app.models.outbound.outbound_entities import OutboundProduct
    return OutboundProduct(
        product_id=f"prod_{name.replace(' ', '_').lower()}", tenant_id="tenant_1", name=name,
        description=f"{name} description", short_description=f"{name} short", price=85000.0, currency="INR",
        category=category, status="ACTIVE", images=images or [], brochure_pdf=brochure,
    )


@pytest.fixture(autouse=True)
def no_real_catalog():
    """Never hit DynamoDB for the product catalog; tests opt in to a fake catalog."""
    with patch.object(tool_router, "_tenant_products", side_effect=lambda session: session.products):
        yield


CONNECTED_CALENDAR = OutboundCalendarSettings(
    tenant_id="tenant_1", google_client_id="id", google_client_secret="secret", google_refresh_token="token"
)


# ── <state> parsing ────────────────────────────────────────────────────

def test_extract_state_parses_and_strips():
    reply, state = extract_state(
        'Great, thanks!\n<state>{"collected": {"Budget Range": "5 lakhs"}, "status": "qualified", "handoff": false}</state>'
    )
    assert reply == "Great, thanks!"
    assert state == {"collected": {"budget_range": "5 lakhs"}, "status": "QUALIFIED"}


def test_extract_state_handles_missing_malformed_and_cut_off():
    assert extract_state("Just a reply") == ("Just a reply", {})
    assert extract_state("Reply <state>{not json}</state>") == ("Reply", {})
    # Output cut off by max_tokens: never leak a half state line
    assert extract_state('Reply text\n<state>{"collected": {"na') == ("Reply text", {})


# ── Tool routing ───────────────────────────────────────────────────────

def test_small_talk_gets_no_tools():
    assert tool_router.select_tools(make_session("hi")) == []
    assert tool_router.select_tools(make_session("Thank you!")) == []


def test_company_question_uses_knowledge_not_products():
    session = make_session("Tell me about your company")
    session.products = [fake_product()]
    assert tool_router.select_tools(session) == tool_router.KNOWLEDGE_TOOLS


def test_prefetched_knowledge_means_no_tool_hop():
    session = make_session("Tell me about your company")
    session.retrieved_knowledge = [{"text": "We build websites.", "source": "about.pdf", "score": 0.8}]
    assert tool_router.select_tools(session) == []


def test_product_question_exposes_catalog_only_when_catalog_exists():
    session = make_session("What is the price of the premium plan?")
    session.retrieved_knowledge = [{"text": "x", "source": "s", "score": 0.9}]
    session.products = [fake_product()]
    assert tool_router.select_tools(session) == tool_router.CATALOG_TOOLS
    session.products = []
    assert tool_router.select_tools(session) == []


def test_catalog_disabled_is_respected():
    session = make_session("Show me your products", enable_product_search=False, enable_knowledge_search=False)
    session.products = [fake_product()]
    assert tool_router.select_tools(session) == []


def test_calendar_tools_need_a_connected_calendar():
    session = make_session("Can we schedule a call tomorrow at 4pm?", enable_knowledge_search=False)
    assert tool_router.select_tools(session) == []  # not connected: no fake bookings
    session.calendar_settings = CONNECTED_CALENDAR
    with patch.object(tool_router, "GOOGLE_LIBS_AVAILABLE", True, create=True), \
         patch("app.services.outbound.providers.google_calendar_provider.GOOGLE_LIBS_AVAILABLE", True):
        assert tool_router.select_tools(session) == tool_router.CALENDAR_TOOLS


def test_yes_after_call_offer_keeps_calendar_tools():
    session = make_session("yes", enable_knowledge_search=False)
    session.calendar_settings = CONNECTED_CALENDAR
    session.history = [msg("assistant", "Would you like to book a call with our counsellor?"), msg("user", "yes")]
    with patch("app.services.outbound.providers.google_calendar_provider.GOOGLE_LIBS_AVAILABLE", True):
        assert tool_router.select_tools(session) == tool_router.CALENDAR_TOOLS


# ── Prompt ─────────────────────────────────────────────────────────────

def test_prompt_is_driven_by_business_instructions():
    session = make_session("Hello", ai_name="Priya", agent_instructions="Ask which course they want, then their city.")
    session.company_name = "ABC Academy"
    session.lead_name = "Selvam"
    session.campaign_message = "Hi Selvam, thanks for your enquiry!"
    session.memory.preferences["lead_profile"] = {"collected": {"course": "B.Ed"}, "status": "IN_PROGRESS"}

    system, user = outbound_prompt_builder.build_prompts(session)

    assert "Priya" in system and "ABC Academy" in system
    assert "Ask which course they want, then their city." in system
    assert "Selvam" in system and "thanks for your enquiry" in system
    assert '"course": "B.Ed"' in system          # don't re-ask answered questions
    assert "<state>" in system                    # state contract present
    assert "## Tools" not in system or "search_company_knowledge" not in system  # no tools offered
    assert "Customer message: Hello" in user
    assert user.endswith("<state> line.)")                    # format reminder comes last


def test_prompt_falls_back_to_business_summary_and_default_flow():
    session = make_session("Hello", business_summary="We sell solar panels.")
    system, _ = outbound_prompt_builder.build_prompts(session)
    assert "We sell solar panels." in system
    assert "Conversation flow" in system


def test_prompt_only_describes_offered_tools_and_includes_reference_info():
    session = make_session("What are your timings?")
    session.active_tools = tool_router.KNOWLEDGE_TOOLS
    session.retrieved_knowledge = [{"text": "Open 9am-6pm, Mon-Sat.", "source": "faq.pdf"}]
    system, user = outbound_prompt_builder.build_prompts(session)
    assert "search_company_knowledge" in system
    assert "Product tools" not in system and "Calendar tools" not in system
    assert "Open 9am-6pm" in user


def test_instructions_are_truncated_to_bound_tokens():
    session = make_session("Hi", agent_instructions="word " * 5000)
    system, _ = outbound_prompt_builder.build_prompts(session)
    assert len(system) < 9000


# ── Reasoning loop ─────────────────────────────────────────────────────

def _llm_reply(text=None, tool_uses=None):
    content = []
    if text:
        content.append({"text": text})
    for t in tool_uses or []:
        content.append({"toolUse": t})
    return ({"role": "assistant", "content": content}, 0.1, {"prompt": 100, "completion": 20, "total": 120}, 0.0001)


@pytest.mark.anyio
async def test_single_call_reply_with_state_and_no_tools():
    session = make_session("Hello")
    with patch("app.services.ai.outbound.sales_agent.outbound_llm_service") as llm:
        llm.converse.return_value = _llm_reply(
            'Hi Selvam! Which course are you interested in?\n<state>{"collected": {}, "status": "IN_PROGRESS"}</state>'
        )
        reply = await outbound_sales_agent.execute_reasoning_loop(session)

    assert reply == "Hi Selvam! Which course are you interested in?"
    assert session.agent_state == {"status": "IN_PROGRESS"}
    assert llm.converse.call_count == 1
    assert llm.converse.call_args.kwargs["tools"] is None  # no tool specs sent for small talk


@pytest.mark.anyio
async def test_history_window_excludes_old_tool_traffic():
    session = make_session("And the fees?")
    old = []
    for i in range(30):
        old.append(msg("user", f"question {i}"))
        old.append(msg("assistant", "", tool_calls=[{"tool_use_id": f"t{i}", "name": "search_company_knowledge", "input": {}}]))
        old.append(OutboundAIMessage(message_id=f"tool{i}", conversation_id="conv_1", role="tool",
                                     content='{"results": ["big payload"]}', metadata={"tool_use_id": f"t{i}"}))
        old.append(msg("assistant", f"answer {i}"))
    session.history = old + session.history

    captured = {}

    def fake_converse(**kwargs):
        # Snapshot: the agent appends the model output to this list after the call.
        captured["messages"] = copy.deepcopy(kwargs["messages"])
        return _llm_reply("Sure.")

    with patch("app.services.ai.outbound.sales_agent.outbound_llm_service") as llm:
        llm.converse.side_effect = fake_converse
        await outbound_sales_agent.execute_reasoning_loop(session)

    sent = captured["messages"]
    flat = str(sent)
    assert "big payload" not in flat and "toolUse" not in flat
    assert len(sent) == HISTORY_WINDOW + 1          # 12 recent turns + current message
    assert sent[0]["content"][0]["text"] == "question 24"
    assert sent[-1]["content"][0]["text"].startswith("Customer message: And the fees?")


@pytest.mark.anyio
async def test_tool_hop_then_answer_and_duplicate_calls_are_cached():
    session = make_session("Tell me about your company")
    session.active_tools = tool_router.KNOWLEDGE_TOOLS
    tool_use = {"toolUseId": "t1", "name": "search_company_knowledge", "input": {"query": "company"}}
    tool_use_dup = {"toolUseId": "t2", "name": "search_company_knowledge", "input": {"query": "company"}}

    with patch("app.services.ai.outbound.sales_agent.outbound_llm_service") as llm, \
         patch("app.services.ai.outbound.sales_agent.outbound_tool_executor") as executor:
        llm.converse.side_effect = [
            _llm_reply(tool_uses=[tool_use, tool_use_dup]),
            _llm_reply('We are a web agency.\n<state>{"collected": {}, "status": "IN_PROGRESS"}</state>'),
        ]

        async def fake_execute(session, name, args):
            return {"success": True, "results": [{"source": "about", "text": "Web agency"}]}

        executor.execute_tool.side_effect = fake_execute
        reply = await outbound_sales_agent.execute_reasoning_loop(session)

    assert reply == "We are a web agency."
    assert executor.execute_tool.call_count == 1  # identical call reused
    offered = [s["toolSpec"]["name"] for s in llm.converse.call_args_list[0].kwargs["tools"]]
    assert offered == ["search_company_knowledge"]


# ── Reply validation & guardrails ──────────────────────────────────────

def test_validator_strips_state_and_keeps_legit_words():
    session = make_session()
    out = response_validator.validate_response(session, "No, this is not spam or a scam.\n<state>{}</state>")
    assert out == "No, this is not spam or a scam."


def test_validator_url_allowlist():
    session = make_session()
    session.client_domain = "n3xglobalsolutions.com"
    text = "See https://n3xglobalsolutions.com/courses and https://made-up-site.example/offer"
    out = response_validator.validate_response(session, text)
    assert "https://n3xglobalsolutions.com/courses" in out
    assert "made-up-site" not in out


def test_validator_truncates_only_far_beyond_limit_at_sentence_boundary():
    session = make_session(max_response_length=300)
    long_text = "This is a sentence about the course. " * 40
    out = response_validator.validate_response(session, long_text)
    assert len(out) <= 800 and out.endswith(".")


def test_offer_and_office_are_not_scrubbed():
    session = make_session()
    text = "We have a great offer of free counselling at our office."
    assert ai_guardrails.clean_hallucinated_offers(session, text) == text


def test_discount_allowed_when_business_instructions_mention_it():
    session = make_session(agent_instructions="Mention the 10% discount for early enrolment.")
    text = "You can get a 10% discount if you enrol this week."
    assert ai_guardrails.clean_hallucinated_offers(session, text) == text


# ── Pipeline behaviour ─────────────────────────────────────────────────

class _Boom(PipelineStep):
    async def execute(self, session):
        raise RuntimeError("bedrock throttled")


class _Record(PipelineStep):
    def __init__(self, always_run=False):
        self.always_run = always_run
        self.ran = False

    async def execute(self, session):
        self.ran = True


@pytest.mark.anyio
async def test_failure_still_persists_and_replies_with_fallback():
    session = make_session("Hello")
    skipped, persisted = _Record(), _Record(always_run=True)
    await ConversationPipeline([_Boom(), skipped, persisted]).run(session)
    assert skipped.ran is False
    assert persisted.ran is True
    assert session.final_reply == FALLBACK_REPLY


@pytest.mark.anyio
async def test_persist_records_customer_message_when_llm_step_never_ran():
    session = make_session("Hello")
    session.final_reply = FALLBACK_REPLY
    with patch("app.services.ai.outbound.conversation_pipeline.outbound_db_service") as db:
        await PersistConversationStep().execute(session)
    saved = [c.args[0] for c in db.add_message.call_args_list]
    assert [m.role for m in saved] == ["user", "assistant"]
    assert saved[0].content == "Hello" and saved[1].content == FALLBACK_REPLY


@pytest.mark.anyio
async def test_send_reply_uses_loaded_tenant_and_respects_skip():
    session = make_session("Hello")
    session.final_reply = "Hi!"
    session.tenant = MagicMock()
    with patch("app.services.outbound.whatsapp_sender.whatsapp_sender") as sender:
        async def fake_send(**kwargs):
            return {"status": "sent", "message_id": "wamid.out"}
        sender.send_message.side_effect = fake_send
        await SendReplyStep().execute(session)
        assert sender.send_message.call_count == 1
        session.skip_reply = True
        await SendReplyStep().execute(session)
        assert sender.send_message.call_count == 1


@pytest.mark.anyio
async def test_opt_out_short_circuits_llm():
    session = make_session("STOP")
    await OptOutStep().execute(session)
    assert session.skip_llm is True
    assert session.final_reply == OPT_OUT_REPLY
    assert session.memory.preferences["opted_out"] is True

    returning = make_session("Hi, I'm interested again")
    returning.memory.preferences["opted_out"] = True
    await OptOutStep().execute(returning)
    assert returning.skip_llm is False and returning.memory.preferences["opted_out"] is False


@pytest.mark.anyio
async def test_agent_state_updates_lead_profile_and_stage():
    session = make_session("I'm a graduate and want to start next month")
    session.agent_state = {"collected": {"qualification": "graduate", "budget": "50k"}, "status": "QUALIFIED",
                           "score": 82, "score_reason": "Graduate, starting next month", "objection": "PRICE"}

    await ApplyAgentStateStep().execute(session)

    profile = session.memory.preferences["lead_profile"]
    assert profile["collected"] == {"qualification": "graduate", "budget": "50k"}
    assert profile["status"] == "QUALIFIED"
    assert session.stage == ConversationStage.QUALIFIED
    assert session.memory.budget == "50k"
    assert profile["score"] == 82 and profile["score_reason"] == "Graduate, starting next month"
    assert profile["objections"] == ["PRICE"]


# ── Webhook helpers & history query ────────────────────────────────────

def test_webhook_reads_buttons_and_interactive_replies():
    from app.routes.v1.outbound_webhook import _extract_text
    assert _extract_text({"type": "text", "text": {"body": "hello"}}) == "hello"
    assert _extract_text({"type": "button", "button": {"text": "Yes, interested", "payload": "YES"}}) == "Yes, interested"
    assert _extract_text({"type": "interactive", "interactive": {"button_reply": {"id": "b1", "title": "Book a call"}}}) == "Book a call"
    assert _extract_text({"type": "image", "image": {}}) is None


def test_webhook_signature_check():
    import hashlib, hmac
    from app.routes.v1 import outbound_webhook as wh
    body = b'{"entry": []}'
    with patch.object(wh, "APP_SECRET", "s3cret"):
        good = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
        assert wh._signature_is_valid(body, good) is True
        assert wh._signature_is_valid(body, "sha256=deadbeef") is False
        assert wh._signature_is_valid(body, None) is False
    with patch.object(wh, "APP_SECRET", None):
        assert wh._signature_is_valid(body, None) is True


def test_pick_lead_prefers_tenant_owning_the_receiving_number():
    from app.routes.v1.outbound_webhook import _pick_lead
    items = [
        {"tenant_id": "other", "lead_id": "l2", "campaign_id": "c2", "created_at": "2026-11-25T10:00:00"},
        {"tenant_id": "mine", "lead_id": "l1", "campaign_id": "c1", "created_at": "2026-11-24T10:00:00"},
    ]
    tenants = {
        "other": MagicMock(config=MagicMock(whatsapp_phone_number_id="999")),
        "mine": MagicMock(config=MagicMock(whatsapp_phone_number_id="1377207628809602")),
    }
    with patch("app.services.tenant_service.get_tenant", side_effect=lambda t: tenants[t]):
        assert _pick_lead(items, "1377207628809602")["lead_id"] == "l1"
        assert _pick_lead(items, None)["lead_id"] == "l2"  # no number id: most recent


def test_history_query_returns_latest_messages_in_order():
    from app.services.outbound.outbound_db_service import outbound_db_service
    newest_first = [
        {"message_id": f"m{i}", "conversation_id": "c", "role": "user", "content": f"msg {i}",
         "timestamp": datetime(2026, 11, 25, 10, i, tzinfo=timezone.utc).isoformat()}
        for i in (3, 2, 1)
    ]
    table = MagicMock()
    table.query.return_value = {"Items": newest_first}
    with patch("app.services.outbound.outbound_db_service.get_dynamodb_table", return_value=table):
        messages = outbound_db_service.get_conversation_messages("c", limit=3)
    assert table.query.call_args.kwargs["ScanIndexForward"] is False
    assert [m.content for m in messages] == ["msg 1", "msg 2", "msg 3"]
