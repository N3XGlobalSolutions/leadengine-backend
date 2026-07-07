"""Background SQS poller worker for Outbound AI Sales Agent conversations."""

import json
import time
import boto3
from botocore.exceptions import ClientError
from app.core.config import get_settings
from app.core.logging_config import setup_logging, get_logger
from app.services.ai.outbound.conversation_orchestrator import outbound_conversation_orchestrator

setup_logging()
logger = get_logger("outbound_worker")
settings = get_settings()


def process_sqs_message(body_str: str) -> bool:
    """Parse SQS payload and execute orchestrator handle message."""
    try:
        body = json.loads(body_str)
        
        tenant_id = body.get("tenant_id")
        campaign_id = body.get("campaign_id")
        lead_id = body.get("lead_id")
        conversation_id = body.get("conversation_id")
        sender_phone = body.get("sender_phone")
        incoming_text = body.get("incoming_text")
        message_id = body.get("message_id")

        if not all([tenant_id, campaign_id, lead_id, conversation_id, sender_phone, incoming_text, message_id]):
            logger.error("Missing required parameters in enqueued SQS message payload.")
            return False

        logger.info(f"📨 Processing SQS message for Conv: {conversation_id} (Lead: {lead_id})")
        
        # Run orchestrator synchronously inside worker thread
        # Orchestrator handles locking and runs the async pipeline
        import asyncio
        loop = asyncio.get_event_loop()
        
        if loop.is_running():
            # Run in worker loop
            future = asyncio.run_coroutine_threadsafe(
                outbound_conversation_orchestrator.handle_customer_message(
                    tenant_id=tenant_id,
                    campaign_id=campaign_id,
                    lead_id=lead_id,
                    conversation_id=conversation_id,
                    sender_phone=sender_phone,
                    incoming_text=incoming_text,
                    message_id=message_id
                ),
                loop
            )
            success = future.result()
        else:
            success = asyncio.run(
                outbound_conversation_orchestrator.handle_customer_message(
                    tenant_id=tenant_id,
                    campaign_id=campaign_id,
                    lead_id=lead_id,
                    conversation_id=conversation_id,
                    sender_phone=sender_phone,
                    incoming_text=incoming_text,
                    message_id=message_id
                )
            )
            
        return success

    except Exception as e:
        logger.error(f"Failed to process enqueued record: {e}", exc_info=True)
        return False


def run_outbound_worker():
    """Poll SQS queue continuously and trigger message processing."""
    SQS_URL = getattr(settings, "OUTBOUND_SQS_QUEUE_URL", None)
    REGION = settings.AWS_REGION

    if not SQS_URL:
        logger.warning("[Worker] OUTBOUND_SQS_QUEUE_URL is not set. Worker thread skipped.")
        return

    sqs = boto3.client("sqs", region_name=REGION)
    logger.info(f"🚀 Outbound SQS Worker started. Polling: {SQS_URL}")

    while True:
        try:
            # Long poll messages (20 seconds wait to save CPU)
            response = sqs.receive_message(
                QueueUrl=SQS_URL,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=20
            )

            messages = response.get("Messages", [])
            for msg in messages:
                msg_id = msg["MessageId"]
                receipt = msg["ReceiptHandle"]
                body_str = msg["Body"]
                
                logger.info(f"📨 Message dequeued: {msg_id}")
                
                # Execute payload
                success = process_sqs_message(body_str)
                
                if success:
                    # Delete message on completion
                    sqs.delete_message(QueueUrl=SQS_URL, ReceiptHandle=receipt)
                    logger.info(f"✅ Message {msg_id} processed and deleted from queue.")
                else:
                    logger.warning(f"❌ Processing failed for message {msg_id}. Leaving in queue for retry.")

        except KeyboardInterrupt:
            logger.info("🛑 Outbound Worker stopped by keyboard interrupt.")
            break
        except Exception as e:
            logger.error(f"⚠️ Polling error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    run_outbound_worker()
