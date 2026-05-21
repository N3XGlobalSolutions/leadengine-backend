# Architecture Overview

> **AOB Bot Platform - System Architecture Documentation**  
> Version 2.0.0 | Last Updated: 2025-12-10

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Technology Stack](#technology-stack)
3. [Architecture Patterns](#architecture-patterns)
4. [System Diagrams](#system-diagrams)
5. [Data Flow](#data-flow)
6. [Performance Characteristics](#performance-characteristics)

---

## System Overview

AOB Bot is a **production-ready, multi-tenant SaaS platform** for intelligent customer support chatbots powered by RAG (Retrieval-Augmented Generation).

### Core Capabilities

1. **Multi-Tenancy**: Complete tenant isolation with individual configurations
2. **RAG-Powered Chat**: Context-aware responses using knowledge base
3. **Lead Capture**: Conversational lead qualification and collection
4. **Authentication**: JWT-based auth with role-based access control
5. **High Performance**: Async architecture supporting 1000+ concurrent users
6. **Safety**: Multi-layered LLM guardrails for security and compliance

### Key Metrics

| Metric | Value |
|--------|-------|
| **API Endpoints** | ~30 routes (v1) |
| **Concurrent Users** | Scalable (Stateless) |
| **Database** | DynamoDB (multi-tenant) |
| **Vector DB** | Qdrant (1024-dim embeddings) |
| **LLM** | AWS Bedrock Nova Pro |
| **Deployment** | FastAPI + Uvicorn |

---

## Technology Stack

### Backend Framework
```
FastAPI 0.104+
├── Async ASGI framework
├── Automatic OpenAPI docs
├── Pydantic validation
└── High performance (Uvicorn)
```

### Database Layer
```
DynamoDB (AWS/LocalStack)
├── Key-Value/Document store
├── Single-table design
├── GSI for access patterns
└── Serverless scalability

Qdrant 1.7+
├── Vector similarity search
├── Cosine distance
├── Tenant filtering
└── 1024-dim embeddings
```

### AI/ML Services
```
AWS Bedrock
├── Nova Pro (text generation)
├── Titan v2 (embeddings)
├── Structured outputs
└── Tool calling support

LangChain
├── Text splitting
└── Semantic chunking
```

### Authentication & Security
```
JWT (python-jose)
├── Access tokens (30min)
├── Refresh tokens (7 days)
└── Role-based access

Bcrypt (passlib)
└── Password hashing (cost 12)
```

### Performance & Monitoring
```
SlowAPI
└── Rate limiting

Structured Logging
├── JSON format
├── Request ID tracking
└── Context injection
```

---

## Architecture Patterns

### 1. Domain-Driven Design

Services are organized by business domain:

```
services/
├── auth/          # Authentication domain
├── user/          # User management domain
├── tenant/        # Tenant configuration domain
├── conversation/  # Chat domain
├── lead/          # Lead management domain
├── ai/            # AI/LLM domain
├── analytics/     # Reporting domain
└── storage/       # File storage domain
```

**Benefits**:
- Clear boundaries between domains
- Easy to find related code
- Scalable team structure
- Independent deployment potential

### 2. Unified Service Interface (DynamoDB)

The application uses a unified service layer backed by DynamoDB. Legacy distinction between "Sync (V1)" and "Async (V2)" data services has been consolidated into optimized, high-performance DynamoDB services that support both sync (direct) and async (via executors) usage patterns.

### 3. Repository Pattern

Services encapsulate data access:

```python
# Service layer abstracts database operations
class TenantService:
    def __init__(self):
        self.table = get_dynamodb_table()
    
    def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        # DynamoDB PK access
        return self.table.get_item(Key={"PK": f"TENANT#{tenant_id}", "SK": "METADATA"})
```

**Benefits**:
- Testable (mock the service)
- Swappable database (proven by migration from Mongo to DynamoDB)
- Consistent error handling
- Caching at service layer

### 4. Middleware Pipeline

Request processing pipeline:

```
Request
  ↓
[CORS] → [Request Context] → [Rate Limiter] → [Exception Handlers]
  ↓
Route Handler
  ↓
[Auth Dependencies] → [Permission Checks] → [Business Logic]
  ↓
Response
```

### 5. Multi-Layered Guardrails

LLM safety layers:

```
User Input
  ↓
[Input Rail: Jailbreak Detection]
  ↓
[Input Rail: Harmful Content]
  ↓
[Input Rail: PII Detection]
  ↓
LLM Processing
  ↓
[Output Rail: System Leak Check]
  ↓
[Output Rail: Harmful Output]
  ↓
User Response
```

---

## System Diagrams

### High-Level Architecture

```mermaid
graph TB
    subgraph "Client Layer"
        WebApp[Web Application]
        Widget[Chat Widget]
        Mobile[Mobile App]
    end
    
    subgraph "API Gateway"
        V1[V1 Routes<br/>Sync API]
        V2[V2 Routes<br/>Async API]
    end
    
    subgraph "Service Layer"
        Auth[Auth Service]
        Tenant[Tenant Service]
        Conv[Conversation Service]
        Lead[Lead Service]
        AI[AI Services]
        Analytics[Analytics]
    end
    
    subgraph "Data Layer"
        Mongo[(MongoDB)]
        Qdrant[(Qdrant)]
    end
    
    subgraph "External Services"
        Bedrock[AWS Bedrock]
        S3[AWS S3]
        SQS[AWS SQS]
    end
    
    WebApp --> V1
    Widget --> V1
    Widget --> V2
    Mobile --> V1
    
    V1 --> Auth
    V1 --> Tenant
    V1 --> Conv
    V1 --> Lead
    V1 --> AI
    V1 --> Analytics
    
    V2 --> AI
    
    Auth --> Mongo
    Tenant --> Mongo
    Conv --> Mongo
    Lead --> Mongo
    Analytics --> Mongo
    
    AI --> Mongo
    AI --> Qdrant
    AI --> Bedrock
    
    S3 -.-> SQS
    SQS -.-> Worker[Background Worker]
    Worker --> AI
```

### RAG Flow Diagram

```mermaid
sequenceDiagram
    participant User
    participant API
    participant Guardrails
    participant RAG
    participant Qdrant
    participant LLM
    participant DB
    
    User->>API: Send message
    API->>Guardrails: Check input safety
    
    alt Input unsafe
        Guardrails-->>API: Refusal message
        API-->>User: Polite refusal
    else Input safe
        Guardrails-->>API: Proceed
        API->>RAG: Answer query
        RAG->>LLM: Generate embedding
        LLM-->>RAG: Query vector
        RAG->>Qdrant: Similarity search
        Qdrant-->>RAG: Relevant chunks
        RAG->>LLM: Generate response + context
        LLM-->>RAG: AI response
        RAG->>Guardrails: Check output safety
        
        alt Output unsafe
            Guardrails-->>RAG: Safe fallback
        else Output safe
            Guardrails-->>RAG: Approved
        end
        
        RAG-->>API: Final answer + sources
        API->>DB: Save message
        API-->>User: Response
    end
```

### Auth Flow Diagram

```mermaid
sequenceDiagram
    participant Client
    participant AuthRoute
    participant AuthService
    participant UserService
    participant DB
    
    Client->>AuthRoute: POST /api/auth/login
    AuthRoute->>AuthService: login(email, password)
    AuthService->>UserService: get_user_by_email(email)
    UserService->>DB: Query users collection
    DB-->>UserService: User data
    UserService-->>AuthService: User
    
    AuthService->>AuthService: verify_password()
    
    alt Password valid
        AuthService->>AuthService: create_access_token()
        AuthService->>AuthService: create_refresh_token()
        AuthService-->>AuthRoute: {access_token, refresh_token}
        AuthRoute-->>Client: 200 OK + tokens
    else Password invalid
        AuthService-->>AuthRoute: AuthenticationError
        AuthRoute-->>Client: 401 Unauthorized
    end
```

---

## Data Flow

### Document Ingestion Flow

```
1. Upload PDF/DOCX/TXT
   ↓
2. Parse to text (PyPDF2/python-docx)
   ↓
3. Semantic chunking (LangChain)
   ├─ Chunk size: 1000 chars
   ├─ Overlap: 200 chars
   └─ Smart separators: \n\n, \n, ., space
   ↓
4. Batch embedding generation
   ├─ AWS Bedrock Titan v2
   ├─ Parallel processing
   └─ 1024-dim vectors
   ↓
5. Store in Qdrant
   ├─ Tenant filtering
   ├─ Source metadata
   └─ Cosine distance
   ↓
6. Save metadata to MongoDB
   └─ Document status tracking
```

### Chat Message Flow

```
1. User sends message
   ↓
2. Get/Create conversation (MongoDB)
   ↓
3. Add user message (MongoDB)
   ↓
4. Retrieve conversation history (last 10 messages)
   ↓
5. Get tenant config (cached)
   ↓
6. Input guardrails check
   ↓
7. RAG search (Qdrant)
   ├─ Generate query embedding
   ├─ Similarity search (top 5)
   └─ Return relevant chunks
   ↓
8. Build prompt with context
   ├─ System prompt + guardrails
   ├─ Conversation history
   ├─ Retrieved context
   └─ Lead capture instruction (if needed)
   ↓
9. LLM generation (AWS Bedrock)
   ↓
10. Output guardrails check
   ↓
11. Add assistant message (MongoDB)
   ↓
12. Check lead capture rules
   ├─ Extract entities
   ├─ Track missing fields
   └─ Create lead if complete
   ↓
13. Return response to user
```

### Lead Capture Flow

```
Message 1: "Hello"
  → No lead capture (count < trigger)

Message 2: "Tell me about pricing"
  → No lead capture (count < trigger)

Message 3: "I'm interested, I'm John Doe"
  → Trigger lead capture (count >= 3)
  → Extract: name="John Doe"
  → Missing: email, phone
  → Response: "Great! Could you share your email?"

Message 4: "john@example.com"
  → Extract: email="john@example.com"
  → Missing: phone
  → Response: "And your phone number?"

Message 5: "555-1234"
  → Extract: phone="555-1234"
  → All fields complete!
  → Create Lead in DB
  → Mark conversation.lead_captured = True
  → Response: "Thanks! Our team will reach out."
```

---

## Performance Characteristics

### Sync vs Async Comparison

| Operation | Sync (V1) | Async (V2) | Improvement |
|-----------|-----------|------------|-------------|
| **DB Query** | Blocking | Non-blocking | 100x concurrency |
| **LLM Call** | Blocking | Thread pool | Non-blocking event loop |
| **Qdrant Search** | Blocking | Async client | True parallelism |
| **Max Concurrent** | ~10 | 1000+ | 100x |
| **Request Latency** | 200ms | 150ms | 25% faster |

### Optimization Techniques

1. **Tenant Config Caching**
   - In-memory cache with 5-minute TTL
   - Reduces DB hits on every chat message
   - 100x faster than DB query

2. **Batch Embedding Generation**
   - Generate all embeddings at once
   - Instead of one-at-a-time
   - 10x faster document ingestion

3. **Aggregation Pipelines**
   - Single MongoDB aggregation
   - Instead of multiple queries
   - 6x faster analytics

4. **Connection Pooling**
   - Reuse MongoDB connections
   - Reuse Qdrant connections
   - Reduces connection overhead

5. **Lazy Initialization**
   - Qdrant connects only when needed
   - App starts without external services
   - Graceful degradation

### Scalability Considerations

**Current Capacity** (Single Instance):
- 1000+ concurrent chat requests (V2)
- 100+ documents/minute ingestion
- 10,000+ messages/minute storage

**Horizontal Scaling** (Multiple Instances):
- Stateless API servers
- Shared MongoDB cluster
- Shared Qdrant cluster
- Load balancer in front

**Bottlenecks** (What to scale first):
1. **MongoDB**: Shard by tenant_id
2. **Qdrant**: Add replicas for read throughput
3. **LLM calls**: Increase Bedrock quotas
4. **API servers**: Add more instances

---

## Security Architecture

### Authentication Flow

```
User Login
  ↓
[Password Hashing: Bcrypt cost 12]
  ↓
[JWT Generation: HS256]
  ↓
[Access Token: 30min expiry]
  ↓
[Refresh Token: 7 day expiry]
```

### Authorization Model

```
RBAC (Role-Based Access Control)
├── platform_admin (Full access)
├── aob_admin (All tenants)
├── client_admin (Own tenant + users)
└── client_agent (Own tenant read-only)
```

### Data Isolation

```
Multi-Tenant Isolation
├── MongoDB queries filtered by tenant_id
├── Qdrant vectors filtered by tenant_id
├── API keys unique per tenant
└── Users scoped to tenant
```

### API Security

```
Defense Layers
├── Rate limiting (SlowAPI)
├── CORS configuration
├── Input validation (Pydantic)
├── LLM guardrails
├── Request ID tracking
└── Structured logging
```

---

## Next Steps

Continue to detailed documentation:
- [02_CORE_INFRASTRUCTURE.md](./02_CORE_INFRASTRUCTURE.md) - Configuration and database setup
- [03_MODELS.md](./03_MODELS.md) - Data models explained
- [10_SERVICES_AI.md](./10_SERVICES_AI.md) - AI services deep dive

---

*Architecture documentation complete. See other docs for implementation details.*
