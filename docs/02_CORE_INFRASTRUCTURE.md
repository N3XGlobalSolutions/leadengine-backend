# Core Infrastructure

> **Core system components - Configuration, Database, and Logging**  
> Files: `app/core/config.py`, `app/core/database.py`, `app/core/async_database.py`, `app/core/logging_config.py`

---

## Table of Contents

1. [config.py - Application Settings](#configpy)
2. [database.py - Database Management](#databasepy)
3. [logging_config.py - Structured Logging](#logging_configpy)

---

## config.py

**File**: `app/core/config.py` (76 lines)

### Purpose

Centralized application configuration using **Pydantic Settings**. All configuration is loaded from environment variables with sensible defaults.

### Why This Approach?

1. **Type Safety**: Pydantic validates all config values at startup
2. **Environment-Based**: Different configs for dev/staging/prod
3. **Single Source of Truth**: All settings in one place
4. **Auto Documentation**: Settings are self-documenting with type hints

### Implementation

```python
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """Application settings loaded from environment."""
    
    model_config = SettingsConfigDict(
        env_file=".env",              # Load from .env file
        env_file_encoding="utf-8",     
        extra="ignore",                # Ignore extra env vars
    )
    
    # All settings defined with types and defaults
    APP_NAME: str = "AOB Bot RAG Backend"
    DEBUG: bool = True
    USE_DYNAMODB: bool = True
    # ... more settings
    
@lru_cache()  # Singleton pattern
def get_settings():
    return Settings()
```

### Complete Configuration Reference

#### Application Settings
```python
APP_NAME: str = "AOB Bot RAG Backend"
DEBUG: bool = True  # Set False in production
```

**Usage**: Branding, feature toggles

---

#### Database Settings (DynamoDB)
```python
USE_DYNAMODB: bool = True
AWS_REGION: str = "us-east-1"
DYNAMODB_TABLE_PREFIX: str = "aob_"  # Optional prefix
```

**Why**: 
- `USE_DYNAMODB`: Hardcoded to True in current version
- `AWS_REGION`: Required for DynamoDB connection
- `DYNAMODB_TABLE_PREFIX`: For namespacing tables (e.g. `aob_users`)

---

#### Qdrant Settings
```python
Qdrant_URL: str | None = None  # Remote: http://ec2-ip:6333
QDRANT_API_KEY: str | None = None  # Optional auth
Qdrant_PATH: str = "data/qdrant_data"  # Local dev path
QDRANT_COLLECTION_NAME: str = "aob_documents"
```

**How It Works**:
- If `Qdrant_URL` is set → Connect to remote Qdrant (production)
- If `Qdrant_URL` is None → Use local file-based Qdrant (development)
- `QDRANT_API_KEY` → Optional authentication for remote

**Why Two Modes?**:
- **Local**: Fast development without external dependencies
- **Remote**: Production scalability with dedicated Qdrant server

**Example**:
```bash
# Development (local)
# Qdrant_URL not set → uses Qdrant_PATH

# Production (EC2)
Qdrant_URL=http://10.0.1.50:6333
QDRANT_API_KEY=your-secret-key
```

---

#### AWS Bedrock Settings
```python
AWS_REGION: str = "us-east-1"
AWS_BEARER_TOKEN_BEDROCK: str | None = None
BEDROCK_LLM_MODEL_ID: str = "openai.gpt-oss-120b-1:0"
BEDROCK_EMBEDDING_MODEL_ID: str = "amazon.titan-embed-text-v2:0"
BEDROCK_EMBEDDING_DIMENSIONS: int = 1024
```

**Why Each Setting?**:
- `AWS_REGION`: Bedrock availability varies by region
- `AWS_BEARER_TOKEN_BEDROCK`: Authentication for Bedrock API
- `BEDROCK_LLM_MODEL_ID`: The text generation model
- `BEDROCK_EMBEDDING_MODEL_ID`: Converts text to vectors
- `BEDROCK_EMBEDDING_DIMENSIONS`: Titan v2 = 1024 dims

**Model Choice Rationale**:
- **GPT-OSS 120B**: High-quality responses, good context window
- **Titan Embed v2**: Fast, cost-effective, 1024 dims (good balance)

---

#### AWS S3 & SQS Settings
```python
AWS_S3_BUCKET_NAME: str | None = None
AWS_SQS_QUEUE_URL: str | None = None
USE_ASYNC_INGESTION: bool = False
```

**Purpose**: Async document ingestion pipeline

**Flow**:
1. Upload large file to S3
2. Send SQS message with file location
3. Background worker processes document
4. Worker ingests into Qdrant

**Why Async?**: Large PDFs (100+ pages) take time to process. Don't block user request.

---

#### JWT Settings
```python
JWT_SECRET_KEY: str  # NO DEFAULT - must be set!
JWT_ALGORITHM: str = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
REFRESH_TOKEN_EXPIRE_DAYS: int = 7
```

**Critical**: `JWT_SECRET_KEY` has **no default** - app will fail to start without it

**Why?**:
- Forces explicit security configuration
- Prevents accidental use of default secrets in production

**How to Generate**:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
# ZrX3Q7TvPkMw...
```

**Token Lifetimes Explained**:
- **Access Token (30min)**: Short-lived for security
- **Refresh Token (7 days)**: Long-lived for convenience
- User refreshes access token without re-login

---

#### CORS Settings
```python
CORS_ORIGINS_STR: str = "http://localhost:3000,http://localhost:8000"

@computed_field
@property
def CORS_ORIGINS(self) -> list[str]:
    """Parse CORS origins from comma-separated string."""
    return [origin.strip() for origin in self.CORS_ORIGINS_STR.split(",") if origin.strip()]
```

**Why Computed Field?**:
- Environment variables are strings
- App needs a list for FastAPI CORS middleware
- `@computed_field` auto-converts on access

**Usage**:
```python
settings = get_settings()
settings.CORS_ORIGINS  # ['http://localhost:3000', 'http://localhost:8000']
```

**Production Example**:
```bash
CORS_ORIGINS_STR=https://app.aobot.com,https://widget.aobot.com
```

---

#### Logging Settings
```python
LOG_LEVEL: str = "INFO"  # DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_FORMAT: str = "json"  # "json" or "text"
```

**Why JSON Logging?**:
- Easy parsing by log aggregators (Datadog, ELK)
- Structured data for analytics
- Queryable fields (request_id, user_id, etc.)

---

#### Rate Limiting Settings
```python
RATE_LIMIT_DEFAULT: str = "100/minute"  # General API
RATE_LIMIT_CHAT: str = "10/minute"      # Chat endpoints
```

**Why Different Limits?**:
- **General API**: More permissive for dashboard/admin
- **Chat**: Stricter to prevent abuse (LLM calls are expensive)

**Format**: `<count>/<period>` where period = minute/hour/day

---

### How It's Used Throughout the App

**Pattern Everywhere**:
```python
from app.core.config import get_settings

settings = get_settings()  # Cached singleton
region = settings.AWS_REGION
```

**Used In**:
- `app/core/database.py` → Qdrant connection
- `app/services/ai/llm_service.py` → Bedrock config
- `app/main.py` → CORS, app name
- `app/middleware/rate_limiter.py` → Rate limits
- Every service that needs config

---

### Singleton Pattern with `@lru_cache()`

```python
@lru_cache()
def get_settings():
    return Settings()
```

**Why?**:
1. Settings loaded exactly **once** at startup
2. Subsequent calls return cached instance
3. No repeated environment variable parsing
4. Thread-safe

**Without caching**:
```python
# BAD: Parses .env 1000 times!
for i in range(1000):
    settings = Settings()  
```

**With caching**:
```python
# GOOD: Parses .env once, returns cache 999 times
for i in range(1000):
    settings = get_settings()
```

---

### Environment File (.env)

**Example `.env` file**:
```bash
# Application
APP_NAME=AOB Bot
DEBUG=false

# Database
USE_DYNAMODB=True
AWS_REGION=us-east-1

# Qdrant
Qdrant_URL=http://ec2-qdrant:6333
QDRANT_COLLECTION_NAME=aob_documents

# AWS Bedrock
AWS_BEARER_TOKEN_BEDROCK=your-token-here
BEDROCK_LLM_MODEL_ID=openai.gpt-oss-120b-1:0

# Security (CHANGE THIS!)
JWT_SECRET_KEY=your-super-secret-key-min-32-chars-required

# CORS
CORS_ORIGINS_STR=https://app.example.com,https://widget.example.com

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json

# Rate Limiting
RATE_LIMIT_DEFAULT=100/minute
RATE_LIMIT_CHAT=10/minute
```

---

## database.py

**File**: `app/core/database.py` (127 lines)

### Purpose

Manages database connections. For **Qdrant**, it uses the singleton pattern. For **DynamoDB**, `boto3` handles connection management internally, and the module provides helpers to access the main table.

### Complete Implementation Breakdown

#### DynamoDB Connection

DynamoDB uses `boto3` which manages connections and credentials automatically via AWS standard chain (Environment variables, ~/.aws/credentials, or IAM Roles).

```python
import boto3

def get_dynamodb_resource():
    """Get DynamoDB resource (singleton logic handled by boto3)."""
    return boto3.resource("dynamodb", region_name=settings.AWS_REGION)

def get_dynamodb_table(table_name="aob_single_table"):
    """Get the main single-table."""
    dynamodb = get_dynamodb_resource()
    return dynamodb.Table(table_name)
```

**Thread Safety**: Boto3 resources are not thread-safe, but `boto3.client` is. For high concurrency, we creates new resources/clients per thread or Use `aioboto3` for async contexts.

---

#### Qdrant Connection

```python
@classmethod
def get_qdrant_client(cls):
    """Get Qdrant client (singleton).
    
    Supports:
    - Remote Qdrant (URL + optional API key)
    - Local Qdrant (path-based storage)
    """
    if cls._qdrant_client is None:
        if settings.Qdrant_URL:
            # Remote Qdrant
            api_key = getattr(settings, "QDRANT_API_KEY", None)
            cls._qdrant_client = QdrantClient(
                url=settings.Qdrant_URL,
                api_key=api_key,
                timeout=30,  # Important for cold starts
            )
        else:
            # Local Qdrant
            cls._qdrant_client = QdrantClient(path=settings.Qdrant_PATH)
    return cls._qdrant_client
```

**Dual Mode Explanation**:

**Mode 1: Remote Qdrant (Production)**
```bash
# .env
Qdrant_URL=http://10.0.1.50:6333
QDRANT_API_KEY=secret123
```

Flow:
1. Checks `settings.Qdrant_URL` → has value
2. Creates `QdrantClient(url=..., api_key=...)`
3. Connects to remote Qdrant server
4. `timeout=30` → waits up to 30s (handles Lambda cold starts)

**Mode 2: Local Qdrant (Development)**
```bash
# .env
# Qdrant_URL not set (or empty)
Qdrant_PATH=data/qdrant_data
```

Flow:
1. Checks `settings.Qdrant_URL` → None
2. Creates `QdrantClient(path=...)`
3. Uses local file-based storage
4. Data persists in `data/qdrant_data/` folder

**Why 30 Second Timeout?**:
- AWS Lambda cold starts can take 10-20 seconds
- Default timeout (5s) causes failures
- 30s is safe margin without blocking too long

---

### Helper Functions

```python
def get_dynamodb_table():
    """Get the main DynamoDB table."""
    return Database.get_dynamodb_table()

def get_qdrant_client():
    """Get Qdrant client instance."""
    return Database.get_qdrant_client()
```

**Why wrapper functions?**:
- **Cleaner imports**: `from app.core.database import get_dynamodb_table`
- **vs**: `from app.core.database import Database; db = Database.get_db()`
- **Testability**: Easier to mock functions than class methods

---

## logging_config.py

**File**: `app/core/logging_config.py`

### Purpose

Structured JSON logging with automatic context injection (request ID, user ID).

### Why JSON Logging?

**Traditional Logging (Text)**:
```
2025-12-10 10:30:15 INFO User logged in
```

**Problems**:
- Hard to parse programmatically
- No structured data
- Can't easily filter by user/request

**JSON Logging**:
```json
{
  "timestamp": "2025-12-10T10:30:15.123Z",
  "level": "INFO",
  "message": "User logged in",
  "request_id": "req_abc123",
  "user_id": "user_456",
  "location": "/app/services/auth.py:45"
}
```

**Benefits**:
- **Queryable**: Find all logs for request_id="req_abc123"
- **Aggregatable**: Count errors by user_id
- **Parseable**: Direct JSON import to analytics tools

### Implementation

```python
import json
import logging
from datetime import datetime

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "location": f"{record.pathname}:{record.lineno}",
            "function": record.funcName,
        }
        
        # Add context if available
        if hasattr(record, "request_id"):
            log_data["request_id"] = record.request_id
        if hasattr(record, "user_id"):
            log_data["user_id"] = record.user_id
        
        return json.dumps(log_data)

def setup_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logging.root.addHandler(handler)
    logging.root.setLevel(settings.LOG_LEVEL)
```
