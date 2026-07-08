"""WhatsApp Webhook Router for incoming customer messages."""

import hashlib
import hmac
import json
import uuid
from typing import Any, Dict, Optional
import boto3
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request, status
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

# Hub Verify Token for the Meta GET handshake — no hardcoded fallback.
VERIFY_TOKEN = settings.WHATSAPP_VERIFY_TOKEN


def _verify_meta_signature(raw_body: bytes, signature_header: Optional[str]) -> bool:
    """Verify the Meta X-Hub-Signature-256 HMAC over the raw request body.

    Returns True only when the signature is present, well-formed, and matches an
    HMAC-SHA256 of the body keyed by WHATSAPP_APP_SECRET. If no app secret is
    configured we fail closed (reject) rather than trusting unsigned payloads.
    """
    app_secret = settings.WHATSAPP_APP_SECRET
    if not app_secret:
        logger.error("[Webhook] WHATSAPP_APP_SECRET not configured; rejecting inbound webhook.")
        return False
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        app_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    provided = signature_header.split("=", 1)[1]
    return hmac.compare_digest(expected, provided)


# ── 1. HUB VERIFICATION (GET) ─────────────────────────────────────────

@router.get("/webhook", response_class=PlainTextResponse)
async def verify_webhook(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
):
    """Facebook Webhook verification endpoint."""
    if not VERIFY_TOKEN:
        logger.error("[Webhook] WHATSAPP_VERIFY_TOKEN not configured.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook not configured",
        )
    if hub_mode == "subscribe" and hub_verify_token and hmac.compare_digest(
        hub_verify_token, VERIFY_TOKEN
    ):
        logger.info("[Webhook] Webhook verified successfully by Meta.")
        return hub_challenge

    logger.warning("[Webhook] Failed verify handshake.")
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Verification token mismatch"
    )


# ── 2. WEBHOOK INBOUND DISPATCH (POST) ────────────────────────────────

@router.post("/webhook")
async def process_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: Optional[str] = Header(None, alias="X-Hub-Signature-256"),
):
    """WhatsApp Incoming replies webhook handler."""
    # 0. Authenticity: verify Meta's HMAC signature over the RAW body before
    # trusting anything in the payload (prevents forged inbound messages).
    raw_body = await request.body()
    if not _verify_meta_signature(raw_body, x_hub_signature_256):
        logger.warning("[Webhook] Rejected inbound webhook: invalid or missing signature.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    try:
        payload = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    logger.debug("[Webhook] Inbound payload received")

    # 1. Parse payload fields
    entry = payload.get("entry", [])
    if not entry or not isinstance(entry, list):
        return {"status": "ignored", "reason": "Empty entry list"}

    changes = entry[0].get("changes", [])
    if not changes or not isinstance(changes, list):
        return {"status": "ignored", "reason": "Empty changes list"}

    value = changes[0].get("value", {})
    messages = value.get("messages", [])
    
    # Check if this is a message delivery status callback rather than a customer reply
    if not messages:
        # Check if statuses update callback (contains status changes for sent messages)
        statuses = value.get("statuses", [])
        if statuses:
            logger.debug(f"[Webhook] Status callback received: {statuses}")
            # Real status updates can sync to campaign logs if needed
            return {"status": "ok", "type": "status_callback"}
        return {"status": "ignored", "reason": "No messages in payload"}

    msg = messages[0]
    message_id = msg.get("id")
    raw_phone = msg.get("from")
    msg_type = msg.get("type")

    if not message_id or not raw_phone:
        return {"status": "ignored", "reason": "Missing message_id or phone"}

    # We only process text replies for agent sales conversations
    if msg_type != "text":
        logger.info(f"[Webhook] Ignored non-text message type: {msg_type}")
        return {"status": "ignored", "reason": "Non-text message type"}

    incoming_text = msg.get("text", {}).get("body", "").strip()
    if not incoming_text:
        return {"status": "ignored", "reason": "Empty text body"}

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
    logger.info(f"[Webhook] Looking up active lead for: {normalized_phone}")
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

    # Disambiguate when the same phone participates in multiple tenants' campaigns.
    # Prefer the lead owned by the tenant whose WhatsApp business number received
    # this message (metadata.phone_number_id); otherwise pick the most recently
    # updated lead deterministically and log the ambiguity for investigation.
    recipient_phone_id = value.get("metadata", {}).get("phone_number_id")
    distinct_tenants = {i.get("tenant_id") for i in items}
    if len(distinct_tenants) > 1:
        logger.warning(
            "[Webhook] Phone maps to multiple tenants; disambiguating",
            extra={"extra_data": {
                "tenant_count": len(distinct_tenants),
                "recipient_phone_id": recipient_phone_id,
            }},
        )
        scoped = [i for i in items if i.get("phone_number_id") == recipient_phone_id] if recipient_phone_id else []
        candidates = scoped or items
    else:
        candidates = items

    lead_item = max(candidates, key=lambda i: i.get("updated_at") or i.get("created_at") or "")
    tenant_id = lead_item["tenant_id"]
    campaign_id = lead_item["campaign_id"]
    lead_id = lead_item["lead_id"]

    # 5. Retrieve or create linked Conversation ID
    # Check if lead already has a mapped conversation
    existing_conv = outbound_db_service.get_conversation_by_lead(lead_id)
    if existing_conv:
        conversation_id = existing_conv.conversation_id
    else:
        conversation_id = str(uuid.uuid4())

    # 6. Push to SQS Queue if URL is configured (Production Scale), or run locally via BackgroundTasks
    sqs_url = getattr(settings, "OUTBOUND_SQS_QUEUE_URL", None)
    
    sqs_payload = {
        "tenant_id": tenant_id,
        "campaign_id": campaign_id,
        "lead_id": lead_id,
        "conversation_id": conversation_id,
        "sender_phone": normalized_phone,
        "incoming_text": incoming_text,
        "message_id": message_id
    }

    if sqs_url:
        try:
            logger.info(f"[Webhook] Enqueuing SQS message to Outbound Queue: {sqs_url}")
            sqs_client = boto3.client("sqs", region_name=settings.AWS_REGION)
            sqs_client.send_message(
                QueueUrl=sqs_url,
                MessageBody=json.dumps(sqs_payload),
                DelaySeconds=0
            )
            return {"status": "ok", "enqueued": True, "conversation_id": conversation_id}
        except Exception as e:
            logger.error(f"[Webhook] Failed to push to SQS: {e}. Falling back to background task.")

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
