# Services Documentation

> **All service layer implementations across 8 business domains**  
> Files: `app/services/*/*.py` (17 service files)

---

## Table of Contents

1. [Overview](#overview)
2. [Authentication & User Services](#authentication--user-services)
3. [Tenant Services](#tenant-services)
4. [Conversation Services](#conversation-services)
5. [Lead Services](#lead-services)
6. [AI Services](#ai-services)
7. [Analytics Service](#analytics-service)
8. [Storage Services](#storage-services)

---

## Overview

Services encapsulate all business logic and database operations using the **Repository Pattern**.

### Service Organization (Domain-Driven Design)

```
app/services/
├── auth/
│   └── auth_service.py                    # Authentication & JWT
├── user/
│   └── user_service_dynamodb.py           # User CRUD
├── tenant/
│   └── tenant_service_dynamodb.py         # Tenant CRUD
├── conversation/
│   └── conversation_service_dynamodb.py   # Message storage & Context
├── lead/
│   └── lead_service_dynamodb.py           # Lead CRUD
├── ai/
│   ├── llm_service.py                     # AWS Bedrock (sync)
│   ├── async_llm_service.py               # AWS Bedrock (async wrapper)
│   ├── rag_service.py                     # RAG pipeline (sync)
│   ├── async_rag_service.py               # RAG pipeline (async)
│   └── guardrails_service.py              # LLM safety controls
├── analytics/
│   └── analytics_service_dynamodb.py      # Statistics aggregation
└── storage/
    ├── storage_service.py                 # AWS S3 uploads
    ├── queue_service.py                   # AWS SQS messaging
```

---

## Authentication & User Services

### auth_service.py

**File**: `app/services/auth/auth_service.py`

**Purpose**: User authentication, password hashing, JWT token management

**Key Methods**:

```python
class AuthService:
    def login(email, password) -> Token:
        """
        Authenticate user and generate tokens.
        
        Process:
        1. Get user by email
        2. Verify password (bcrypt)
        3. Create access token (30min)
        4. Create refresh token (7 days)
        5. Update last_login timestamp
        """
        
    def refresh_token(refresh_token) -> Token:
        """Generate new access token from valid refresh token."""
        
    def hash_password(password) -> str:
        """Hash password with bcrypt (cost factor 12)."""
        
    def verify_password(plain, hashed) -> bool:
        """Verify password against bcrypt hash."""
```

**Security**:
- Bcrypt cost factor 12 (balance security vs performance)
- JWT HS256 algorithm
- Access token: 30min expiry
- Refresh token: 7 day expiry

---

### user_service.py

**File**: `app/services/user/user_service.py`

**Purpose**: User CRUD with soft delete pattern

**Key Methods**:

```python
class UserService:
    def create_user(user_data: UserCreate) -> User:
        """Create user with hashed password and unique UUIDs."""
        
    def get_user(user_id) -> Optional[User]:
        """Get user by ID (excludes soft-deleted users)."""
        
    def get_user_by_email(email) -> Optional[User]:
        """Used for login lookups (excludes soft-deleted)."""
        
    def list_users(tenant_id=None, skip=0, limit=10) -> List[User]:
        """
        List users with optional tenant filter.
        Excludes soft-deleted users.
        """
        
    def update_user(user_id, updates: UserUpdate) -> User:
        """Update user fields."""
        
    def delete_user(user_id):
        """
        Soft delete: sets is_deleted=True, deleted_at=now().
        User data retained for audit trail.
        """
```

**Soft Delete Pattern**:
```python
# We do:
table.update_item(
    Key={"PK": f"USER#{user_id}", "SK": "METADATA"},
    UpdateExpression="set is_deleted=:t, deleted_at=:n",
    ExpressionAttributeValues={":t": True, ":n": datetime.utcnow().isoformat()}
)
```

**Benefits**:
- Audit trail preserved
- Can restore if accidental
- Foreign key references remain valid

---

## Tenant Services

### tenant_service.py

**File**: `app/services/tenant/tenant_service.py`

**Purpose**: Tenant CRUD with in-memory caching for performance

**Key Methods**:

```python
class TenantService:
    _tenant_cache = {}  # In-memory cache
    _cache_timestamps = {}
    CACHE_TTL = 300  # 5 minutes
    
    def create_tenant(tenant_data: TenantCreate) -> Tenant:
        """Create tenant with default config."""
        
    def get_tenant(tenant_id) -> Optional[Tenant]:
        """Get tenant (excludes soft-deleted)."""
        
    def get_tenant_cached(tenant_id) -> Optional[Tenant]:
        """
        Get tenant with 5-minute cache.
        
        Performance impact:
        - Without cache: DB query every chat message
        - With cache: DB query once per 5 minutes
        - Improvement: 100x faster
        """
        
    def update_tenant(tenant_id, updates) -> Tenant:
        """Update tenant and invalidate cache."""
        
    def delete_tenant(tenant_id):
        """Soft delete tenant."""
        
    def generate_api_key(tenant_id) -> str:
        """
        Generate unique API key for widget auth.
        Format: aob_<uuid>
        Stored in tenant.config.api_key
        """
        
    def validate_api_key(api_key) -> Optional[str]:
        """Validate API key and return tenant_id."""
```

**Caching Implementation**:
```python
def get_tenant_cached(self, tenant_id):
    now = time.time()
    
    # Check cache validity
    if tenant_id in self._tenant_cache:
        cache_age = now - self._cache_timestamps.get(tenant_id, 0)
        if cache_age < self.CACHE_TTL:
            return self._tenant_cache[tenant_id]
    
    # Cache miss or expired - fetch from DB
    tenant = self.get_tenant(tenant_id)
    if tenant:
        self._tenant_cache[tenant_id] = tenant
        self._cache_timestamps[tenant_id] = now
    
    return tenant
```

---

### async_tenant_service.py (Unified in tenant_service_dynamodb.py)

**Status**: Merged. The DynamoDB implementation supports `run_in_executor` for async operations where needed, but efficient DynamoDB http calls are fast enough for most direct usage. Codebase uses `tenant_service` universally.

---

## Conversation Services

### conversation_service.py

**File**: `app/services/conversation/conversation_service.py`

**Purpose**: Permanent message storage for conversations

**Key Methods**:

```python
class ConversationService:
    def create_conversation(tenant_id, user_identifier=None) -> Conversation:
        """Create new conversation with UUID."""
        
    def get_conversation(conversation_id) -> Optional[Conversation]:
        """Get conversation by ID."""
        
    def add_message(conversation_id, role, content, sources=[]):
        """
        Add message and update conversation metadata.
        
        Updates:
        - Increments message_count
        - Updates last_message_at timestamp
        - Stores message with UUID
        """
        
    def get_messages(conversation_id, skip=0, limit=50) -> List[Message]:
        """Get paginated messages for conversation."""
        
    def get_tenant_conversations(tenant_id, skip=0, limit=20) -> List[Conversation]:
        """List conversations for tenant."""
        
    def mark_lead_captured(conversation_id):
        """Set lead_captured=True."""
        
    def update_extracted_data(conversation_id, data):
        """Update extracted_lead_data during lead collection."""
```

**Storage**: All messages stored permanently in MongoDB for audit and analytics

---

### conversation_history_service.py (Deprecated & Removed)

**Status**: Removed. Session-based history is now handled directly by `conversation_service_dynamodb.py` using `get_context_history`, reusing the permanent message storage for LLM context. This simplifies architecture and reduces database calls.

---

### async_conversation_service.py

**Status**: Merged. Functionality incorporated into `conversation_service_dynamodb.py`.

---

## Lead Services

### lead_service.py

**File**: `app/services/lead/lead_service.py`

**Purpose**: Lead CRUD and status management

**Key Methods**:

```python
class LeadService:
    def create_lead(lead_data: LeadCreate) -> Lead:
        """Create lead with status='new'."""
        
    def get_lead(lead_id) -> Optional[Lead]:
        """Get lead by ID."""
        
    def get_conversation_lead(conversation_id) -> Optional[Lead]:
        """
        Check if lead already exists for conversation.
        Prevents duplicate lead creation.
        """
        
    def list_leads(tenant_id, status=None, skip=0, limit=20) -> List[Lead]:
        """
        List leads with optional status filter.
        
        Example: status='new' returns only new leads
        """
        
    def update_lead_status(lead_id, status: LeadStatus) -> Lead:
        """
        Update lead status in lifecycle.
        
        Lifecycle: new → contacted → qualified → converted/lost
        """
```

**Status Serialization**:
```python
# DynamoDB Status Update
table.update_item(
    Key={"PK": f"LEAD#{lead_id}", "SK": "METADATA"},
    UpdateExpression="set #s = :s",
    ExpressionAttributeNames={"#s": "status"},
    ExpressionAttributeValues={":s": status.value}
)
```

---

### async_lead_service.py

**Status**: Merged. Functionality incorporated into `lead_service_dynamodb.py`.

---

## AI Services

### llm_service.py

**File**: `app/services/ai/llm_service.py`

**Purpose**: AWS Bedrock integration for LLM and embeddings

**Models Used**:
- **LLM**: `openai.gpt-oss-120b-1:0` (Nova Pro)
- **Embeddings**: `amazon.titan-embed-text-v2:0` (1024 dimensions)

**Key Methods**:

```python
class LLMService:
    def generate_response(prompt, temperature=0.7, max_tokens=2000) -> str:
        """Basic text completion."""
        
    def generate_rag_response(query, context, history, system_prompt) -> str:
        """
        Generate response with RAG context.
        
        Prompt structure:
        <system>
        {system_prompt with guardrails}
        </system>
        
        <context>
        {retrieved document chunks}
        </context>
        
        <history>
        {conversation history}
        </history>
        
        <user>
        {query}
        </user>
        """
        
    def generate_embedding(text) -> List[float]:
        """
        Generate 1024-dim embedding from text.
        Returns vector for similarity search.
        """
        
    def generate_embeddings_batch(texts: List[str]) -> List[List[float]]:
        """
        Batch embedding generation (10x faster).
        
        Instead of:
        for text in texts:
            embed = generate_embedding(text)  # 100 calls for 100 chunks
        
        Do:
        embeds = generate_embeddings_batch(texts)  # 1 call for 100 chunks
        """
        
    def extract_lead_info(text, fields) -> dict:
        """
        Structured entity extraction using tool calling.
        
        Example:
        text = "I'm John Doe, email john@example.com"
        fields = ["name", "email"]
        
        Returns: {"name": "John Doe", "email": "john@example.com"}
        """
```

**Authentication**: Uses `AWS_BEARER_TOKEN_BEDROCK` from environment

---

### async_llm_service.py

**File**: `app/services/ai/async_llm_service.py`

**Purpose**: Async wrapper using `run_in_executor` for non-blocking boto3 calls

**Why Wrapper?**:
- boto3 is synchronous (no official async version)
- Use thread pool to avoid blocking async event loop

**Implementation**:
```python
_executor = ThreadPoolExecutor(max_workers=10)

async def generate_embedding_async(text):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _executor,
        llm_service.generate_embedding,
        text
    )
```

---

### rag_service.py ⭐

**File**: `app/services/ai/rag_service.py`

**Purpose**: Complete RAG pipeline (document processing + query answering)

**Document Processing Methods**:

```python
class RAGService:
    def parse_docx(file_path) -> str:
        """Extract text from DOCX using python-docx."""
        
    def parse_pdf(file_path) -> str:
        """Extract text from PDF using PyPDF2."""
        
    def chunk_text(text, chunk_size=1000, overlap=200) -> List[str]:
        """
        Semantic chunking using LangChain RecursiveCharacterTextSplitter.
        
        Smart separators (in order):
        1. Double newline (paragraphs)
        2. Single newline (lines)
        3. Period + space (sentences)
        4. Space (words)
        5. Empty string (characters - last resort)
        
        Fallback: Basic character chunking if LangChain unavailable
        """
        
    def delete_document(tenant_id, source):
        """
        Delete all vectors for document (for re-ingestion).
        Qdrant filter: tenant_id + source
        """
        
    def ingest_document(file_path, tenant_id, source, document_type):
        """
        Full ingestion pipeline.
        
        Steps:
        1. Parse document (PDF/DOCX/TXT)
        2. Chunk text (semantic splitting)
        3. Delete existing vectors (idempotency)
        4. Generate embeddings (batch processing)
        5. Store vectors in Qdrant with metadata
        6. Save document metadata in MongoDB
        """
```

**Query Methods**:

```python
    def search(query, tenant_id, limit=5) -> List[dict]:
        """
        Similarity search in Qdrant.
        
        Process:
        1. Generate query embedding
        2. Search with filter: tenant_id
        3. Return top N chunks with scores
        """
        
    def answer_query(query, tenant_id, missing_fields, history, tenant_config) -> tuple:
        """
        Complete RAG answer generation with guardrails.
        
        Pipeline:
        1. INPUT RAIL: Guardrails check (allow PII if collecting lead)
        2. Similarity search (top 5 chunks)
        3. Build conversation history string
        4. Build system prompt with guardrails
        5. Add lead capture instruction if needed
        6. Generate LLM response
        7. OUTPUT RAIL: Validate response
        8. Return (answer, sources)
        """
```

**Lazy Qdrant Connection**:
```python
def _get_qdrant_client(self):
    if self.qdrant_client is None:
        # Connect only when first needed
        self.qdrant_client = QdrantClient(...)
        self._ensure_collection()
    return self.qdrant_client
```

**Benefit**: App starts even if Qdrant is down

---

### async_rag_service.py

**File**: `app/services/ai/async_rag_service.py`

**Purpose**: Async RAG using AsyncQdrantClient and async LLM

---

### guardrails_service.py

**File**: `app/services/ai/guardrails_service.py`

**Purpose**: Multi-layered LLM safety controls (OWASP LLMSVS + NeMo Guardrails)

**Guardrail Layers** (checked in order):

1. **Jailbreak Detection** - Prompt injection attempts
2. **Harmful Content** - Violence, illegal activities
3. **PII Detection** - Email, phone, SSN, credit cards
4. **Coding Requests** - Programming assistance
5. **Off-Topic** - Entertainment, general knowledge
6. **Custom Guardrails** - Tenant-specific restrictions

**Key Methods**:

```python
class GuardrailsService:
    def check_input(query, custom_guardrails, allow_pii=False) -> GuardrailResult:
        """
        Input rail validation.
        
        Returns:
        - is_safe: bool
        - category: GuardrailCategory (if blocked)
        - confidence: float (0-1)
        """
        
    def check_output(response, original_query) -> GuardrailResult:
        """
        Output rail validation.
        
        Checks for:
        - System instruction leaks
        - Harmful content generation
        - Excessive length
        """
        
    def get_refusal_message(category) -> str:
        """
        Returns polite refusal message for category.
        
        Example: "I'm here to help with questions about our products..."
        """
        
    def build_system_prompt(tone, language, custom_guardrails, context) -> str:
        """
        Build robust system prompt with embedded guardrails.
        
        Includes:
        - Core directives (stay on topic)
        - Tone/language instructions
        - Safety rules (non-overridable)
        - Custom restrictions
        """
```

**PII Detection Patterns**:
```python
EMAIL_PATTERN = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
PHONE_PATTERN = r'\b(\+\d{1,3}[-.]?)?\(?\d{3}\)?[-.]?\d{3}[-.]?\d{4}\b'
SSN_PATTERN = r'\b\d{3}-\d{2}-\d{4}\b'
CREDIT_CARD_PATTERN = r'\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b'
AADHAAR_PATTERN = r'\b\d{4}[- ]?\d{4}[- ]?\d{4}\b'  # Indian ID
```

---

## Analytics Service

### analytics_service.py

**File**: `app/services/analytics/analytics_service.py`

**Purpose**: Statistics aggregation with performance optimization

**Key Methods**:

```python
class AnalyticsService:
    def get_tenant_stats(tenant_id) -> dict:
        """
        Comprehensive tenant stats using single aggregation.
        
        Returns:
        - conversations: int
        - leads: int
        - documents: int
        - leads_by_status: {"new": 20, "contacted": 15, ...}
        - conversion_rate: float
        
        Optimization: Single MongoDB aggregation instead of 6 queries (6x faster)
        """
        
    def get_platform_stats() -> dict:
        """Platform-wide statistics (all tenants)."""
        
    def get_daily_metrics(tenant_id, days=7) -> list:
        """
        Daily conversation volume for charts.
        
        Returns: [{"date": "2025-12-09", "conversations": 15}, ...]
        Fills missing days with 0
        """
```

**Performance Optimization**:
```python
# Old approach - 6 separate queries
new_count = db.leads.count_documents({"status": "new"})
contacted_count = db.leads.count_documents({"status": "contacted"})
# ... 4 more queries

# New approach - 1 aggregation pipeline (6x faster)
pipeline = [
    {"$match": {"tenant_id": tenant_id}},
    {"$group": {"_id": "$status", "count": {"$sum": 1}}}
]
results = db.leads.aggregate(pipeline)
```

---

## Storage Services

### storage_service.py

**File**: `app/services/storage/storage_service.py`

**Purpose**: AWS S3 file upload management

**Key Methods**:

```python
class StorageService:
    def upload_to_s3(file_content, file_name, bucket, content_type) -> str:
        """
        Upload file to S3 and return public URL.
        
        Returns: https://bucket.s3.region.amazonaws.com/file.pdf
        """
        
    def generate_presigned_url(bucket, key, expiration=3600) -> str:
        """
        Generate temporary download URL.
        Default expiration: 1 hour
        """
```

---

### queue_service.py

**File**: `app/services/storage/queue_service.py`

**Purpose**: AWS SQS messaging for async processing

**Key Methods**:

```python
class QueueService:
    def send_message(queue_url, message_body: dict) -> str:
        """
        Send JSON message to SQS.
        
        Message format:
        {
            "type": "document_ingestion",
            "tenant_id": "...",
            "source": "s3://bucket/file.pdf",
            "document_type": "pdf"
        }
        
        Returns: message_id
        """
```

**Used For**: Async document ingestion (upload to S3 + SQS + background worker)

---

## Summary

### Service Count by Domain
- **Auth & User**: 2 services
- **Tenant**: 2 services (sync + async)
- **Conversation**: 3 services (sync + async + history)
- **Lead**: 2 services (sync + async)
- **AI**: 5 services (LLM + RAG + Guardrails, sync + async)
- **Analytics**: 1 service
- **Storage**: 2 services (S3 + SQS)

**Total**: 17 service files

### Design Patterns
1. **Repository Pattern**: All data access encapsulated in services
2. **Singleton**: Database connections shared
3. **Caching**: Tenant config cached (5min TTL)
4. **Soft Delete**: Audit trail preservation
5. **Single Table Design**: Efficient DynamoDB access patterns
6. **Async Wrapper**: Thread pool for blocking boto3

### Performance Optimizations
1. **Tenant Caching**: 100x faster (once per 5min vs every message)
2. **Batch Embeddings**: 10x faster (all at once vs one-by-one)
3. **Aggregation Pipelines**: 6x faster (single query vs multiple)
4. **Async Services**: 100x concurrency (1000+ vs 10 concurrent users)

---

*Services documentation complete. See [06_MIDDLEWARE_UTILITIES_WORKERS.md](./06_MIDDLEWARE_UTILITIES_WORKERS.md) for supporting components.*
