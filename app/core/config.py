"""Application configuration settings."""

from functools import lru_cache
import os
from dotenv import load_dotenv

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load environment variables from .env file
load_dotenv()

# Map custom AWS var names to standard boto3 names if needed (for local dev)
if os.getenv("AWS_ACCESS_KEY") and not os.getenv("AWS_ACCESS_KEY_ID"):
    os.environ["AWS_ACCESS_KEY_ID"] = os.getenv("AWS_ACCESS_KEY")
if os.getenv("AWS_SECRET_KEY") and not os.getenv("AWS_SECRET_ACCESS_KEY"):
    os.environ["AWS_SECRET_ACCESS_KEY"] = os.getenv("AWS_SECRET_KEY")


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application Settings
    APP_NAME: str = "LeadEngine Bot RAG Backend"
    DEBUG: bool = True

    # Environment: "development" or "production"
    APP_ENV: str = "development"

    #REDIS Settings
    REDIS_URL: str | None = None

    # Qdrant Settings
    Qdrant_URL: str | None = None  # Remote Qdrant URL (e.g., http://ec2-ip:6333)
    QDRANT_API_KEY: str | None = None  # API key for remote Qdrant authentication
    Qdrant_PATH: str = "data/qdrant_data"  # Local path for development
    QDRANT_COLLECTION_NAME: str = "test_leadengine_documents"  # Auto-suffixed by _env_suffix

    # AWS Bedrock - credentials are optional (Lambda uses IAM role)
    AWS_ACCESS_KEY: str | None = None  # Optional - Lambda uses IAM role
    AWS_SECRET_KEY: str | None = None  # Optional - Lambda uses IAM role
    AWS_REGION: str = "ap-south-1"  # Default region

    # Anthropic Claude
    CLAUDE_API_KEY: str | None = None

    BEDROCK_LLM_MODEL_ID: str = "zai.glm-5"

    BEDROCK_EMBEDDING_MODEL_ID: str = "amazon.titan-embed-text-v2:0"
    BEDROCK_EMBEDDING_DIMENSIONS: int = 1024  # Titan V2 uses 1024 dimensions

    # AWS S3 & SQS Settings (For Async Ingestion)
    AWS_S3_BUCKET_NAME: str | None = None
    AWS_SQS_QUEUE_URL: str | None = None
    USE_ASYNC_INGESTION: bool = False  # Toggle for future deployment

    # Outbound Campaign Settings
    TELEGRAM_BOT_TOKEN: str | None = None  # Telegram Bot API token for outbound campaigns
    OUTBOUND_SQS_QUEUE_URL: str | None = None  # Dedicated SQS queue for outbound campaigns
    ENCRYPTION_KEY: str | None = None

    # Feature Flags
    USE_DYNAMODB: bool = False  # Toggle between MongoDB and DynamoDB

    # JWT Settings - No default to force explicit configuration
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 720
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # CORS Settings - can be set via CORS_ORIGINS or CORS_ORIGINS_STR
    CORS_ORIGINS_STR: str = "https://d2g68tfj6dbr9y.cloudfront.net,https://qbotglobal.com,app.qbotglobal.com,http://localhost:3000,http://localhost:8000"

    # Logging Settings
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"  # "json" or "text"

    # Rate Limiting Settings
    RATE_LIMIT_DEFAULT: str = "100/minute"
    RATE_LIMIT_CHAT: str = "10/minute"

    @computed_field
    @property
    def CORS_ORIGINS(self) -> list[str]:
        """Parse CORS origins from comma-separated string.

        Reads from CORS_ORIGINS env var first, falls back to CORS_ORIGINS_STR.
        """
        # Check if CORS_ORIGINS was set directly (from Lambda env var)
        cors_str = os.getenv("CORS_ORIGINS", self.CORS_ORIGINS_STR)

        return [
            origin.strip()
            for origin in cors_str.split(",")
            if origin.strip()
        ]

    @computed_field
    @property
    def _env_suffix(self) -> str:
        """Returns '_dev' for development, '' for production."""
        if self.APP_ENV == "production":
            return ""
        return "_dev"

    @computed_field
    @property
    def DYNAMODB_TABLE_NAME(self) -> str:
        """Auto-suffixed DynamoDB table name based on APP_ENV."""
        # Allow explicit override via env var
        explicit = os.getenv("DYNAMODB_TABLE_NAME")
        if explicit and self.APP_ENV == "production":
            return explicit
        return f"test_leadengine"

    @computed_field
    @property
    def EFFECTIVE_QDRANT_COLLECTION(self) -> str:
        """Auto-suffixed Qdrant collection name based on APP_ENV."""
        return f"test_leadengine_documents"

    @computed_field
    @property
    def EFFECTIVE_S3_BUCKET(self) -> str:
        """Auto-suffixed S3 bucket name based on APP_ENV."""
        base = self.AWS_S3_BUCKET_NAME or "aob-bot-document"
        if self.APP_ENV == "production":
            return base
        return f"test-leadengine-document" #if not base.endswith("-dev") else base

    @computed_field
    @property
    def EFFECTIVE_SQS_URL(self) -> str:
        """Auto-suffixed SQS queue URL based on APP_ENV."""
        base = self.AWS_SQS_QUEUE_URL or ""
        if self.APP_ENV == "production" or not base:
            return base
        # Replace queue name in URL
        if "-dev" not in base:
            return base.replace("aob-bot-ingestion-queue", "test-ingestion-queue-leadengine")
        return base


@lru_cache()
def get_settings():
    """Get cached settings instance."""
    return Settings()
