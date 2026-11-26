"""SQS queue for long-running outbound work (campaign batches, WhatsApp AI replies).

The API Lambda has a 30 s limit, so anything that can take longer is put on the
outbound queue and processed by the SQS worker Lambda (300 s limit):

- {"type": "campaign_batch", "campaign_id": ...}  -> send one batch, then queue the next
                                                      batch with DelaySeconds = batch delay
- {"type": "whatsapp_reply", ...}                   -> run the AI conversation for a reply

If OUTBOUND_SQS_QUEUE_URL is not configured (local development) callers fall
back to running the work in-process.
"""

import json
from typing import Any, Dict

import boto3

from app.core.config import get_settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)
settings = get_settings()

SQS_MAX_DELAY_SECONDS = 900

MSG_CAMPAIGN_BATCH = "campaign_batch"
MSG_WHATSAPP_REPLY = "whatsapp_reply"

_client = None


def queue_url() -> str:
    return getattr(settings, "OUTBOUND_SQS_QUEUE_URL", None) or ""


def is_enabled() -> bool:
    return bool(queue_url())


def _sqs():
    global _client
    if _client is None:
        _client = boto3.client("sqs", region_name=settings.AWS_REGION)
    return _client


def enqueue(message_type: str, payload: Dict[str, Any], delay_seconds: int = 0) -> bool:
    """Put a job on the outbound queue. Returns False if no queue is configured or sending failed."""
    url = queue_url()
    if not url:
        return False
    body = {"type": message_type, **payload}
    try:
        _sqs().send_message(
            QueueUrl=url,
            MessageBody=json.dumps(body),
            DelaySeconds=max(0, min(int(delay_seconds), SQS_MAX_DELAY_SECONDS)),
        )
        logger.info(f"[OutboundQueue] Enqueued {message_type} (delay {delay_seconds}s): "
                    f"{payload.get('campaign_id') or payload.get('conversation_id')}")
        return True
    except Exception as e:
        logger.error(f"[OutboundQueue] Failed to enqueue {message_type}: {e}")
        return False
