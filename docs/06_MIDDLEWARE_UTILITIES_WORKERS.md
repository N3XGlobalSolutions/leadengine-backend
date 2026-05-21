# Middleware, Utilities & Workers

> **Supporting components - Middleware, Utilities, Workers, and Deployment**  
> Files: `app/middleware/*.py`, `app/utils/*.py`, `app/workers/*.py`

---

## Table of Contents

1. [Middleware](#middleware)
2. [Utilities](#utilities)
3. [Workers](#workers)
4. [Deployment Guide](#deployment-guide)

---

## Middleware

Location: `app/middleware/`

### exception_handlers.py

**Purpose**: Centralized error handling for all API endpoints

**Exception Handlers**:

| Exception | HTTP Code | Response |
|-----------|-----------|----------|
| `AuthenticationError` | 401 | Unauthorized |
| `PermissionError` | 403 | Forbidden |
| `NotFoundError` | 404 | Not Found |
| `ValidationError` | 422 | Unprocessable Entity |
| `RateLimitExceeded` | 429 | Too Many Requests |
| `Exception` | 500 | Internal Server Error |

**Error Response Format**:
```json
{
  "detail": "Error message",
  "status_code": 401,
  "request_id": "req_abc123",
  "timestamp": "2025-12-10T00:00:00Z"
}
```

**Features**:
- Request ID tracking
- Structured logging
- User-friendly error messages
- Sensitive data masking

**Setup**:
```python
from app.middleware.exception_handlers import setup_exception_handlers

app = FastAPI()
setup_exception_handlers(app)
```

---

### rate_limiter.py

**Purpose**: API rate limiting using SlowAPI

**Configuration**:
```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(
    key_func=get_remote_address,  # Limit by IP address
    storage_uri="memory://",       # In-memory storage
    default_limits=["200/minute"]  # Default: 200 req/min
)
```

**Per-Endpoint Limits**:
```python
@router.post("/chat")
@limiter.limit("10/minute")  # Override default
async def chat(request: ChatRequest):
    ...
```

**Response on Limit Exceeded** (429):
```json
{
  "detail": "Rate limit exceeded: 10 per 1 minute",
  "status_code": 429
}
```

**Setup**:
```python
from app.middleware.rate_limiter import limiter

app = FastAPI()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
```

---

### request_context.py

**Purpose**: Request ID tracking and context injection for logging

**Features**:
- Generates unique request ID per request (`req_<uuid>`)
- Injects request ID into all logs
- Adds user context if authenticated
- Tracks request duration

**Context Variables**:
```python
request_id: str
user_id: Optional[str]
tenant_id: Optional[str]
start_time: float
```

**Log Injection**:
Every log automatically includes:
```json
{
  "message": "Processing request",
  "request_id": "req_abc123",
  "user_id": "user_456",
  "duration_ms": 45.2
}
```

**Setup**:
```python
from app.middleware.request_context import RequestContextMiddleware

app = FastAPI()
app.add_middleware(RequestContextMiddleware)
```

---

## Utilities

Location: `app/utils/`

### security.py

**Purpose**: Authentication and password utilities

**Key Functions**:

```python
def verify_password(plain: str, hashed: str) -> bool:
    """
    Verify password against bcrypt hash.
    Uses passlib with bcrypt backend.
    """
    
def get_password_hash(password: str) -> str:
    """
    Hash password with bcrypt (cost factor 12).
    
    Cost factor 12:
    - Secure against brute force
    - Fast enough for good UX (~150ms)
    """
    
def create_access_token(data: dict, expires_delta: timedelta = None) -> str:
    """
    Create JWT access token.
    
    Default expiry: 30 minutes
    Algorithm: HS256
    Secret: JWT_SECRET_KEY from config
    """
    
def create_refresh_token(data: dict) -> str:
    """
    Create JWT refresh token.
    
    Expiry: 7 days
    """
    
def decode_token(token: str) -> dict:
    """
    Decode and validate JWT token.
    
    Raises:
    - JWTError if invalid/expired
    """
```

**JWT Payload Structure**:
```json
{
  "sub": "user_id",
  "email": "user@example.com",
  "role": "client_admin",
  "tenant_id": "tenant-uuid",
  "exp": 1234567890,
  "iat": 1234567890
}
```

**Security Settings**:
- Algorithm: HS256
- Secret: `JWT_SECRET_KEY` (min 32 chars, no default)
- Access token: 30 minutes
- Refresh token: 7 days

---

### dependencies.py

**Purpose**: FastAPI dependency injection for auth and authorization

**Key Dependencies**:

```python
async def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    """
    Validates JWT token and returns current user.
    
    Process:
    1. Decode JWT token
    2. Extract user_id
    3. Get user from database
    4. Verify user exists, active, not deleted
    
    Raises:
    - 401 if token invalid/expired
    - 401 if user not found/deleted/inactive
    """
    
async def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    """
    Ensures user is active.
    Raises 401 if user.is_active = False
    """
    
async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """
    Requires platform_admin or aob_admin role.
    
    Raises 403 if role not in ["platform_admin", "aob_admin"]
    """
    
async def require_tenant_access(
    tenant_id: str,
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Ensures user has access to tenant.
    
    Access rules:
    - platform_admin: ALL tenants
    - aob_admin: ALL tenants
    - client_admin/agent: ONLY own tenant
    
    Raises 403 if insufficient access
    """
    
async def get_api_key_tenant(x_api_key: Optional[str] = Header(None)) -> str:
    """
    Validates API key and returns tenant_id.
    Used for widget authentication (no user login).
    
    Raises 401 if key invalid
    """
```

**Usage in Routes**:
```python
@router.get("/api/tenants/{tenant_id}")
async def get_tenant(
    tenant_id: str,
    current_user: User = Depends(require_tenant_access)
):
    # current_user already validated for tenant access
    tenant = tenant_service.get_tenant(tenant_id)
    return tenant
```

---

### exceptions.py

**Purpose**: Custom exception classes

**Exception Definitions**:

```python
class AuthenticationError(Exception):
    """Raised for authentication failures (401)."""
    pass

class PermissionError(Exception):
    """Raised for authorization failures (403)."""
    pass

class NotFoundError(Exception):
    """Raised when resource not found (404)."""
    pass

class ValidationError(Exception):
    """Raised for data validation errors (422)."""
    pass
```

**Usage**:
```python
from app.utils.exceptions import NotFoundError

user = user_service.get_user(user_id)
if not user:
    raise NotFoundError(f"User {user_id} not found")
```

**Handled By**: `exception_handlers.py` middleware

---

## Workers

Location: `app/workers/`

### worker.py

**Purpose**: Background worker for async document processing via SQS

**Process Flow**:
```
1. Poll SQS queue for messages
2. Download file from S3
3. Save to temp file
4. Ingest into RAG (parse → chunk → embed → store)
5. Delete message from queue
6. Clean up temp file
```

**Implementation**:

```python
def process_queue():
    while True:
        # Poll SQS
        messages = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=20  # Long polling
        )
        
        for message in messages:
            try:
                data = json.loads(message['Body'])
                
                # Download from S3
                s3_key = data['source'].replace('s3://bucket/', '')
                file_obj = s3.get_object(Bucket=bucket, Key=s3_key)
                file_content = file_obj['Body'].read()
                
                # Save to temp file
                temp_file = f"/tmp/{data['source']}"
                with open(temp_file, 'wb') as f:
                    f.write(file_content)
                
                # Ingest into RAG
                rag_service.ingest_document(
                    file_path=temp_file,
                    tenant_id=data['tenant_id'],
                    source=data['source'],
                    document_type=data['document_type']
                )
                
                # Delete message from queue
                sqs.delete_message(
                    QueueUrl=queue_url,
                    ReceiptHandle=message['ReceiptHandle']
                )
                
                # Clean up
                os.remove(temp_file)
                
            except Exception as e:
                logger.error(f"Error processing message: {e}")
                # Message returns to queue for retry
```

**Error Handling**:
- Failed messages returned to queue
- Retry with exponential backoff (SQS handles this)
- Dead letter queue for persistent failures

**SQS Configuration**:
```python
QUEUE_URL: str = os.getenv("AWS_SQS_QUEUE_URL")
POLL_INTERVAL: int = 5  # seconds
MAX_MESSAGES: int = 10
WAIT_TIME: int = 20  # long polling
```

---

### run_worker_service.py

**Purpose**: Worker service runner/launcher

**Features**:
- Graceful shutdown handling (SIGTERM, SIGINT)
- Exception recovery
- Logging setup

**Implementation**:
```python
import signal
import sys
from worker import process_queue

def signal_handler(sig, frame):
    logger.info("Shutting down worker gracefully...")
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    logger.info("Starting worker service...")
    
    try:
        process_queue()
    except Exception as e:
        logger.error(f"Worker crashed: {e}")
        sys.exit(1)
```

**Usage**:
```bash
python app/workers/run_worker_service.py
```

**Deployment Options**:
1. **systemd service** (Linux)
2. **Docker container**
3. **AWS ECS task**
4. **Kubernetes pod**

---

## Deployment Guide

### Local Development

**1. Install Dependencies**:
```bash
cd /home/harshan/aob_bot
uv sync
```

**2. Setup Environment**:
```bash
cp .env.example .env
# Edit .env with your credentials
```

**Required Environment Variables**:
```bash
# Database (AWS DynamoDB)
USE_DYNAMODB=True
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your-key
AWS_SECRET_ACCESS_KEY=your-secret

# Qdrant (Local)
Qdrant_URL=  # Leave empty for local mode
Qdrant_PATH=data/qdrant_data
QDRANT_COLLECTION_NAME=aob_documents

# AWS Bedrock
AWS_BEARER_TOKEN_BEDROCK=your-token-here
BEDROCK_LLM_MODEL_ID=openai.gpt-oss-120b-1:0

# Security (GENERATE NEW!)
JWT_SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
```

**3. Set up DynamoDB**:
Ensure you have access to AWS DynamoDB or run LocalStack.
If using AWS, creating the Single Table is required (Primary Key: `PK`, Sort Key: `SK`).

```bash
# AWS CLI Example
aws dynamodb create-table \
    --table-name aob_single_table \
    --attribute-definitions AttributeName=PK,AttributeType=S AttributeName=SK,AttributeType=S \
    --key-schema AttributeName=PK,KeyType=HASH AttributeName=SK,KeyType=RANGE \
    --billing-mode PAY_PER_REQUEST
```

**4. Start API Server**:
```bash
uv run uvicorn app.main:app --reload --port 8000
```

**5. Start Background Worker** (optional):
```bash
python app/workers/run_worker_service.py
```

---

### Production Deployment

**1. Environment Variables**:
```bash
# Use secrets manager (AWS Secrets Manager, HashiCorp Vault)
JWT_SECRET_KEY=<secure-32-char-string>
USE_DYNAMODB=True
AWS_REGION=us-east-1
Qdrant_URL=https://qdrant.example.com:6333
QDRANT_API_KEY=<qdrant-auth-key>
```

**2. Database Setup**:
```bash
# DynamoDB (Serverless)
# - No provisioning needed (On-Demand billing)
# - Ensure IAM roles for Lambda/EC2 have DynamoDB access

# Qdrant (EC2 or managed)
# - Deploy Qdrant server
# - Configure authentication
# - Set up firewall rules
```

**3. Run with Gunicorn/Uvicorn**:
```bash
# Production server (multi-worker)
gunicorn app.main:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000
```

**4. Docker Deployment**:
```dockerfile
FROM python:3.10-slim

WORKDIR /app
COPY . .

RUN pip install uv && uvrun sync

CMD ["gunicorn", "app.main:app", "--workers", "4", "--worker-class", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000"]
```

**5. Systemd Service**:
```ini
[Unit]
Description=AOB Bot API Server
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/aob_bot
ExecStart=/opt/aob_bot/.venv/bin/gunicorn app.main:app --workers 4 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
Restart=always

[Install]
WantedBy=multi-user.target
```

---

### Health Checks

**Health Endpoint**:
```bash
curl http://localhost:8000/health

# Response:
{
  "status": "healthy",
  "version": "2.0.0",
  "timestamp": "2025-12-10T00:00:00Z"
}
```

**Database Health**:
```python
@app.get("/health/db")
async def health_db():
    try:
        # Check DynamoDB connectivity
        table.scan(Limit=1)
        return {"status": "healthy"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}
```

---

### Monitoring

**Logging**:
```bash
# JSON logs for aggregation
{
  "timestamp": "2025-12-10T00:00:00Z",
  "level": "INFO",
  "message": "Request processed",
  "request_id": "req_abc123",
  "duration_ms": 45.2
}
```

**Recommended Tools**:
- **Application**: Datadog, New Relic, Sentry
- **Logs**: ELK Stack, CloudWatch
- **Metrics**: Prometheus + Grafana

---

### Troubleshooting

**Common Issues**:

**1. "No module named 'slowapi'"**
```bash
Solution: uv add slowapi
```

**2. "Qdrant connection timeout"**
```bash
Solution: Qdrant is optional for startup (lazy connection)
App works without it - start Qdrant when ready for RAG
```

**3. "JWT_SECRET_KEY must be 32+ characters"**
```bash
Solution: Generate secure key
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

**4. "AccessDeniedException" (DynamoDB)**
```bash
Solution: Check AWS Credentials
- Check `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`
- Verify IAM role has `dynamodb:PutItem`, `dynamodb:GetItem`, etc.
```

**5. "Rate limit exceeded"**
```bash
Solution: Adjust rate limits in config
RATE_LIMIT_CHAT=20/minute  # Increase limit
```

---

## Summary

### Middleware Files
- `exception_handlers.py` - Centralized error handling
- `rate_limiter.py` - API rate limiting (SlowAPI)
- `request_context.py` - Request ID tracking

### Utility Files
- `security.py` - Authentication (JWT, bcrypt)
- `dependencies.py` - FastAPI dependencies (RBAC)
- `exceptions.py` - Custom exception classes

### Worker Files
- `worker.py` - Background document processor
- `run_worker_service.py` - Worker launcher

### Design Patterns
1. **Middleware Pipeline**: Request → [CORS] → [Context] → [Rate Limit] → [Exception Handlers] → Route
2. **Dependency Injection**: FastAPI dependencies for auth
3. **Background Processing**: SQS + Workers for async tasks
4. **Graceful Shutdown**: Signal handling for workers

### Security
- JWT tokens (HS256, 30min/7day expiry)
- Bcrypt hashing (cost 12)
- Rate limiting (200/min default, 10/min chat)
- Request tracking (unique IDs)
- RBAC (4 role levels)
