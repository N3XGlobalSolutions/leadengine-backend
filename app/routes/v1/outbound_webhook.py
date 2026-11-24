"""WhatsApp Webhook Router for incoming customer messages."""

import json
import os
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

# Hub Verify Token for Facebook Setup
VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "leadengine_verify_token")


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
    
    logger.warning(f"[Webhook] Failed verify handshake. Token: {hub_verify_token}")
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
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    logger.debug(f"[Webhook] Inbound payload: {json.dumps(payload)}")

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

    # Match the most recent lead by creation timestamp
    items.sort(key=lambda x: x.get("created_at", ""))
    lead_item = items[-1]
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
