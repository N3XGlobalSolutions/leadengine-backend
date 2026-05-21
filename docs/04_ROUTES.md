# API Routes Documentation

> **All API endpoints (V1 & V2) with detailed examples**  
> Files: `app/routes/v1/*.py`, `app/routes/v2/*.py`

---

## Table of Contents

1. [Overview](#overview)
2. [Authentication](#authentication)  
3. [V1 Routes - auth.py](#v1-authpy)
4. [V1 Routes - users.py](#v1-userspy)
5. [V1 Routes - tenants.py](#v1-tenantspy)
6. [V1 Routes - chat.py](#v1-chatpy)
7. [V1 Routes - documents.py](#v1-documentspy)
8. [V1 Routes - leads.py](#v1-leadspy)
9. [V1 Routes - analytics.py](#v1-analyticspy)
9. [V1 Routes - analytics.py](#v1-analyticspy)

---

## Overview

The API is organized into modular routes. All routes are located in `app/routes/v1/` but are exposed under `/api/...`.


### Base URL Structure
```
/api/auth/*          # Authentication endpoints
/api/users/*         # User management
/api/tenants/*       # Tenant configuration
/api/chat            # Main chat endpoint
/api/documents/*     # Document ingestion
/api/leads/*         # Lead management
/api/analytics/*     # Statistics and reporting
```

---

## Authentication

### JWT Token Authentication

**Request Header**:
```
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

**Token Payload**:
```json
{
  "sub": "user_id",
  "email": "user@example.com",
  "role": "client_admin",
  "tenant_id": "tenant-uuid",
  "exp": 1234567890
}
```

**Token Lifetimes**:
- Access Token: 30 minutes
- Refresh Token: 7 days

---

### API Key Authentication (Widgets)

**Request Header**:
```
X-API-Key: aob_a1b2c3d4e5f6789...
```

**Use Case**: Chat widgets without user login

---

## V1: auth.py

**File**: `app/routes/v1/auth.py`

### 1. Register User

```http
POST /api/auth/register
Content-Type: application/json
```

**Request Body**:
```json
{
  "email": "user@example.com",
  "password": "SecurePass123",
  "full_name": "John Doe",
  "role": "client_admin",
  "tenant_id": "tenant-uuid"
}
```

**Response (201 Created)**:
```json
{
  "user_id": "user-abc123",
  "email": "user@example.com",
  "full_name": "John Doe",
  "role": "client_admin",
  "tenant_id": "tenant-uuid",
  "created_at": "2025-12-10T00:00:00Z",
  "is_active": true
}
```

**Validation**:
- Email must be unique
- Password minimum 8 characters
- Role must be valid enum value

---

### 2. Login

```http
POST /api/auth/login
Content-Type: application/json
```

**Request Body**:
```json
{
  "email": "user@example.com",
  "password": "SecurePass123"
}
```

**Response (200 OK)**:
```json
{
  "access_token": "eyJhbGciOi...",
  "refresh_token": "eyJhbGciOi...",
  "token_type": "bearer"
}
```

**Error (401 Unauthorized)**:
```json
{
  "detail": "Invalid credentials"
}
```

---

### 3. Refresh Token

```http
POST /api/auth/refresh
Authorization: Bearer <refresh_token>
```

**Response (200 OK)**:
```json
{
  "access_token": "new_access_token",
  "refresh_token": "new_refresh_token",
  "token_type": "bearer"
}
```

---

### 4. Get Current User

```http
GET /api/auth/me
Authorization: Bearer <access_token>
```

**Response (200 OK)**:
```json
{
  "user_id": "user-abc123",
  "email": "user@example.com",
  "full_name": "John Doe",
  "role": "client_admin",
  "tenant_id": "tenant-uuid",
  "is_active": true,
  "last_login": "2025-12-10T00:00:00Z"
}
```

---

## V1: users.py

**File**: `app/routes/v1/users.py`

### 1. Create User

```http
POST /api/users
Authorization: Bearer <token> (admin only)
Content-Type: application/json
```

**RBAC**: Only `platform_admin` and `aob_admin` can create users

**Request/Response**: Same as `/api/auth/register`

---

### 2. List Users

```http
GET /api/users?skip=0&limit=10
Authorization: Bearer <token>
```

**Query Parameters**:
- `skip`: Pagination offset (default: 0)
- `limit`: Results per page (default: 10, max: 100)

**Response (200 OK)**:
```json
{
  "users": [
    {
      "user_id": "user-123",
      "email": "user@example.com",
      "full_name": "John Doe",
      "role": "client_agent",
      "tenant_id": "tenant-xyz"
    }
  ],
  "total": 25
}
```

**Filtering Logic**:
- `platform_admin/aob_admin`: See ALL users
- `client_admin/client_agent`: See only users from own tenant

---

### 3. Get User

```http
GET /api/users/{user_id}
Authorization: Bearer <token>
```

**Response (200 OK)**: User object

**Error (403 Forbidden)**: If user tries to access user from different tenant

---

### 4. Update User

```http
PUT /api/users/{user_id}
Authorization: Bearer <token>
Content-Type: application/json
```

**Request Body**:
```json
{
  "full_name": "Jane Doe",
  "is_active": false
}
```

**Response (200 OK)**: Updated user object

---

### 5. Delete User (Soft Delete)

```http
DELETE /api/users/{user_id}
Authorization: Bearer <token> (admin only)
```

**Response (200 OK)**:
```json
{
  "message": "User deleted successfully"
}
```

**Effect**: Sets `is_deleted=True`, `deleted_at=now()`

---

## V1: tenants.py

**File**: `app/routes/v1/tenants.py`

### 1. Create Tenant

```http
POST /api/tenants
Authorization: Bearer <token> (admin only)
Content-Type: application/json
```

**Request Body**:
```json
{
  "name": "Acme Corporation",
  "config": {
    "welcome_message": "Welcome to Acme support!",
    "language": "en",
    "tone": "professional",
    "lead_form_fields": ["name", "email", "phone", "company"],
    "lead_trigger_count": 3,
    "guardrails": ["pricing", "competitors"]
  }
}
```

**Response (201 Created)**:
```json
{
  "tenant_id": "tenant-abc123",
  "name": "Acme Corporation",
  "config": { ... },
  "created_at": "2025-12-10T00:00:00Z",
  "is_active": true
}
```

---

### 2. Get Tenant

```http
GET /api/tenants/{tenant_id}
Authorization: Bearer <token>
```

**Response (200 OK)**: Tenant object with full config

**Caching**: Tenant objects cached for 5 minutes in `tenant_service`

---

### 3. Update Tenant

```http
PUT /api/tenants/{tenant_id}
Authorization: Bearer <token>
Content-Type: application/json
```

**Request Body** (partial update):
```json
{
  "config": {
    "tone": "friendly",
    "lead_trigger_count": 5
  }
}
```

**Effect**: Updates tenant, invalidates cache

---

### 4. Generate API Key

```http
POST /api/tenants/{tenant_id}/generate-api-key
Authorization: Bearer <token>
```

**Response (200 OK)**:
```json
{
  "api_key": "aob_a1b2c3d4e5f6789..."
}
```

**Format**: `aob_<uuid>` for easy identification

**Storage**: Stored in `tenant.config.api_key`

---

### 5. Get Welcome Message (Public)

```http
GET /api/tenants/{tenant_id}/welcome
```

**No authentication required**

**Response (200 OK)**:
```json
{
  "welcome_message": "Welcome to Acme support!"
}
```

**Use Case**: Chat widget initialization

---

## V1: chat.py ⭐

**File**: `app/routes/v1/chat.py` (Most complex route)

### 1. Send Message

```http
POST /api/chat
X-API-Key: aob_... OR Authorization: Bearer <token>
Content-Type: application/json
```

**Request Body**:
```json
{
  "query": "What are your pricing plans?",
  "tenant_id": "tenant-abc123",
  "conversation_id": "conv-xyz789",
  "user_identifier": "user@example.com"
}
```

**Fields**:
- `query` (required): User's message
- `tenant_id` (required): Tenant identifier
- `conversation_id` (optional): Continue existing conversation, creates new if omitted
- `user_identifier` (optional): User email, phone, or anonymous ID

---

**Response (200 OK)**:
```json
{
  "answer": "We have three pricing plans: Basic ($10/mo), Pro ($25/mo), and Enterprise (custom)...",
  "conversation_id": "conv-xyz789",
  "message_id": "msg-abc123",
  "sources": ["pricing_guide.pdf", "faq_document.docx"],
  "suggested_actions": [],
  "missing_fields": []
}
```

**Response (Lead Capture Active)**:
```json
{
  "answer": "Great! Could you please share your email address?",
  "conversation_id": "conv-xyz789",
  "message_id": "msg-def456",
  "sources": [],
  "suggested_actions": ["capture_lead"],
  "missing_fields": ["email", "phone"]
}
```

---

**Complete Chat Flow**:

1. **Get/Create Conversation**
   ```python
   if conversation_id:
       conversation = get_conversation(conversation_id)
   else:
       conversation = create_conversation(tenant_id, user_identifier)
   ```

2. **Add User Message**
   ```python
   add_message(conversation_id, role="user", content=query)
   ```

3. **Retrieve History** (last 10 messages for LLM context)
   ```python
   history = get_history(conversation_id)
   ```

4. **Get Tenant Config** (cached for 5 minutes)
   ```python
   tenant = get_tenant_cached(tenant_id)
   config = tenant.config
   ```

5. **RAG Answer Generation**
   - Input guardrails check
   - Similarity search (Qdrant)
   - Build prompt with context + history
   - LLM generation (AWS Bedrock)
   - Output guardrails check

6. **Add Assistant Message**
   ```python
   add_message(conversation_id, role="assistant", content=answer, sources=sources)
   ```

7. **Lead Capture Logic**
   ```python
   if message_count >= lead_trigger_count and not lead_captured:
       # Extract entities from user message
       extracted = extract_lead_info(query, lead_form_fields)
       
       # Check missing fields
       missing_fields = fields_not_in_extracted_data
       
       if missing_fields:
           return {"missing_fields": missing_fields, "suggested_actions": ["capture_lead"]}
       else:
           # Create lead
           create_lead({...extracted_data})
           mark_lead_captured(conversation_id)
           return {"suggested_actions": ["lead_captured"]}
   ```

---

### 2. Get Conversation Messages

```http
GET /api/chat/conversations/{conversation_id}/messages?skip=0&limit=50
Authorization: Bearer <token>
```

**Response (200 OK)**:
```json
{
  "messages": [
    {
      "message_id": "msg-123",
      "role": "user",
      "content": "Hello",
      "timestamp": "2025-12-10T00:00:00Z"
    },
    {
      "message_id": "msg-124",
      "role": "assistant",
      "content": "Hi! How can I help?",
      "sources": [],
      "timestamp": "2025-12-10T00:00:01Z"
    }
  ],
  "total": 15
}
```

---

### 3. Get Tenant Conversations

```http
GET /api/chat/tenants/{tenant_id}/conversations?skip=0&limit=20
Authorization: Bearer <token>
```

**Response (200 OK)**:
```json
{
  "conversations": [
    {
      "conversation_id": "conv-123",
      "user_identifier": "user@example.com",
      "started_at": "2025-12-10T00:00:00Z",
      "last_message_at": "2025-12-10T00:15:00Z",
      "message_count": 10,
      "lead_captured": true
    }
  ],
  "total": 45
}
```

---

### 4. Clear Session History

```http
POST /api/chat/sessions/{conversation_id}/clear
```

**Effect**: Clears TTL-based session history (keeps permanent messages)

---

### 5. Reset Session

```http
POST /api/chat/sessions/reset
```

**Effect**: Creates new conversation, clears old session  

---

## V1: documents.py

**File**: `app/routes/v1/documents.py`

### 1. Ingest Document (Sync)

```http
POST /api/documents/ingest
Authorization: Bearer <token>
Content-Type: multipart/form-data
```

**Form Data**:
```
file: <binary file data>
tenant_id: "tenant-abc123"
source: "product_manual.pdf"
document_type: "pdf"
```

**Supported Types**: `pdf`, `docx`, `txt`

**Process**:
1. Save uploaded file
2. Parse document (PDF/DOCX/TXT)
3. Semantic chunking (LangChain RecursiveCharacterTextSplitter)
4. Batch embedding generation
5. Store vectors in Qdrant
6. Save metadata in MongoDB

**Response (200 OK)**:
```json
{
  "message": "Document ingested successfully",
  "chunk_count": 50,
  "source": "product_manual.pdf"
}
```

---

### 2. Upload to S3 (Async)

```http
POST /api/documents/upload-to-s3
Authorization: Bearer <token>
Content-Type: multipart/form-data
```

**Process**:
1. Upload file to S3
2. Send SQS message with file location
3. Return immediately
4. Background worker processes document

**Response (202 Accepted)**:
```json
{
  "message": "Document queued for processing",
  "s3_url": "https://bucket.s3.region.amazonaws.com/file.pdf"
}
```

---

## V1: leads.py

**File**: `app/routes/v1/leads.py`

### 1. Create Lead

```http
POST /api/leads
Content-Type: application/json
```

**No authentication required** (for widget use)

**Request Body**:
```json
{
  "tenant_id": "tenant-abc123",
  "conversation_id": "conv-xyz789",
  "name": "John Doe",
  "email": "john@example.com",
  "phone": "+1-555-1234",
  "company": "Acme Corp",
  "message": "Interested in your product"
}
```

**Duplicate Check**: Prevents creating duplicate lead for same conversation

**Response (201 Created)**:
```json
{
  "lead_id": "lead-abc123",
  "tenant_id": "tenant-abc123",
  "conversation_id": "conv-xyz789",
  "name": "John Doe",
  "email": "john@example.com",
  "status": "new",
  "created_at": "2025-12-10T00:00:00Z"
}
```

---

### 2. Get Lead

```http
GET /api/leads/{lead_id}
Authorization: Bearer <token>
```

---

### 3. List Tenant Leads

```http
GET /api/leads/tenants/{tenant_id}/all?status=new&skip=0&limit=20
Authorization: Bearer <token>
```

**Query Parameters**:
- `status` (optional): Filter by lead status (`new`, `contacted`, `qualified`, `converted`, `lost`)
- `skip`: Pagination offset
- `limit`: Results per page

**Response (200 OK)**:
```json
{
  "leads": [
    {
      "lead_id": "lead-123",
      "name": "John Doe",
      "email": "john@example.com",
      "phone": "+1-555-1234",
      "company": "Acme Corp",
      "status": "new",
      "created_at": "2025-12-10T00:00:00Z"
    }
  ],
  "total": 45
}
```

---

### 4. Update Lead Status

```http
PUT /api/leads/{lead_id}/status
Authorization: Bearer <token>
Content-Type: application/json
```

**Request Body**:
```json
{
  "status": "qualified"
}
```

**Status Lifecycle**: `new → contacted → qualified → converted/lost`

**Response (200 OK)**: Updated lead object

---

## V1: analytics.py

**File**: `app/routes/v1/analytics.py`

### 1. Tenant Statistics

```http
GET /api/analytics/tenants/{tenant_id}/stats
Authorization: Bearer <token>
```

**Response (200 OK)**:
```json
{
  "conversations": 150,
  "leads": 45,
  "documents": 12,
  "leads_by_status": {
    "new": 20,
    "contacted": 15,
    "qualified": 5,
    "converted": 3,
    "lost": 2
  },
  "conversion_rate": 30.0,
  "generated_at": "2025-12-10T00:00:00Z"
}
```

**Optimization**: Single MongoDB aggregation pipeline (6x faster than multiple queries)

---

### 2. Platform Statistics

```http
GET /api/analytics/platform/stats
Authorization: Bearer <token> (admin only)
```

**Response (200 OK)**:
```json
{
  "total_tenants": 25,
  "total_conversations": 5000,
  "total_leads": 1200,
  "global_conversion_rate": 24.0
}
```

---

### 3. Daily Metrics

```http
GET /api/analytics/tenants/{tenant_id}/daily?days=7
Authorization: Bearer <token>
```

**Query Parameters**:
- `days`: Number of days to retrieve (default: 7)

**Response (200 OK)**:
```json
{
  "metrics": [
    {"date": "2025-12-03", "conversations": 15},
    {"date": "2025-12-04", "conversations": 23},
    {"date": "2025-12-05", "conversations": 18},
    {"date": "2025-12-06", "conversations": 20},
    {"date": "2025-12-07", "conversations": 25},
    {"date": "2025-12-08", "conversations": 22},
    {"date": "2025-12-09", "conversations": 19}
  ]
}
```

**Use Case**: Dashboard charts showing conversation volume trends

---

---

## Error Handling

### Standard Error Response

```json
{
  "detail": "Error message",
  "status_code": 401,
  "request_id": "req_abc123",
  "timestamp": "2025-12-10T00:00:00Z"
}
```

### HTTP Status Codes

| Code | Meaning | Example |
|------|---------|---------|
| 200 | Success | Request successful |
| 201 | Created | Resource created |
| 202 | Accepted | Queued for processing |
| 400 | Bad Request | Invalid input |
| 401 | Unauthorized | Invalid/missing token |
| 403 | Forbidden | Insufficient permissions |
| 404 | Not Found | Resource doesn't exist |
| 422 | Validation Error | Pydantic validation failed |
| 429 | Rate Limit | Too many requests |
| 500 | Server Error | Internal error |

---

## Rate Limiting

**Default**: 200 requests/minute per IP

**Chat Endpoints**: 10 requests/minute per IP

**Response on Limit Exceeded**:
```json
{
  "detail": "Rate limit exceeded: 10 per 1 minute",
  "status_code": 429
}
```

---

## Summary

### Route Statistics
- **Total Endpoints**: ~30
- **Routes**: ~30 endpoints across 7 files

### Authentication Methods
1. JWT tokens (access + refresh)
2. API keys (widget authentication)

### Key Features
- Multi-tenant isolation
- Role-based access control
- RAG-powered chat
- Conversational lead capture
- Real-time analytics
- Rate limiting

---

*Routes documentation complete. See [05_SERVICES.md](./05_SERVICES.md) for service layer implementation.*
