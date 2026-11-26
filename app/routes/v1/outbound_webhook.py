"""WhatsApp Webhook Router for incoming customer messages."""

import hashlib
import hmac
import json
import os
import uuid
from typing import Any, Dict, List, Optional
import boto3
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.core.dynamodb import get_dynamodb_table
from app.services.outbound.outbound_db_service import outbound_db_service
from app.services.outbound.whatsapp_sender import whatsapp_sender
from app.services.ai.outbound.conversation_orchestrator import outbound_conversation_orchestrator

logger = get_logger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api/outbound", tags=["Outbound Webhooks"])

# Hub Verify Token for Facebook Setup
VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "leadengine_verify_token")

# Meta App Secret (App Dashboard > App settings > Basic). When set, every webhook
# POST must carry a valid X-Hub-Signature-256 header, so nobody else can post fake
# messages that trigger replies and LLM costs.
APP_SECRET = os.environ.get("WHATSAPP_APP_SECRET") or getattr(settings, "WHATSAPP_APP_SECRET", None)
if not APP_SECRET:
    logger.warning("[Webhook] WHATSAPP_APP_SECRET is not set: webhook signatures are not verified.")


def _signature_is_valid(raw_body: bytes, signature_header: Optional[str]) -> bool:
    if not APP_SECRET:
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.split("=", 1)[1])


def _extract_text(msg: Dict[str, Any]) -> Optional[str]:
    """Customer text from typed messages, template quick-reply buttons and interactive replies."""
    msg_type = msg.get("type")
    if msg_type == "text":
        return (msg.get("text") or {}).get("body")
    if msg_type == "button":  # quick-reply button on a template message
        button = msg.get("button") or {}
        return button.get("text") or button.get("payload")
    if msg_type == "interactive":
        interactive = msg.get("interactive") or {}
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        return reply.get("title") or reply.get("id")
    return None


def _apply_status_updates(statuses: List[Dict[str, Any]]) -> None:
    """Track real delivery: update the lead, the message record and the campaign counters.

    Only campaign messages are tracked (AI replies have no link record). Updates are
    forward-only (sent -> delivered -> read, or failed), so retried or out-of-order
    webhooks never double count.
    """
    from app.models.outbound.outbound_lead import MessageStatus
    from app.models.outbound.message import OutboundMessageStatus
    from app.services.outbound.campaign_service import campaign_service
    from app.services.outbound.outbound_lead_service import outbound_lead_service
    from app.services.outbound.message_service import message_service
    from app.services.outbound.whatsapp_sender import explain_meta_error

    for st in statuses:
        wamid, state = st.get("id"), st.get("status")
        error_text = None
        if state == "failed":
            err = (st.get("errors") or [{}])[0]
            error_text = explain_meta_error(err.get("code"), (err.get("error_data") or {}).get("details") or err.get("title") or "")
            logger.warning(f"[Webhook] Message {wamid} to {st.get('recipient_id')} FAILED: {error_text}")

        if not wamid or state not in ("sent", "delivered", "read", "failed"):
            continue
        try:
            link = outbound_db_service.advance_wamid_status(wamid, state)
            if not link or link.get("previous_status") == state:
                continue
            campaign_id, lead_id, previous = link["campaign_id"], link["lead_id"], link.get("previous_status", "sent")

            if state == "delivered":
                outbound_lead_service.update_lead_message_status(campaign_id, lead_id, MessageStatus.DELIVERED)
                campaign_service.increment_campaign_counter(campaign_id, "delivered_count")
                message_service.update_message_status(link["message_id"], OutboundMessageStatus.DELIVERED)
            elif state == "read":
                outbound_lead_service.update_lead_message_status(campaign_id, lead_id, MessageStatus.READ)
                campaign_service.increment_campaign_counter(campaign_id, "read_count")
                if previous == "sent":  # read without a separate delivered event
                    campaign_service.increment_campaign_counter(campaign_id, "delivered_count")
            elif state == "failed":
                outbound_lead_service.update_lead_message_status(campaign_id, lead_id, MessageStatus.FAILED, error_reason=error_text)
                message_service.update_message_status(link["message_id"], OutboundMessageStatus.FAILED, error_message=error_text)
                # Meta accepted it (counted as sent) but could not deliver it: move it to failed.
                campaign_service.increment_campaign_counter(campaign_id, "sent_count", -1)
                campaign_service.increment_campaign_counter(campaign_id, "failed_count")
        except Exception as e:
            logger.error(f"[Webhook] Could not apply status {state} for {wamid}: {e}")


def _pick_lead(items: List[Dict[str, Any]], phone_number_id: Optional[str]) -> Dict[str, Any]:
    """Most recent campaign lead for this phone, preferring the tenant that owns the receiving number.

    The same customer can be a lead of several tenants; the WhatsApp number that
    received the message tells us which business they are talking to.
    """
    items = sorted(items, key=lambda x: x.get("created_at", ""), reverse=True)
    if phone_number_id:
        from app.services import tenant_service
        checked: Dict[str, bool] = {}
        for item in items:
            tenant_id = item.get("tenant_id")
            if tenant_id not in checked:
                try:
                    tenant = tenant_service.get_tenant(tenant_id)
                    owned = getattr(getattr(tenant, "config", None), "whatsapp_phone_number_id", None)
                    checked[tenant_id] = bool(owned) and str(owned) == str(phone_number_id)
                except Exception:
                    checked[tenant_id] = False
            if checked[tenant_id]:
                return item
    return items[0]


# ── 1. HUB VERIFICATION (GET) ─────────────────────────────────────────

