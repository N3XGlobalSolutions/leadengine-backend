# Complete API Documentation & Setup Guide

> **From Zero to Production - Complete guide with all endpoints, flows, and setup**  
> Everything you need to start from scratch and understand every API call

---

## Table of Contents

1. [Getting Started from Scratch](#getting-started-from-scratch)
2. [Database Design (Single Table)](#database-design-single-table)
3. [Complete API Reference](#complete-api-reference)
4. [Business Logic Flows](#business-logic-flows)
5. [Testing Guide](#testing-guide)

---

## Getting Started from Scratch

### Prerequisites

Install these on your system:
- Python 3.10+
- AWS Account (for DynamoDB/Bedrock) OR LocalStack
- Qdrant (Optional for local dev, can run file-based)
- Git

### Step 1: Clone and Setup Project

```bash
# Clone repository
cd /home/aobbot/aob_bot

# Install uv (Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install all dependencies
uv sync
```

### Step 2: Environment Configuration

Create `.env` file:

```bash
cp .env.example .env
```

Edit `.env` with your settings:

```bash
# Application
APP_NAME=AOB Bot RAG Backend
DEBUG=true

# Database (DynamoDB)
USE_DYNAMODB=True
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your-key
AWS_SECRET_ACCESS_KEY=your-secret

# Qdrant (Vector Database)
Qdrant_URL=http://your-ec2-ip:6333
QDRANT_COLLECTION_NAME=aob_documents
# OR for local file-based:
# Qdrant_PATH=data/qdrant_data

# AWS Bedrock (LLM)
AWS_BEARER_TOKEN_BEDROCK=your-bedrock-token
BEDROCK_LLM_MODEL_ID=openai.gpt-oss-120b-1:0
BEDROCK_EMBEDDING_MODEL_ID=amazon.titan-embed-text-v2:0

# Security (GENERATE NEW!)
JWT_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
JWT_ALGORITHM=HS256

# CORS
CORS_ORIGINS_STR=http://localhost:3000,http://localhost:8000

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json
```

### Step 3: Setup DynamoDB Tables

**Option A - AWS Managed (Production)**:
Create a single table named `aob_single_table` (or whatever your `DYNAMODB_TABLE_PREFIX` + name logic maps to, usually just strict naming or single table).

Required Keys:
- Partition Key: `PK` (String)
- Sort Key: `SK` (String)
- GSIs usually needed for indexing:
    - `GSI1_PK` / `GSI1_SK` (for lookups by email, tenant, etc.)

**Option B - LocalStack**:
```bash
docker run --rm -it -p 4566:4566 localstack/localstack
# Then create table via aws-cli targeting local endpoint
```

### Step 4: Start the Application

```bash
# Development mode (with auto-reload)
uvicorn app.main:app --reload --port 8000

# You should see:
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Application startup complete.
```

### Step 5: Verify Installation

```bash
# Health check
curl http://localhost:8000/health

# Expected response:
{
  "status": "healthy",
  "version": "2.0.0"
}
```

### Step 6: Access API Documentation

Open browser and go to:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

---

## Database Design (Single Table)

We use a **DynamoDB Single Table Design** pattern. All entities (Users, Tenants, Leads, Conversation Metadata) are stored in one table. Messages are also stored here (or potentially a separate table if size dictates, but for this guide we assume the Service layer handles it via the Single Table abstraction).

### Core Entities & Key Patterns

| Entity | PK | SK | Purpose |
|--------|----|----|---------|
| **Tenant** | `TENANT#<id>` | `METADATA` | Tenant config & info |
| **User** | `USER#<id>` | `METADATA` | User profile, role |
| **Lead** | `LEAD#<id>` | `METADATA` | Lead details |
| **Conversation** | `CONV#<id>` | `METADATA` | Chat metadata |
| **Message** | `CONV#<id>` | `MSG#<ts>` | Chat messages ordered by time |

### Access Patterns (GSI)

**GSI1**: Used for "Find all items of type X for Tenant Y"
- `GSI1PK`: `TENANT#<id>`
- `GSI1SK`: `<TYPE>#<SortValue>`

Example: List all leads for a tenant
- Query: `GSI1PK="TENANT#123" AND begins_with(GSI1SK, "LEAD#")`

---

## Complete API Reference

### Authentication Flow

#### 1. Register First Admin User

**Endpoint**: `POST /api/auth/register`

**Request**:
```bash
curl -X POST http://localhost:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "admin@example.com",
    "password": "SecurePass123",
    "full_name": "Admin User",
    "role": "platform_admin"
  }'
```

**Internal Logic**:
1. Check if email exists (GSI Lookup).
2. Create User Item:
   ```python
   Item={
       "PK": "USER#uuid",
       "SK": "METADATA",
       "email": "admin@example.com",
       "GSI1PK": "TENANT#<null>", # or specialized partition for admins
       ...
   }
   ```
3. Return success.

#### 2. Login

**Endpoint**: `POST /api/auth/login`

**Internal Logic**:
1. Lookup User by Email (GSI).
2. Verify Password (Bcrypt).
3. Generate JWT Access/Refresh tokens.
4. Update `last_login` timestamp in DynamoDB.

#### 3. Get Current User

**Endpoint**: `GET /api/auth/me`

---

### Tenant Management Flow

#### 1. Create Tenant

**Endpoint**: `POST /api/tenants`

**Request Body**:
```json
{
  "name": "Acme Corp",
  "config": {
    "welcome_message": "Hello!",
    "language": "en"
  }
}
```

**DynamoDB Operation**:
```python
table.put_item(
    Item={
        "PK": f"TENANT#{uuid}",
        "SK": "METADATA",
        "name": "Acme Corp",
        "config": {...}
    }
)
```

#### 2. Generate API Key

**Endpoint**: `POST /api/tenants/{id}/generate-api-key`

---

### Document Ingestion Flow

**Endpoint**: `POST /api/documents/ingest`

**Process**:
1. Upload file.
2. Text Extraction (PDF/DOCX).
3. Chunking (LangChain).
4. **Embedding Generation** (Bedrock).
5. **Vector Storage** (Qdrant `upsert`).
6. **Metadata Storage** (DynamoDB `DOC#<id>`).

---

## Business Logic Flows

(See [05_SERVICES.md](./05_SERVICES.md) for detailed logic)

---

## Testing Guide

### Running Automated Tests
The repository includes a comprehensive verification script.

```bash
# Verify API health and logic
python scripts/verify_full_api.py
```
This script tests:
- Auth (Register/Login)
- Tenant/User CRUD
- Document Ingestion
- Chat RAG flow
- Lead Capture

### Manual Widget Testing
Use the Javascript snippet provided in the [Deployment Guide](./06_MIDDLEWARE_UTILITIES_WORKERS.md#javascript-widget-example) to test the chat widget integration against your local server.
