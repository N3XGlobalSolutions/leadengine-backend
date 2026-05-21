"""S3 Storage Service for document uploads."""

import boto3
from typing import BinaryIO
from botocore.exceptions import ClientError
from app.core.config import get_settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class StorageService:
    """Handles S3 file uploads for async document processing."""

    def __init__(self):
        settings = get_settings()
        self.bucket_name = settings.EFFECTIVE_S3_BUCKET
        # Use IAM role credentials in Lambda (no explicit credentials)
        self.s3_client = boto3.client(
            "s3",
            region_name=settings.AWS_REGION,
            # IAM role credentials auto-detected in Lambda
        )

    def upload_file(
        self, file_obj: BinaryIO, key: str, content_type: str = "application/pdf"
    ) -> str:
        """Uploads a file-like object to S3."""
        if not self.bucket_name:
            raise ValueError("AWS_S3_BUCKET_NAME is not configured")

        try:
            self.s3_client.upload_fileobj(
                file_obj, self.bucket_name, key, ExtraArgs={"ContentType": content_type}
            )
            logger.info(f"File uploaded to s3://{self.bucket_name}/{key}")
            return key
        except ClientError as e:
            logger.error(f"S3 upload failed: {e}")
            raise

    def download_file(self, key: str, download_path: str) -> None:
        """Downloads a file from S3 to a local path."""
        if not self.bucket_name:
            raise ValueError("AWS_S3_BUCKET_NAME is not configured")

        try:
            self.s3_client.download_file(self.bucket_name, key, download_path)
            logger.info(
                f"File downloaded from s3://{self.bucket_name}/{key} to {download_path}"
            )
        except ClientError as e:
            logger.error(f"S3 download failed: {e}")
            raise

    def generate_presigned_url(self, key: str, expiration: int = 3600) -> str:
        """Generates a presigned URL for a file."""
        if not self.bucket_name:
            return ""

        try:
            url = self.s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket_name, "Key": key},
                ExpiresIn=expiration,
            )
            return url
        except ClientError as e:
            logger.error(f"Failed to generate presigned URL: {e}")
            return ""

    def delete_file(self, key: str) -> bool:
        """Deletes a file from S3."""
        if not self.bucket_name:
            return False

        try:
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=key)
            logger.info(f"File deleted from s3://{self.bucket_name}/{key}")
            return True
        except ClientError as e:
            logger.error(f"S3 delete failed: {e}")
            return False

    def list_files(self, prefix: str) -> list[str]:
        """Lists files in the bucket with a given prefix."""
        if not self.bucket_name:
            return []

        try:
            paginator = self.s3_client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=self.bucket_name, Prefix=prefix)
            
            files = []
            for page in pages:
                if "Contents" in page:
                    for obj in page["Contents"]:
                        files.append(obj["Key"])
            return files
        except ClientError as e:
            logger.error(f"S3 list failed: {e}")
            return []


storage_service = StorageService()
