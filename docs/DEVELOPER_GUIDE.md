# AOB Bot Backend - Complete Developer & User Documentation

> **Version:** 2.0.0  
> **Last Updated:** December 2025  
> **Tech Stack:** FastAPI, MongoDB, Qdrant, Google Gemini AI

---

## Table of Contents

1. [Overview](#1-overview)
2. [Quick Start Guide](#2-quick-start-guide)
3. [Business Logic & Architecture](#3-business-logic--architecture)
4. [User Roles & Permissions](#4-user-roles--permissions)
5. [API Reference](#5-api-reference)
6. [Configuration](#6-configuration)
7. [Workflows & Use Cases](#7-workflows--use-cases)
8. [Expected Outputs](#8-expected-outputs)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. Overview

### What is AOB Bot?

AOB Bot is a **multi-tenant SaaS chatbot backend** that enables businesses to deploy AI-powered customer support chatbots on their websites. It uses **RAG (Retrieval-Augmented Generation)** to answer questions based on uploaded knowledge base documents.

### Key Features

| Feature | Description |
|---------|-------------|
| **Multi-Tenancy** | Complete data isolation between different client organizations |
| **RAG-based Chat** | AI answers using your uploaded documents (PDFs, DOCX, URLs) |
| **Lead Capture** | Collect visitor information during chat conversations |
| **Role-Based Access** | 4-level permission system for different user types |
| **API Key Auth** | Secure widget integration for public websites |
| **Rate Limiting** | Protection against API abuse |
| **Structured Logging** | JSON logs with request tracing |

### How It Works

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Website       │     │   Admin         │     │   Mobile App    │
│   Chat Widget   │     │   Dashboard     │     │   (Future)      │
└────────┬────────┘     └────────┬────────┘     └────────┬────────┘
         │                       │                       │
         │ API Key Auth          │ JWT Auth              │ JWT Auth
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │    AOB Bot Backend      │
                    │   (FastAPI + Python)    │
                    └────────────┬────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              │                  │                  │
     ┌────────▼────────┐ ┌───────▼───────┐ ┌───────▼───────┐
     │    MongoDB      │ │    Qdrant     │ │ Google Gemini │
     │  (App Data)     │ │ (Vectors)     │ │   (AI/LLM)    │
     └─────────────────┘ └───────────────┘ └───────────────┘
```

---

## 2. Quick Start Guide

### Prerequisites

- **Python 3.13+**
- **MongoDB** running on `localhost:27017`
- **Gemini API Key** from Google AI Studio

### Step 1: Clone & Setup

```bash
# Navigate to project
cd /home/harshan/aob_bot

# Create virtual environment (if not exists)
python -m venv .venv

# Activate virtual environment
source .venv/bin/activate

# Install dependencies
~/.local/bin/uv sync
# OR using pip
pip install -r requirements.txt
```

### Step 2: Configure Environment

Copy and edit the environment file:

```bash
cp .env.example .env
nano .env
```

**Required variables in `.env`:**

```env
# REQUIRED - Get from https://makersuite.google.com/app/apikey
GEMINI_API_KEY=your-actual-gemini-api-key

# REQUIRED - Generate a secure random string (min 32 characters)
JWT_SECRET_KEY=your-super-secret-jwt-key-at-least-32-chars-long

# Database (default works if MongoDB is on localhost)
MONGODB_URI=mongodb://localhost:27017
DB_NAME=aob_bot_db

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json

# Rate Limiting
RATE_LIMIT_DEFAULT=100/minute
RATE_LIMIT_CHAT=10/minute
```

### Step 3: Start MongoDB

```bash
# Using Docker
docker run -d -p 27017:27017 --name mongodb mongo:latest

# OR verify local MongoDB is running
mongosh --eval "db.adminCommand('ping')"
```

### Step 4: Run the Server

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

### Step 5: Verify Installation

```bash
curl http://localhost:8000/health
```

**Expected Output:**
```json
{"status": "healthy", "version": "2.0.0"}
```

### Step 6: Access API Documentation

Open in browser: **http://localhost:8000/docs**

This shows the interactive Swagger UI with all endpoints.

---

## 3. Business Logic & Architecture

### 3.1 Multi-Tenancy Model

AOB Bot uses a **shared database, tenant-scoped data** model:

```
┌─────────────────────────────────────────────────────────────┐
│                     PLATFORM (N3X)                           │
│  Owns the infrastructure, manages AOB and all clients       │
├─────────────────────────────────────────────────────────────┤
│                     AOB (Primary Tenant)                     │
│  SaaS provider, creates and manages client accounts         │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │  Client A   │  │  Client B   │  │  Client C   │         │
│  │  (Acme Co)  │  │  (Beta Inc) │  │  (Gamma Ltd)│         │
│  │             │  │             │  │             │         │
│  │ • Own docs  │  │ • Own docs  │  │ • Own docs  │         │
│  │ • Own leads │  │ • Own leads │  │ • Own leads │         │
│  │ • Own chats │  │ • Own chats │  │ • Own chats │         │
│  └─────────────┘  └─────────────┘  └─────────────┘         │
└─────────────────────────────────────────────────────────────┘
```

**Key Principle:** Every piece of data has a `tenant_id` that ensures complete isolation.

### 3.2 RAG Pipeline (How AI Answers Questions)

```
USER QUESTION: "What is your refund policy?"
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│ Step 1: EMBED THE QUESTION                                 │
│   • Convert question to 768-dimensional vector             │
│   • Using: Google Gemini text-embedding-004                │
│   • Output: [0.123, -0.456, 0.789, ...]                   │
└───────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│ Step 2: SEARCH VECTOR DATABASE (Qdrant)                   │
│   • Find similar document chunks                           │
│   • Filter: WHERE tenant_id = current_tenant ← CRITICAL!  │
│   • Return: Top 5 most relevant chunks                     │
│                                                            │
│   Results:                                                 │
│   1. "Refund Policy: 30 days..." (score: 0.92)            │
│   2. "Returns must be made..." (score: 0.87)              │
│   3. "To request refund..." (score: 0.85)                 │
└───────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│ Step 3: BUILD PROMPT WITH CONTEXT                         │
│                                                            │
│   "You are a helpful assistant.                           │
│    Use the following context to answer:                   │
│                                                            │
│    Context:                                                │
│    [Chunk 1] Refund Policy: 30 days...                    │
│    [Chunk 2] Returns must be made...                      │
│    [Chunk 3] To request refund...                         │
│                                                            │
│    Question: What is your refund policy?                  │
│    Answer:"                                                │
└───────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│ Step 4: GENERATE ANSWER (Gemini LLM)                      │
│   • Send prompt to gemini-2.5-flash                        │
│   • Get natural language response                          │
│                                                            │
│   Answer: "Our refund policy allows full refunds within   │
│   30 days of purchase. To request a refund, please        │
│   contact our support team..."                             │
└───────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│ Step 5: RETURN RESPONSE                                    │
│   {                                                        │
│     "answer": "Our refund policy allows...",              │
│     "sources": ["refund-policy.pdf", "faq.docx"],         │
│     "conversation_id": "uuid-123",                         │
│     "suggested_actions": ["capture_lead"]                  │
│   }                                                        │
└───────────────────────────────────────────────────────────┘
```

### 3.3 Document Ingestion Pipeline

When you upload a document:

```
UPLOAD: product-manual.pdf
        │
        ▼
┌────────────────────────────────────────┐
│ 1. PARSE DOCUMENT                       │
│    • PDF → PyPDF2 extracts text        │
│    • DOCX → python-docx extracts text  │
│    • URL → BeautifulSoup scrapes text  │
└────────────────────────────────────────┘
        │
        ▼
┌────────────────────────────────────────┐
│ 2. CHUNK TEXT                           │
│    • Split into 1000-character pieces  │
│    • 100-character overlap             │
│    • Preserves context across chunks   │
│                                         │
│    "Introduction to our product..."     │
│    "...product. Features include..."    │
│    "...include: 1. Fast processing..."  │
└────────────────────────────────────────┘
        │
        ▼
┌────────────────────────────────────────┐
│ 3. EMBED EACH CHUNK                     │
│    • Convert text to 768-dim vectors   │
│    • Each chunk becomes searchable     │
└────────────────────────────────────────┘
        │
        ▼
┌────────────────────────────────────────┐
│ 4. STORE IN QDRANT                      │
│    Each point has:                      │
│    • id: unique UUID                    │
│    • vector: [768 floats]               │
│    • payload:                           │
│      - tenant_id (for isolation)        │
│      - source (filename)                │
│      - text (original chunk)            │
│      - chunk_index                      │
└────────────────────────────────────────┘
        │
        ▼
┌────────────────────────────────────────┐
│ 5. SAVE METADATA TO MONGODB             │
│    {                                    │
│      tenant_id, source, document_type, │
│      chunk_count, status: "ingested"   │
│    }                                    │
└────────────────────────────────────────┘
```

### 3.4 Lead Capture Flow

```
CHAT CONVERSATION
        │
        ├── Message 1: "Hi, what do you offer?"
        ├── Message 2: "We offer X, Y, Z..."
        ├── Message 3: "What's the pricing?"
        ├── Message 4: "Pricing starts at $99..."
        │
        ▼
┌────────────────────────────────────────┐
│ LEAD SUGGESTION TRIGGERED               │
│ (After 3+ messages)                     │
│                                         │
│ Response includes:                      │
│ "suggested_actions": ["capture_lead"]   │
└────────────────────────────────────────┘
        │
        ▼
┌────────────────────────────────────────┐
│ WIDGET SHOWS LEAD FORM                  │
│                                         │
│ User fills:                             │
│ • Name: "John Smith"                    │
│ • Email: "john@example.com"             │
│ • Phone: "+1-555-0123"                  │
│ • Company: "Acme Inc"                   │
└────────────────────────────────────────┘
        │
        ▼
┌────────────────────────────────────────┐
│ POST /api/leads                         │
│                                         │
│ • Creates lead record                   │
│ • Links to conversation                 │
│ • Marks conversation.lead_captured=true│
│ • Sales team can now follow up         │
└────────────────────────────────────────┘
```

---

## 4. User Roles & Permissions

### 4.1 Role Hierarchy

```
┌─────────────────────────────────────────────────────────────┐
│                     PLATFORM_ADMIN                           │
│  • Full system access                                        │
│  • Create/manage all tenants                                 │
│  • Create/manage all users (any role)                        │
│  • View data across all tenants                              │
│  • Typically: N3X (Platform Owner)                           │
├─────────────────────────────────────────────────────────────┤
│                       AOB_ADMIN                              │
│  • Manage all tenants                                        │
│  • Create client admin users                                 │
│  • View cross-tenant analytics                               │
│  • Cannot modify platform settings                           │
│  • Typically: AOB Staff                                      │
├─────────────────────────────────────────────────────────────┤
│                      CLIENT_ADMIN                            │
│  • Manage own tenant configuration                           │
│  • Create client agent users (own tenant only)               │
│  • Upload documents to knowledge base                        │
│  • Generate API keys for chat widget                         │
│  • View own tenant's conversations & leads                   │
│  • Cannot access other tenants                               │
│  • Typically: Client Company Admin                           │
├─────────────────────────────────────────────────────────────┤
│                      CLIENT_AGENT                            │
│  • View own tenant's conversations                           │
│  • View own tenant's leads                                   │
│  • Cannot modify tenant configuration                        │
│  • Cannot create users                                       │
│  • Cannot access other tenants                               │
│  • Typically: Sales/Support Staff                            │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 Permission Matrix

| Action | PLATFORM_ADMIN | AOB_ADMIN | CLIENT_ADMIN | CLIENT_AGENT |
|--------|:--------------:|:---------:|:------------:|:------------:|
| Create tenants | ✅ | ✅ | ❌ | ❌ |
| Delete tenants | ✅ | ✅ | ❌ | ❌ |
| List all tenants | ✅ | ✅ | ❌ | ❌ |
| View own tenant | ✅ | ✅ | ✅ | ✅ |
| Update own tenant | ✅ | ✅ | ✅ | ❌ |
| Create users (any role) | ✅ | ✅ | ❌ | ❌ |
| Create users (own tenant) | ✅ | ✅ | ✅* | ❌ |
| List all users | ✅ | ✅ | ❌ | ❌ |
| List tenant users | ✅ | ✅ | ✅ | ✅ |
| Upload documents | ✅ | ✅ | ✅ | ❌ |
| Generate API key | ✅ | ✅ | ✅ | ❌ |
| View conversations | ✅ | ✅ | ✅ | ✅ |
| View leads | ✅ | ✅ | ✅ | ✅ |
| Update lead status | ✅ | ✅ | ✅ | ✅ |

*CLIENT_ADMIN can only create CLIENT_AGENT users for their own tenant.

---

## 5. API Reference

### 5.1 Authentication Endpoints

#### Register User
```http
POST /api/auth/register
Content-Type: application/json

{
  "email": "user@example.com",
  "password": "SecurePass123",
  "full_name": "John Doe",
  "role": "client_admin",
  "tenant_id": "uuid-of-tenant"  // Optional for platform_admin
}
```

**Response (201 Created):**
```json
{
  "user_id": "550e8400-e29b-41d4-a716-446655440000",
  "email": "user@example.com",
  "full_name": "John Doe",
  "role": "client_admin",
  "tenant_id": "uuid-of-tenant",
  "created_at": "2025-12-06T10:30:00Z",
  "is_active": true,
  "last_login": null
}
```

#### Login
```http
POST /api/auth/login
Content-Type: application/json

{
  "email": "user@example.com",
  "password": "SecurePass123"
}
```

**Response (200 OK):**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

#### Get Current User
```http
GET /api/auth/me
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "user_id": "550e8400-e29b-41d4-a716-446655440000",
  "email": "user@example.com",
  "full_name": "John Doe",
  "role": "client_admin",
  "tenant_id": "uuid-of-tenant",
  "created_at": "2025-12-06T10:30:00Z",
  "is_active": true,
  "last_login": "2025-12-06T10:35:00Z"
}
```

#### Refresh Token
```http
POST /api/auth/refresh?refresh_token=<refresh_token>
```

**Response (200 OK):**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

---

### 5.2 Tenant Endpoints

#### Create Tenant (Admin Only)
```http
POST /api/tenants
Authorization: Bearer <admin_token>
Content-Type: application/json

{
  "name": "Acme Corporation",
  "config": {
    "welcome_message": "Welcome to Acme Support! How can I help?",
    "lead_form_enabled": true,
    "lead_form_fields": ["name", "email", "phone", "company"],
    "language": "en",
    "tone": "professional",
    "guardrails": ["politics", "religion"],
    "branding": {
      "primary_color": "#0066CC",
      "logo_url": "https://acme.com/logo.png"
    }
  }
}
```

**Response (201 Created):**
```json
{
  "tenant_id": "550e8400-e29b-41d4-a716-446655440001",
  "name": "Acme Corporation",
  "config": {
    "welcome_message": "Welcome to Acme Support!",
    "lead_form_enabled": true,
    "lead_form_fields": ["name", "email", "phone", "company"],
    "language": "en",
    "tone": "professional",
    "guardrails": ["politics", "religion"],
    "max_conversation_length": 50,
    "api_key": null,
    "branding": {
      "primary_color": "#0066CC",
      "logo_url": "https://acme.com/logo.png"
    }
  },
  "created_at": "2025-12-06T10:30:00Z",
  "is_active": true
}
```

#### Generate API Key
```http
POST /api/tenants/{tenant_id}/generate-api-key
Authorization: Bearer <token>
```

**Response (200 OK):**
```json
{
  "api_key": "aob_xJ9kP2mNqR8vL3dS5hF7gK1bC4wE6yT8",
  "tenant_id": "550e8400-e29b-41d4-a716-446655440001"
}
```

---

### 5.3 Document Endpoints

#### Upload Document
```http
POST /api/documents/ingest
Authorization: Bearer <token>
X-Tenant-ID: <tenant_id>
Content-Type: multipart/form-data

file: <binary file data>
```

**Response (200 OK):**
```json
{
  "status": "success",
  "message": "Document product-manual.pdf ingested for tenant 550e8400..."
}
```

#### Ingest from URL
```http
POST /api/documents/ingest-url?url=https://example.com/faq
Authorization: Bearer <token>
X-Tenant-ID: <tenant_id>
```

**Response (200 OK):**
```json
{
  "status": "success",
  "message": "URL https://example.com/faq ingested for tenant 550e8400..."
}
```

---

### 5.4 Chat Endpoints

#### Send Message (Widget - API Key Auth)
```http
POST /api/chat
X-API-Key: aob_xJ9kP2mNqR8vL3dS5hF7gK1bC4wE6yT8
Content-Type: application/json

{
  "query": "What are your business hours?",
  "tenant_id": "550e8400-e29b-41d4-a716-446655440001",
  "conversation_id": null,
  "user_identifier": "visitor-123"
}
```

**Response (200 OK):**
```json
{
  "answer": "Our business hours are Monday through Friday, 9 AM to 5 PM EST. We are closed on weekends and major holidays.",
  "conversation_id": "660e8400-e29b-41d4-a716-446655440002",
  "message_id": "770e8400-e29b-41d4-a716-446655440003",
  "sources": ["faq.pdf", "about-us.docx"],
  "suggested_actions": []
}
```

**After 3+ messages:**
```json
{
  "answer": "I'd be happy to help further...",
  "conversation_id": "660e8400-...",
  "message_id": "770e8400-...",
  "sources": ["..."],
  "suggested_actions": ["capture_lead"]
}
```

#### Get Conversation Messages
```http
GET /api/chat/conversations/{conversation_id}/messages?limit=50
Authorization: Bearer <token>
```

**Response (200 OK):**
```json
[
  {
    "message_id": "msg-001",
    "conversation_id": "conv-001",
    "role": "user",
    "content": "What are your hours?",
    "timestamp": "2025-12-06T10:30:00Z",
    "sources": []
  },
  {
    "message_id": "msg-002",
    "conversation_id": "conv-001",
    "role": "assistant",
    "content": "Our hours are 9 AM - 5 PM...",
    "timestamp": "2025-12-06T10:30:05Z",
    "sources": ["faq.pdf"]
  }
]
```

#### Get Tenant Conversations
```http
GET /api/chat/tenants/{tenant_id}/conversations?skip=0&limit=100
Authorization: Bearer <token>
```

**Response (200 OK):**
```json
[
  {
    "conversation_id": "conv-001",
    "tenant_id": "tenant-001",
    "user_identifier": "john@example.com",
    "started_at": "2025-12-06T10:30:00Z",
    "last_message_at": "2025-12-06T10:45:00Z",
    "message_count": 8,
    "is_active": true,
    "lead_captured": true
  }
]
```

---

### 5.5 Lead Endpoints

#### Capture Lead (Public - No Auth Required)
```http
POST /api/leads
Content-Type: application/json

{
  "tenant_id": "550e8400-e29b-41d4-a716-446655440001",
  "conversation_id": "660e8400-e29b-41d4-a716-446655440002",
  "name": "John Smith",
  "email": "john@example.com",
  "phone": "+1-555-0123",
  "company": "Acme Inc",
  "message": "Interested in enterprise plan"
}
```

**Response (201 Created):**
```json
{
  "lead_id": "880e8400-e29b-41d4-a716-446655440004",
  "tenant_id": "550e8400-e29b-41d4-a716-446655440001",
  "conversation_id": "660e8400-e29b-41d4-a716-446655440002",
  "name": "John Smith",
  "email": "john@example.com",
  "phone": "+1-555-0123",
  "company": "Acme Inc",
  "message": "Interested in enterprise plan",
  "status": "new",
  "created_at": "2025-12-06T10:45:00Z",
  "metadata": null
}
```

#### Update Lead Status
```http
PATCH /api/leads/{lead_id}/status?new_status=contacted
Authorization: Bearer <token>
```

**Response (200 OK):**
```json
{
  "lead_id": "880e8400-...",
  "status": "contacted",
  ...
}
```

**Available statuses:** `new`, `contacted`, `qualified`, `converted`, `lost`

#### Get Tenant Leads
```http
GET /api/leads/tenants/{tenant_id}/all?skip=0&limit=100
Authorization: Bearer <token>
```

**Response (200 OK):**
```json
[
  {
    "lead_id": "880e8400-...",
    "name": "John Smith",
    "email": "john@example.com",
    "status": "new",
    "created_at": "2025-12-06T10:45:00Z",
    ...
  }
]
```

---

## 6. Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|:--------:|---------|-------------|
| `GEMINI_API_KEY` | ✅ | - | Google Gemini API key |
| `JWT_SECRET_KEY` | ✅ | - | Secret for JWT signing (min 32 chars) |
| `MONGODB_URI` | ❌ | `mongodb://localhost:27017` | MongoDB connection string |
| `DB_NAME` | ❌ | `aob_bot_db` | Database name |
| `Qdrant_URL` | ❌ | `null` | Qdrant server URL (if remote) |
| `Qdrant_PATH` | ❌ | `data/qdrant_data` | Local Qdrant storage path |
| `LOG_LEVEL` | ❌ | `INFO` | Logging level |
| `LOG_FORMAT` | ❌ | `json` | Log format (`json` or `text`) |
| `RATE_LIMIT_DEFAULT` | ❌ | `100/minute` | Default rate limit |
| `RATE_LIMIT_CHAT` | ❌ | `10/minute` | Chat endpoint rate limit |
| `CORS_ORIGINS_STR` | ❌ | `http://localhost:3000,...` | Comma-separated CORS origins |

---

## 7. Workflows & Use Cases

### Workflow 1: Setting Up a New Client

```bash
# 1. Platform admin logs in
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@platform.com","password":"AdminPass123"}' \
  | jq -r '.access_token')

# 2. Create tenant for client
TENANT=$(curl -s -X POST http://localhost:8000/api/tenants \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"Acme Corporation"}')
TENANT_ID=$(echo $TENANT | jq -r '.tenant_id')

# 3. Create client admin user
curl -X POST http://localhost:8000/api/users \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"email\": \"admin@acme.com\",
    \"password\": \"AcmeAdmin123\",
    \"full_name\": \"Acme Admin\",
    \"role\": \"client_admin\",
    \"tenant_id\": \"$TENANT_ID\"
  }"

# 4. Generate API key for widget
curl -X POST "http://localhost:8000/api/tenants/$TENANT_ID/generate-api-key" \
  -H "Authorization: Bearer $TOKEN"
```

### Workflow 2: Client Uploading Knowledge Base

```bash
# Client admin logs in
CLIENT_TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@acme.com","password":"AcmeAdmin123"}' \
  | jq -r '.access_token')

# Upload PDF
curl -X POST http://localhost:8000/api/documents/ingest \
  -H "Authorization: Bearer $CLIENT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -F "file=@product-manual.pdf"

# Ingest from URL
curl -X POST "http://localhost:8000/api/documents/ingest-url?url=https://acme.com/faq" \
  -H "Authorization: Bearer $CLIENT_TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID"
```

### Workflow 3: Embedding Chat Widget

```html
<!DOCTYPE html>
<html>
<head>
  <title>Acme Support</title>
</head>
<body>
  <div id="chat-container"></div>
  
  <script>
    const API_URL = 'http://localhost:8000';
    const API_KEY = 'aob_xJ9kP2mNqR8vL3dS...';
    const TENANT_ID = 'your-tenant-id';
    let conversationId = null;
    
    async function sendMessage(query) {
      const response = await fetch(`${API_URL}/api/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-API-Key': API_KEY
        },
        body: JSON.stringify({
          query: query,
          tenant_id: TENANT_ID,
          conversation_id: conversationId
        })
      });
      
      const data = await response.json();
      conversationId = data.conversation_id;
      
      // Display answer
      displayMessage('assistant', data.answer);
      
      // Check for lead capture suggestion
      if (data.suggested_actions.includes('capture_lead')) {
        showLeadForm();
      }
    }
    
    async function submitLead(formData) {
      await fetch(`${API_URL}/api/leads`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tenant_id: TENANT_ID,
          conversation_id: conversationId,
          ...formData
        })
      });
    }
  </script>
</body>
</html>
```

---

## 8. Expected Outputs

### Server Startup Logs

```json
{"timestamp": "2025-12-06T08:43:20.057878Z", "level": "INFO", "logger": "app.middleware.exception_handlers", "message": "Exception handlers registered"}
{"timestamp": "2025-12-06T08:43:20.071524Z", "level": "INFO", "logger": "app.main", "message": "Application starting up", "extra": {"version": "2.0.0"}}
```

### Request Logs

```json
{"timestamp": "2025-12-06T08:43:28.830512Z", "level": "INFO", "logger": "app.middleware.request_context", "message": "GET /health", "request_id": "969c6ec3-b3e6-45c5-81bf-5dfde0fab0f2", "extra": {"method": "GET", "path": "/health", "client_ip": "127.0.0.1"}}
{"timestamp": "2025-12-06T08:43:28.831470Z", "level": "INFO", "logger": "app.middleware.request_context", "message": "Completed GET /health - 200", "request_id": "969c6ec3-b3e6-45c5-81bf-5dfde0fab0f2", "extra": {"status_code": 200}}
```

### Error Response Format

```json
{
  "error": {
    "type": "validation_error",
    "message": "Request validation failed",
    "request_id": "abc123-def456-...",
    "details": [
      {
        "field": "body.email",
        "message": "value is not a valid email address",
        "type": "value_error.email"
      }
    ]
  }
}
```

### Rate Limit Response

```json
{
  "error": {
    "type": "rate_limit_exceeded",
    "message": "Too many requests. Please slow down.",
    "request_id": "abc123-...",
    "details": {
      "retry_after": "60 seconds"
    }
  }
}
```

---

## 9. Troubleshooting

### Issue: Server Won't Start

**Check MongoDB:**
```bash
docker ps | grep mongo
# OR
mongosh --eval "db.adminCommand('ping')"
```

**Check Environment:**
```bash
cat .env | grep -E "GEMINI_API_KEY|JWT_SECRET_KEY"
# Both must be set!
```

### Issue: CORS Errors in Browser

**Update `.env`:**
```env
CORS_ORIGINS_STR=http://localhost:3000,https://your-app.com
```

**Restart server after changes.**

### Issue: "Invalid API Key" on Chat

1. Verify API key was generated:
   ```bash
   curl -X POST "http://localhost:8000/api/tenants/$TENANT_ID/generate-api-key" \
     -H "Authorization: Bearer $TOKEN"
   ```

2. Use exact key in `X-API-Key` header

### Issue: Empty/Irrelevant Chat Answers

1. Check documents were ingested for the tenant
2. Verify tenant_id in chat request matches document tenant_id
3. Try simpler questions matching document content

### Issue: Rate Limit Hit

Wait 60 seconds, or adjust `RATE_LIMIT_*` in `.env` and restart.

---

## Support

For issues or questions:
- Check API docs at `http://localhost:8000/docs`
- Review server logs (JSON format for easy parsing)
- Ensure all environment variables are properly set

---

*Generated for AOB Bot v2.0.0*