@router.get("/webhook", response_class=PlainTextResponse)
async def verify_webhook(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
):
    """Facebook Webhook verification endpoint."""
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        logger.info("[Webhook] Webhook verified successfully by Meta.")
        return hub_challenge

    logger.warning("[Webhook] Failed verify handshake (token mismatch).")
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Verification token mismatch"
    )


# ── 2. WEBHOOK INBOUND DISPATCH (POST) ────────────────────────────────

@router.post("/webhook")
async def process_webhook(
    request: Request,
    background_tasks: BackgroundTasks
):
    """WhatsApp Incoming replies webhook handler."""
    raw_body = await request.body()
    if not _signature_is_valid(raw_body, request.headers.get("X-Hub-Signature-256")):
        logger.warning("[Webhook] Rejected request with invalid signature.")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    try:
        payload = json.loads(raw_body or b"{}")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # 1. Parse payload fields
    entry = payload.get("entry", [])
    if not entry or not isinstance(entry, list):
        return {"status": "ignored", "reason": "Empty entry list"}

    changes = entry[0].get("changes", [])
    if not changes or not isinstance(changes, list):
        return {"status": "ignored", "reason": "Empty changes list"}

    value = changes[0].get("value", {})
    messages = value.get("messages", [])
    phone_number_id = (value.get("metadata") or {}).get("phone_number_id")

    # Delivery status callback rather than a customer message
    if not messages:
        statuses = value.get("statuses", [])
        if statuses:
            _apply_status_updates(statuses)
            return {"status": "ok", "type": "status_callback"}
        return {"status": "ignored", "reason": "No messages in payload"}

    msg = messages[0]
    message_id = msg.get("id")
    raw_phone = msg.get("from")
    msg_type = msg.get("type")

    if not message_id or not raw_phone:
        return {"status": "ignored", "reason": "Missing message_id or phone"}

    incoming_text = (_extract_text(msg) or "").strip()
    if not incoming_text:
        logger.info(f"[Webhook] Ignored unsupported or empty message type: {msg_type}")
        return {"status": "ignored", "reason": f"Unsupported message type: {msg_type}"}

    # 2. Idempotency Check (Deduplication)
    is_new = outbound_db_service.check_and_set_dedup(message_id)
    if not is_new:
        logger.info(f"[Webhook] Duplicate message ignored: {message_id}")
        return {"status": "ok", "reason": "Duplicate message"}

    # 3. Phone number normalization
    normalized_phone = whatsapp_sender.normalize_phone(raw_phone)
    if not normalized_phone:
        logger.warning(f"[Webhook] Failed to normalize phone number: {raw_phone}")
        return {"status": "ignored", "reason": "Phone normalization failed"}

    # 4. Lookup OutboundLead in DynamoDB using phone index GSI1
    logger.info(f"[Webhook] Incoming {msg_type} message from {normalized_phone} (number id {phone_number_id})")
    table = get_dynamodb_table()

    res = table.query(
        IndexName="GSI1",
        KeyConditionExpression="GSI1PK = :pk",
        ExpressionAttributeValues={":pk": f"OB_PHONE#{normalized_phone}"}
    )
    items = res.get("Items", [])

    if not items:
        # Ignore the message if the user is not part of any outbound campaigns
        logger.info(f"[Webhook] No active lead match found for {normalized_phone}. Ignoring message.")
        return {"status": "ignored", "reason": "No campaign lead associated"}

    lead_item = _pick_lead(items, phone_number_id)
    tenant_id = lead_item["tenant_id"]
    campaign_id = lead_item["campaign_id"]
    lead_id = lead_item["lead_id"]

    # Real reply tracking: count each lead's first reply once per campaign.
    try:
        from app.services.outbound.outbound_lead_service import outbound_lead_service
        from app.services.outbound.campaign_service import campaign_service
        if outbound_lead_service.mark_replied(campaign_id, lead_id):
            campaign_service.increment_campaign_counter(campaign_id, "response_count")
    except Exception as e:
        logger.warning(f"[Webhook] Could not record reply for lead {lead_id}: {e}")

    # 5. Retrieve or create linked Conversation ID
    existing_conv = outbound_db_service.get_conversation_by_lead(lead_id)
    if existing_conv:
        conversation_id = existing_conv.conversation_id
    else:
        conversation_id = str(uuid.uuid4())

    # 6. Hand the AI reply to the SQS worker (300 s limit) so the webhook answers Meta
    # immediately; run in-process only when no queue is configured or SQS fails.
    from app.services.outbound import outbound_queue

    job = {
        "tenant_id": tenant_id,
        "campaign_id": campaign_id,
        "lead_id": lead_id,
        "conversation_id": conversation_id,
        "sender_phone": normalized_phone,
        "incoming_text": incoming_text,
        "message_id": message_id
    }
    if outbound_queue.enqueue(outbound_queue.MSG_WHATSAPP_REPLY, job):
        return {"status": "ok", "enqueued": True, "conversation_id": conversation_id}

    # Local development / SQS fallback: Execute orchestrator in background task
    logger.info(f"[Webhook] Executing conversation pipeline via background task: {conversation_id}")
    background_tasks.add_task(
        outbound_conversation_orchestrator.handle_customer_message,
        tenant_id=tenant_id,
        campaign_id=campaign_id,
        lead_id=lead_id,
        conversation_id=conversation_id,
        sender_phone=normalized_phone,
        incoming_text=incoming_text,
        message_id=message_id
    )

    return {"status": "ok", "enqueued": False, "conversation_id": conversation_id}
