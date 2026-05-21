# AOB Bot Cloud Architecture Guide

This document outlines the recommended architecture for deploying the AOB Bot to **AWS Lambda (Serverless)** for production. It specifically addresses **Background Processing (SQS)** and **Rate Limiting (Redis)**.

## 1. High-Level Architecture

The system moves from a monolithic server (FastAPI) to event-driven microservices using AWS Lambda.

```mermaid
graph TD
    User([User / Client])
    
    subgraph "API Layer (Sync)"
        ALB[API Gateway / Function URL]
        API_Lambda[API Lambda (FastAPI)]
        Redis[(Redis Cache)]
    end
    
    subgraph "Storage Layer"
        S3[S3 Bucket (Documents)]
        Mongo[(MongoDB Atlas)]
        Qdrant[(Qdrant EC2)]
    end
    
    subgraph "Background Layer (Async)"
        SQS[SQS Queue]
        Worker_Lambda[Worker Lambda]
        Bedrock[AWS Bedrock]
    end

    User -->|HTTPS| ALB
    ALB --> API_Lambda
    
    %% Rate Limiting
    API_Lambda -->|Check Limit| Redis
    
    %% Data Access
    API_Lambda -->|Metadata| Mongo
    API_Lambda -->|Vector Search| Qdrant
    API_Lambda -->|Chat Gen| Bedrock

    %% Ingestion Flow
    API_Lambda -->|1. Upload File| S3
    API_Lambda -->|2. Send Event| SQS
    
    %% Background Processing
    SQS -->|Trigger| Worker_Lambda
    Worker_Lambda -->|3. Download| S3
    Worker_Lambda -->|4. Embed| Bedrock
    Worker_Lambda -->|5. Store| Qdrant
    Worker_Lambda -->|6. Update Status| Mongo
```

---

## 2. Rate Limiting: Why Redis?

In a serverless environment (Lambda), you cannot store rate limit counters in memory (like we do now with `slowapi`'s memory backend) because:
1.  **Multiple Instances**: AWS might spin up 50 copies of your Lambda function to handle traffic. They don't share memory.
2.  **Statelessness**: When a Lambda finishes a request, it "freezes" or dies. Memory is lost.

### Solution: AWS ElastiCache (Redis) or MemoryDB
We use Redis as a **shared counter**.

*   **Flow**:
    1.  Request comes in from User A.
    2.  Lambda checks Redis: `GET rate_limit:user_A`.
    3.  If count > 100, return `429 Too Many Requests`.
    4.  Else, `INCR rate_limit:user_A` and process request.

### Implementation in Production
Update `app/middleware/rate_limiter.py` to use Redis:

```python
from slowapi.util import get_remote_address
from slowapi.limiter import Limiter

# In production, read from ENV
redis_url = os.getenv("REDIS_URL", "redis://elasticache-endpoint:6379")

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=redis_url  # Connects to shared Redis
)
```

---

## 3. Background Tasks: AWS SQS

Processing a PDF can take 10-60 seconds. API Gateway has a **29-second timeout**. If you do this synchronously, your API will fail.

### The Problem with Celery in Cloud
*   **Celery** requires a standard server (EC2) running 24/7 to listen for tasks. This defeats the purpose of "Serverless" (pay-for-use).
*   **SQS + Lambda** works by *pushing* events. AWS manages the "listener" for you.

### Recommended Workflow (SQS)

1.  **API Lambda (`/ingest`)**:
    *   Validates the file upload.
    *   Uploads bytes to **S3**.
    *   Sends a lightweight JSON message to **SQS**:
        ```json
        {
          "tenant_id": "uuid",
          "s3_key": "uploads/file.pdf",
          "original_filename": "report.pdf"
        }
        ```
    *   Returns `202 Accepted` to user immediately.

2.  **Worker Lambda**:
    *   Configured with SQS as an **Event Source**.
    *   Wakes up *only* when a message arrives.
    *   Downloads file from S3 → Chunks → Embeds (Bedrock) → Upserts (Qdrant).
    *   On failure (e.g., Bedrock API glitch), SQS automatically retries 3 times before moving to a Dead Letter Queue (DLQ).

---

## 4. Cost Analysis (Estimates)

### Scenario: Startup / Low Volume
*   **Requests**: 100,000 / month
*   **Documents**: 1,000 uploads / month

| Service | Estimated Cost | Notes |
| :--- | :--- | :--- |
| **AWS Lambda** | **$0.00** | Covered by Free Tier (400k GB-seconds/mo) |
| **AWS SQS** | **$0.00** | First 1M requests/mo are free |
| **AWS S3** | **<$0.10** | Storage is cheap for PDFs |
| **MongoDB Atlas** | **$0.00** | M0 Sandbox is free |
| **Qdrant (EC2)** | **~$15.00** | t4g.small spot instance (or Fargate) |
| **Redis** | **~$0 - $15** | Use **Upstash (Serverless Redis)** for free tier, or smallest ElastiCache node |
| **AWS Bedrock** | **Usage based** | ~$0.0001 per 1k tokens. Very cheap. |
| **Total** | **~$15 - $30 / mo** | dominated by the EC2 instance cost |

### Scenario: Scaling Up
*   **SQS**: Extremely cheap ($0.40 per 1 million requests).
*   **Lambda**: Scales linearly. Cheaper than EC2 for bursty traffic; more expensive for constant 24/7 distinct load.
*   **Redis**: Fixed cost for ElastiCache instance, or per-request for Serverless options (Upstash/AWS MemoryDB).

### Why not Celery?
*   Running a dedicated EC2/Fargate worker for Celery would cost **$15-30/month** *minimum*, even if no one uploads files.
*   SQS + Lambda costs **$0** when idle.

---

## 5. Deployment Checklist

1.  [ ] **Create S3 Bucket**: `aob-bot-documents` (Private)
2.  [ ] **Create SQS Queue**: `aob-bot-ingestion-queue`
3.  [ ] **Provision Redis**:
    *   *Option A (AWS)*: ElastiCache Serverless (easiest but potential min cost)
    *   *Option B (External)*: Upstash Redis (Free tier compatible)
4.  [ ] **Update Code**:
    *   `POST /ingest` -> Upload to S3 + Send SQS Message
    *   Create new handler `process_document(event, context)` for the worker
5.  [ ] **SAM / Serverless Framework**: Use `template.yaml` to define the infrastructure code.
