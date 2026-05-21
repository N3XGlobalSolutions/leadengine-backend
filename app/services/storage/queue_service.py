"""SQS Queue Service for async document ingestion."""

import json
import boto3
from botocore.exceptions import ClientError
from app.core.config import get_settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class QueueService:
    """Handles SQS messaging for async document processing."""

    def __init__(self):
        settings = get_settings()
        self.queue_url = settings.EFFECTIVE_SQS_URL
        # Use IAM role credentials in Lambda (no explicit credentials)
        self.sqs_client = boto3.client(
            "sqs",
            region_name=settings.AWS_REGION,
            # IAM role credentials auto-detected in Lambda
        )

    def send_message(self, message_body: dict) -> str:
        """Sends a JSON message to SQS."""
        if not self.queue_url:
            raise ValueError("AWS_SQS_QUEUE_URL is not configured")

        try:
            response = self.sqs_client.send_message(
                QueueUrl=self.queue_url, MessageBody=json.dumps(message_body)
            )
            message_id = response.get("MessageId")
            logger.info(f"Message sent to SQS: {message_id}")
            return message_id
        except ClientError as e:
            logger.error(f"SQS send failed: {e}")
            raise


queue_service = QueueService()
