import asyncio
import json
import os

from typing import Any, Dict, List

from app.services.ai.rag_service import rag_service
from app.services.storage.storage_service import storage_service
from app.core.logging_config import setup_logging, get_logger

# Initialize logging for Lambda environment
setup_logging()
logger = get_logger(__name__)

MSG_CAMPAIGN_BATCH = "campaign_batch"
MSG_WHATSAPP_REPLY = "whatsapp_reply"
REPLY_FIELDS = ("tenant_id", "campaign_id", "lead_id", "conversation_id", "sender_phone", "incoming_text", "message_id")


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """AWS Lambda handler for SQS messages.

    Job types (by the "type" field of the message body):
    - campaign_batch   -> send the next batch of an outbound campaign
    - whatsapp_reply   -> run the WhatsApp AI conversation for a customer reply
    - (no type)        -> document ingestion (original behaviour, unchanged)

    Outbound job failures are reported per message (batchItemFailures) so one bad
    message never makes SQS redeliver others (which could resend WhatsApp messages).
    Document ingestion keeps raising on failure, as before.
    """
    failures: List[Dict[str, str]] = []

    for record in event.get("Records", []):
        message_id = record.get("messageId", "")
        try:
            body = json.loads(record["body"])
        except Exception as e:
            logger.error(f"Unparseable SQS message {message_id}: {e}")
            continue  # retrying a malformed message would never succeed

        job_type = body.get("type")
        if job_type is None and all(body.get(k) for k in ("conversation_id", "incoming_text", "message_id")):
            job_type = MSG_WHATSAPP_REPLY  # payloads queued before the "type" field existed

        if job_type in (MSG_CAMPAIGN_BATCH, MSG_WHATSAPP_REPLY):
            try:
                asyncio.run(_run_outbound_job(job_type, body))
            except Exception as e:
                logger.error(f"Outbound job {job_type} failed ({message_id}): {e}", exc_info=True)
                failures.append({"itemIdentifier": message_id})
            continue

        _process_document(body)

    return {"batchItemFailures": failures}


async def _run_outbound_job(job_type: str, body: Dict[str, Any]) -> None:
    if job_type == MSG_CAMPAIGN_BATCH:
        from app.services.outbound.campaign_executor_service import campaign_executor_service
        campaign_id = body.get("campaign_id")
        if not campaign_id:
            logger.error("campaign_batch job without campaign_id")
            return
        logger.info(f"📣 Campaign batch job for {campaign_id}")
        await campaign_executor_service.run_batch_job(campaign_id)
        return

    from app.services.ai.outbound.conversation_orchestrator import outbound_conversation_orchestrator
    missing = [k for k in REPLY_FIELDS if not body.get(k)]
    if missing:
        logger.error(f"whatsapp_reply job missing fields: {missing}")
        return
    logger.info(f"📨 WhatsApp reply job for conversation {body['conversation_id']}")
    await outbound_conversation_orchestrator.handle_customer_message(**{k: body[k] for k in REPLY_FIELDS})


def _process_document(body: Dict[str, Any]) -> None:
    """Document ingestion (Chunk -> Embed -> Upsert). Raises on failure to trigger SQS retry."""
    try:
        tenant_id = body.get("tenant_id")
        s3_key = body.get("s3_key")
        original_filename = body.get("original_filename")
        document_type = body.get("document_type", "pdf")

        if not tenant_id or not s3_key:
            logger.error("Missing tenant_id or s3_key in message")
            return

        logger.info(f"Processing document: Key={s3_key}, Tenant={tenant_id}")

        # Download file to temp directory (Lambda /tmp or local temp)
        import tempfile
        local_filename = os.path.basename(s3_key)
        download_path = os.path.join(tempfile.gettempdir(), local_filename)

        storage_service.download_file(s3_key, download_path)

        # rag_service.ingest_document handles idempotency (delete before insert)
        rag_service.ingest_document(
            file_path=download_path,
            tenant_id=tenant_id,
            source=original_filename or local_filename,
            document_type=document_type,
        )

        logger.info(f"Successfully processed {s3_key}")

        # Trigger question bank warm-up for S3 Max tenants
        try:
            from app.services.chat.question_bank_service import warm_up_question_bank
            warmed = warm_up_question_bank(tenant_id)
            if warmed:
                logger.info(f"Question bank warm-up completed for tenant {tenant_id}")
        except Exception as wb_err:
            logger.warning(f"Question bank warm-up failed (non-critical): {wb_err}")

        # Cleanup
        if os.path.exists(download_path):
            os.remove(download_path)

    except Exception as e:
        logger.error(f"Failed to process record: {e}")
        # Raising exception triggers SQS retry logic
        raise e
