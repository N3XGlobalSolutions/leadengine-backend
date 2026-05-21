import sys
import os
import time
import boto3
from dotenv import load_dotenv

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from app.workers.worker import handler
from app.core.logging_config import setup_logging, get_logger

from app.core.config import get_settings

load_dotenv()
setup_logging()
logger = get_logger("worker_service")

def run_worker():
    """
    Polls SQS queue and simulates Lambda trigger by invoking handler().
    This allows 'Real' processing of async tasks in a local environment.
    """
    settings = get_settings()
    SQS_URL = settings.EFFECTIVE_SQS_URL
    REGION = settings.AWS_REGION

    if not SQS_URL:
        logger.error("AWS_SQS_QUEUE_URL is not set.")
        return

    sqs = boto3.client(
        "sqs",
        region_name=REGION,
        # IAM role or default credentials auto-detected
    )
    logger.info(f"🚀 Worker Service started. Polling: {SQS_URL}")

    while True:
        try:
            # Long polling (20 seconds)
            response = sqs.receive_message(
                QueueUrl=SQS_URL, MaxNumberOfMessages=1, WaitTimeSeconds=20
            )

            if "Messages" in response:
                for msg in response["Messages"]:
                    logger.info(f"📨 Message received: {msg['MessageId']}")

                    # Construct Lambda-like event
                    event = {"Records": [{"body": msg["Body"]}]}

                    # Invoke the actual worker handler
                    try:
                        handler(event, None)

                        # Only delete if successful
                        sqs.delete_message(
                            QueueUrl=SQS_URL, ReceiptHandle=msg["ReceiptHandle"]
                        )
                        logger.info("✅ Message processed and deleted.")
                    except Exception as e:
                        logger.error(f"❌ Processing failed: {e}")
                        # Do not delete; let VisibilityTimeout handle retry

            else:
                # No messages, continue loop
                pass

        except KeyboardInterrupt:
            logger.info("🛑 Worker stopped by user.")
            break
        except Exception as e:
            logger.error(f"⚠️ Polling error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    run_worker()
