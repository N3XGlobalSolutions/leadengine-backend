import json
import os

from typing import Any, Dict

from app.services.ai.rag_service import rag_service
from app.services.storage.storage_service import storage_service
from app.core.logging_config import setup_logging, get_logger

# Initialize logging for Lambda environment
setup_logging()
logger = get_logger(__name__)


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda handler for processing SQS messages.
    Triggered by SQS event containing file upload details.
    """
    logger.info(f"Received event: {json.dumps(event)}")

    for record in event.get("Records", []):
        try:
            # Parse SQS message body
            body = json.loads(record["body"])

            tenant_id = body.get("tenant_id")
            s3_key = body.get("s3_key")
            original_filename = body.get("original_filename")
            document_type = body.get("document_type", "pdf")

            if not tenant_id or not s3_key:
                logger.error("Missing tenant_id or s3_key in message")
                continue

            logger.info(f"Processing document: Key={s3_key}, Tenant={tenant_id}")

            # Download file to temp directory (Lambda /tmp or local temp)
            import tempfile
            local_filename = os.path.basename(s3_key)
            download_path = os.path.join(tempfile.gettempdir(), local_filename)

            storage_service.download_file(s3_key, download_path)

            # Ingest document (Chunk -> Embed -> Upsert)
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

    return {"statusCode": 200, "body": json.dumps("Processing complete")}
