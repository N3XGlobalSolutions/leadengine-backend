# Data Models

> **Pydantic Models for Data Validation and Serialization**  
> Files: `app/models/*.py` (6 files)

---

## Table of Contents

1. [Overview](#overview)
2. [user.py - User & Authentication Models](#userpy)
3. [tenant.py - Multi-Tenant Configuration](#tenantpy)
4. [conversation.py - Chat Models](#conversationpy)
5. [lead.py - Lead Management](#leadpy)
6. [document.py - Document Metadata](#documentpy)

---

## Overview

All models use **Pydantic** for:
1. **Type Validation**: Automatic validation at runtime
2. **JSON Serialization**: Easy API request/response handling
3. **Documentation**: Self-documenting with type hints
4. **IDE Support**: Autocomplete and type checking

### Why Pydantic?

```python
# Traditional dict - NO type safety
user = {\"email\": \"invalid-email\"}  # Silently accepts bad data

# Pydantic - AUTOMATIC validation
user = UserCreate(email=\"invalid-email\")  # Raises ValidationError!
```

---

## user.py

**File**: `app/models/user.py` (86 lines)

### Purpose

Models for user authentication, authorization, and role-based access control (RBAC).

### Models Defined

1. `UserRole` - Enum for access levels
2. `UserBase` - Common user fields
3. `UserCreate` - Create user request
4. `UserUpdate` - Update user request
5. `User` - Complete user (with password hash)
6. `UserResponse` - API response (without password)
7. `UserLogin` - Login request
8. `Token` - JWT token response
9. `TokenData` - JWT payload data

---

### UserRole Enum

```python
class UserRole(str, Enum):
    PLATFORM_ADMIN = \"platform_admin\"
    AOB_ADMIN = \"aob_admin\"
    CLIENT_ADMIN = \"client_admin\"
    CLIENT_AGENT = \"client_agent\"
```

**Access Levels Explained**:

| Role | Scope | Permissions |
|------|-------|-------------|
| `PLATFORM_ADMIN` | **All tenants** | Full platform access, create/delete tenants |
| `AOB_ADMIN` | **All tenants** | Manage all tenants, view all data |
| `CLIENT_ADMIN` | **Own tenant** | Manage tenant config, create users, generate API keys |
| `CLIENT_AGENT` | **Own tenant** | View conversations, leads (read-only) |

**Why `str, Enum`?**:
```python
class UserRole(str, Enum):  # Inherits from both!
```
- **`str`**: JSON serialization works automatically
- **`Enum`**: Type-safe enum validation

**Without `str`**:
```python
class UserRole(Enum):
    ADMIN = \"admin\"

json.dumps({\"role\": UserRole.ADMIN})  # ERROR: not JSON serializable
```

**With `str`**:
```python
class UserRole(str, Enum):
    ADMIN = \"admin\"

json.dumps({\"role\": UserRole.ADMIN})  # ✓ Works! Outputs \"admin\"
```

---

### UserBase Model

```python
class UserBase(BaseModel):
    email: EmailStr  # Pydantic validates email format!
    full_name: str
    role: UserRole = UserRole.CLIENT_AGENT  # Default role
    tenant_id: Optional[str] = None  # None for platform admins
```

**Field Breakdown**:

**`email: EmailStr`**:
```python
UserBase(email=\"test@example.com\")  # ✓ Valid
UserBase(email=\"not-an-email\")      # ✗ ValidationError
```
- **EmailStr**: Pydantic type that validates email format
- Automatically rejects invalid emails

**`tenant_id: Optional[str] = None`**:
- **Platform admins**: `tenant_id = None` (not scoped to tenant)
- **All other roles**: `tenant_id` required

**Why None for platform_admin?**:
```python
# Platform admin - access ALL tenants
user = User(role=UserRole.PLATFORM_ADMIN, tenant_id=None)

# Client admin - access ONLY their tenant
user = User(role=UserRole.CLIENT_ADMIN, tenant_id=\"tenant-123\")
```

---

### UserCreate Model

```python
class UserCreate(UserBase):
    password: str = Field(..., min_length=8)
```

**Inheritance Pattern**:
```python
UserCreate → UserBase
```
Inherits: `email`, `full_name`, `role`, `tenant_id`  
Adds: `password`

**Password Validation**:
```python
password: str = Field(..., min_length=8)
```
- `...`: Required field (no default)
- `min_length=8`: Minimum 8 characters

**Usage**:
```python
# ✓ Valid
user = UserCreate(
    email=\"user@example.com\",
    password=\"SecurePass123\",
    full_name=\"John Doe\",
    role=UserRole.CLIENT_ADMIN,
    tenant_id=\"tenant-xyz\"
)

# ✗ Invalid - password too short
user = UserCreate(password=\"short\")  # ValidationError: min_length=8
```

---

### User Model (Complete)

```python
class User(UserBase):
    user_id: str                                         # UUID
    hashed_password: str                                 # Bcrypt hash
    created_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = True
    is_deleted: bool = False                             # Soft delete
    deleted_at: Optional[datetime] = None
    last_login: Optional[datetime] = None
```

**Why `hashed_password` not `password`?**:
- **Security**: Never store plaintext passwords
- **Naming**: Clear that it's hashed, not raw

**Soft Delete Pattern**:
```python
is_deleted: bool = False
deleted_at: Optional[datetime] = None
```

**How it works**:
```python
# Normal user
user.is_deleted = False
user.deleted_at = None

# Deleted user (soft delete)
user.is_deleted = True
user.deleted_at = datetime.utcnow()
```

**Why soft delete?**:
1. **Audit trail**: Know who deleted what and when
2. **Recovery**: Can restore if accidental
3. **Data integrity**: Foreign key references still valid

**Used in queries**:
```python
# Get active users only
db.users.find({\"is_deleted\": False})
```

**`default_factory` Explained**:
```python
created_at: datetime = Field(default_factory=datetime.utcnow)
```

**Why not `default`?**:
```python
# WRONG - Same timestamp for ALL users!
created_at: datetime = datetime.utcnow()  # Evaluated once at import

# RIGHT - New timestamp for EACH user
created_at: datetime = Field(default_factory=datetime.utcnow)  # Evaluated per instance
```

---

### UserResponse Model

```python
class UserResponse(UserBase):
    user_id: str
    created_at: datetime
    is_active: bool
    last_login: Optional[datetime] = None
```

**Why separate from `User`?**:
- **Security**: Excludes `hashed_password` from API responses
- **Clean API**: Only expose necessary fields

**Usage**:
```python
# Internal - full user with password hash
user = User(..., hashed_password=\"bcrypt_hash\")

# External API - no password
response = UserResponse(**user.dict())
return response  # No password in JSON!
```

---

### UserLogin Model

```python
class UserLogin(BaseModel):
    email: EmailStr
    password: str
```

**Purpose**: Login endpoint request body

**Usage**:
```python
@app.post(\"/api/auth/login\")
async def login(credentials: UserLogin):
    # Pydantic validates email format automatically
    user = get_user_by_email(credentials.email)
    if verify_password(credentials.password, user.hashed_password):
        return create_tokens(user)
```

---

### Token Models

```python
class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = \"bearer\"

class TokenData(BaseModel):
    user_id: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    tenant_id: Optional[str] = None
```

**Token Usage**:
```python
# Login response
{
    \"access_token\": \"eyJ...\",
    \"refresh_token\": \"eyJ...\",
    \"token_type\": \"bearer\"
}
```

**TokenData Usage**:
```python
# Decode JWT payload
payload = jwt.decode(token, secret)
token_data = TokenData(**payload)

# Use for auth
if token_data.role == \"platform_admin\":
    # Allow access
```

---

### Where Used

**Services**:
- `app/services/user/user_service.py` - User CRUD
- `app/services/auth/auth_service.py` - Login, tokens

**Routes**:
- `app/routes/v1/auth.py` - Registration, login
- `app/routes/v1/users.py` - User management

---

## tenant.py

**File**: `app/models/tenant.py` (49 lines)

### Purpose

Multi-tenant configuration and settings models.

### Models Defined

1. `TenantConfig` - Tenant configuration settings
2. `Tenant` - Complete tenant model
3. `TenantCreate` - Create tenant request
4. `TenantUpdate` - Update tenant request

---

### TenantConfig Model

```python
class TenantConfig(BaseModel):
    branding: Optional[Dict[str, Any]] = None  # Logo, colors
    welcome_message: str = \"Hello! How can I help you today?\"
    lead_form_enabled: bool = True
    lead_form_fields: List[str] = [\"name\", \"email\", \"phone\"]
    language: str = \"en\"
    tone: str = \"professional\"  # professional, friendly, casual
    guardrails: List[str] = []  # Topics to avoid
    max_conversation_length: int = 50
    lead_trigger_count: int = 3  # Messages before lead capture
    api_key: Optional[str] = None  # Widget authentication
```

**Field-by-Field Explanation**:

**`branding: Optional[Dict[str, Any]] = None`**:
```python
branding = {
    \"logo_url\": \"https://example.com/logo.png\",
    \"primary_color\": \"#007bff\",
    \"secondary_color\": \"#6c757d\",
    \"font_family\": \"Inter\"
}
```
- **Flexible dict**: Any branding properties
- **Optional**: Not all tenants customize branding

**`welcome_message: str`**:
```python
welcome_message = \"Hi there! I'm here to help. What can I do for you?\"
```
- **First message**: Widget shows this on load
- **Customizable**: Each tenant has unique greeting

**`lead_form_fields: List[str]`**:
```python
lead_form_fields = [\"name\", \"email\", \"phone\"]  # Default
lead_form_fields = [\"name\", \"email\", \"phone\", \"company\"]  # Custom
```
- **Defines fields to collect**: AI asks for these fields
- **Order matters**: Asks in order listed

**`language: str = \"en\"`**:
```python
language = \"en\"  # English
language = \"es\"  # Spanish
language = \"fr\"  # French
```
- **AI response language**: LLM responds in this language
- **ISO 639-1 codes**: Standard language codes

**`tone: str = \"professional\"`**:
```python
tone = \"professional\"  # Formal, business-like
tone = \"friendly\"      # Warm, casual
tone = \"casual\"        # Very relaxed
```
- **LLM personality**: Affects response style
- **System prompt**: Injected into LLM instructions

**`guardrails: List[str] = []`**:
```python
guardrails = [\"pricing\", \"competitors\", \"legal advice\"]
```
- **Blocked topics**: AI refuses to discuss these
- **Custom per tenant**: Each tenant defines restrictions

**`lead_trigger_count: int = 3`**:
```python
# After 3 messages, start lead capture
lead_trigger_count = 3

# Immediate lead capture
lead_trigger_count = 1

# Never capture (disabled)
lead_trigger_count = 999
```
- **Trigger point**: When to start asking for lead info
- **Configurable**: Balance between UX and lead gen

**`api_key: Optional[str] = None`**:
```python
api_key = \"aob_a1b2c3d4e5f6...\"
```
- **Widget authentication**: Secure widget access
- **Generated**: Created via `/generate-api-key` endpoint
- **Format**: `aob_<uuid>`

---

### Tenant Model

```python
class Tenant(BaseModel):
    tenant_id: str
    name: str
    config: TenantConfig = Field(default_factory=TenantConfig)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = True
    is_deleted: bool = False  # Soft delete
    deleted_at: Optional[datetime] = None
```

**`config: TenantConfig = Field(default_factory=TenantConfig)`**:
```python
# Creates fresh TenantConfig with defaults for each tenant
tenant = Tenant(tenant_id=\"123\", name=\"Acme Corp\")
tenant.config.welcome_message  # \"Hello! How can I help you today?\"
```

**Soft Delete** (same pattern as User):
```python
# Active tenant
tenant.is_deleted = False

# Deleted tenant
tenant.is_deleted = True
tenant.deleted_at = datetime.utcnow()
```

---

### Where Used

**Services**:
- `app/services/tenant/tenant_service.py` - Tenant CRUD, caching
- `app/services/tenant/async_tenant_service.py` - Async version

**Routes**:
- `app/routes/v1/tenants.py` - Tenant management
- `app/routes/v1/chat.py` - Fetches config for chat

**How Config Is Used**:
```python
# In chat route
tenant = tenant_service.get_tenant_cached(tenant_id)
config = tenant.config

# Pass to RAG
answer = rag_service.answer_query(
    query=query,
    tone=config.tone,
    language=config.language,
    guardrails=config.guardrails,
    ...
)
```

---

## conversation.py

**File**: `app/models/conversation.py` (54 lines)

### Purpose

Models for chat conversations and messages.

### Models Defined

1. `Message` - Individual chat message
2. `Conversation` - Conversation metadata
3. `ChatRequest` - API request for chat
4. `ChatResponse` - API response from chat

---

### Message Model

```python
class Message(BaseModel):
    message_id: str                                      # UUID
    conversation_id: str                                 # Foreign key
    role: str                                            # \"user\" or \"assistant\"
    content: str                                         # Message text
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    sources: List[str] = []                              # RAG sources
```

**`role: str`**:
```python
role = \"user\"       # Message from user
role = \"assistant\"  # Message from AI
```

**`sources: List[str]`**:
```python
# For assistant messages from RAG
sources = [
    \"product_manual.pdf\",
    \"faq_document.docx\"
]

# For user messages
sources = []  # Empty
```

**Message Storage**:
```python
# MongoDB storage
{
    \"message_id\": \"msg_abc123\",
    \"conversation_id\": \"conv_xyz789\",
    \"role\": \"user\",
    \"content\": \"How do I reset my password?\",
    \"timestamp\": \"2025-12-10T10:30:00Z\",
    \"sources\": []
}
```

---

### Conversation Model

```python
class Conversation(BaseModel):
    conversation_id: str
    tenant_id: str                                       # Multi-tenant isolation
    user_identifier: Optional[str] = None                # Email/phone/anon ID
    started_at: datetime = Field(default_factory=datetime.utcnow)
    last_message_at: datetime = Field(default_factory=datetime.utcnow)
    message_count: int = 0
    is_active: bool = True
    lead_captured: bool = False
    extracted_lead_data: Dict[str, str] = {}             # Partial lead info
    lead_status: str = \"collecting\"                     # \"collecting\", \"completed\"
```

**`user_identifier: Optional[str]`**:
```python
# Authenticated user
user_identifier = \"user@example.com\"

# Widget user (provided phone)
user_identifier = \"+1-555-1234\"

# Anonymous
user_identifier = None
```

**`message_count: int = 0`**:
- **Incremented**: On each message
- **Used for**: Lead trigger (`if count >= 3`)

**Lead Capture Fields**:
```python
lead_captured: bool = False                    # Lead created?
extracted_lead_data: Dict[str, str] = {}       # Partial data during collection
lead_status: str = \"collecting\"                # \"collecting\" or \"completed\"
```

**Lead Capture Flow**:
```python
# Message 1-2: No lead capture
conversation.message_count = 2
conversation.lead_captured = False
conversation.extracted_lead_data = {}

# Message 3: Trigger lead capture
conversation.message_count = 3
conversation.extracted_lead_data = {\"name\": \"John\"}  # Extracted
conversation.lead_status = \"collecting\"

# Message 4: More data collected
conversation.extracted_lead_data = {\"name\": \"John\", \"email\": \"john@example.com\"}

# Message 5: All data collected
conversation.lead_captured = True
conversation.lead_status = \"completed\"
# Lead created in database
```

---

### ChatRequest / ChatResponse

```python
class ChatRequest(BaseModel):
    query: str
    tenant_id: str
    conversation_id: Optional[str] = None  # Create new if None
    user_identifier: Optional[str] = None
    api_key: Optional[str] = None  # Widget auth

class ChatResponse(BaseModel):
    answer: str
    conversation_id: str
    message_id: str
    sources: List[str] = []
    suggested_actions: List[str] = []  # e.g., [\"capture_lead\"]
```

**Usage**:
```python
# Request
POST /api/chat
{
    \"query\": \"What are your pricing plans?\",
    \"tenant_id\": \"tenant-123\",
    \"conversation_id\": \"conv-abc\"  // Optional - continue existing
}

# Response
{
    \"answer\": \"We have three pricing plans: Basic, Pro, and Enterprise...\",
    \"conversation_id\": \"conv-abc\",
    \"message_id\": \"msg-xyz\",
    \"sources\": [\"pricing_guide.pdf\"],
    \"suggested_actions\": []
}
```

---

### Where Used

**Services**:
- `app/services/conversation/conversation_service.py` - Message storage
- `app/services/conversation/conversation_history_service.py` - Session history
- `app/services/conversation/async_conversation_service.py` - Async version

**Routes**:
- `app/routes/v1/chat.py` - Main chat endpoint
- `app/routes/v2/chat.py` - Async chat endpoint

---

## lead.py

**File**: `app/models/lead.py` (47 lines)

### Purpose

Models for lead capture and management.

### Models Defined

1. `LeadStatus` - Lead lifecycle enum
2. `Lead` - Complete lead model
3. `LeadCreate` - Create lead request

---

### LeadStatus Enum

```python
class LeadStatus(str, Enum):
    NEW = \"new\"
    CONTACTED = \"contacted\"
    QUALIFIED = \"qualified\"
    CONVERTED = \"converted\"
    LOST = \"lost\"
```

**Lead Lifecycle**:
```
new → contacted → qualified → converted  ✓
                          \\→ lost        ✗
```

**Status Meanings**:
- **NEW**: Just captured, not yet contacted
- **CONTACTED**: Sales team reached out
- **QUALIFIED**: Interested, good fit
- **CONVERTED**: Became a customer!
- **LOST**: Not interested or poor fit

**Usage**:
```python
# New lead from chat
lead.status = LeadStatus.NEW

# Sales follows up
lead.status = LeadStatus.CONTACTED

# Lead expresses interest
lead.status = LeadStatus.QUALIFIED

# Deal closed!
lead.status = LeadStatus.CONVERTED
```

---

### Lead Model

```python
class Lead(BaseModel):
    lead_id: str
    tenant_id: str
    conversation_id: str                                 # Source conversation
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    message: Optional[str] = None                        # Initial message
    status: LeadStatus = LeadStatus.NEW
    created_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: Optional[Dict[str, Any]] = None            # Custom fields
```

**Why all fields Optional?**:
```python
name: Optional[str] = None
```
- **Flexibility**: Not all tenants collect all fields
- **Partial leads**: Can create with just email
- **Progressive collection**: Add fields over time

**`metadata: Optional[Dict[str, Any]]`**:
```python
metadata = {
    \"utm_source\": \"google\",
    \"utm_campaign\": \"summer2024\",
    \"custom_field_1\": \"value\",
    \"custom_field_2\": 123
}
```
- **Extensibility**: Custom fields without schema changes
- **Analytics**: Track campaign sources

---

### Where Used

**Services**:
- `app/services/lead/lead_service.py` - Lead CRUD
- `app/services/lead/async_lead_service.py` - Async version

**Routes**:
- `app/routes/v1/leads.py` - Lead management
- `app/routes/v1/chat.py` - Lead creation during chat

---

## document.py

**File**: `app/models/document.py` (18 lines)

### Purpose

Metadata for documents ingested into knowledge base.

### DocumentMetadata Model

```python
class DocumentMetadata(BaseModel):
    source: str                                          # File name or URL
    tenant_id: str                                       # Multi-tenant isolation
    document_type: str                                   # \"docx\", \"pdf\", \"url\"
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)
    chunk_count: int = 0                                 # Number of chunks created
    extra_info: Optional[Dict[str, Any]] = None
```

**`source: str`**:
```python
source = \"product_manual.pdf\"
source = \"https://example.com/faq\"
source = \"company_handbook.docx\"
```

**`chunk_count: int`**:
- **Set during ingestion**: After document is split
- **Used for**: Metrics, debugging

**Storage Pattern**:

**MongoDB** (metadata):
```python
{
    \"source\": \"manual.pdf\",
    \"tenant_id\": \"tenant-123\",
    \"document_type\": \"pdf\",
    \"chunk_count\": 50,
    \"uploaded_at\": \"2025-12-10T10:00:00Z\"
}
```

**Qdrant** (vectors):
```python
# 50 separate vector points
{
    \"id\": \"vec_1\",
    \"vector\": [0.1, 0.2, ...],
    \"payload\": {
        \"tenant_id\": \"tenant-123\",
        \"source\": \"manual.pdf\",
        \"text\": \"chunk 1 content...\",
        \"chunk_index\": 0
    }
}
# ... 49 more chunks
```

---

### Where Used

**Services**:
- `app/services/ai/rag_service.py` - Document ingestion
- `app/services/ai/async_rag_service.py` - Async ingestion

**Routes**:
- `app/routes/v1/documents.py` - Document upload

---

## Summary

### All Models Overview

| File | Models | Primary Use Case |
|------|--------|------------------|
| `user.py` | 9 models | Authentication, RBAC |
| `tenant.py` | 4 models | Multi-tenant config |
| `conversation.py` | 4 models | Chat messages |
| `lead.py` | 3 models | Lead management |
| `document.py` | 1 model | Knowledge base metadata |

### Pydantic Patterns Used

1. **Inheritance**: `UserCreate(UserBase)`
2. **Enums**: `UserRole`, `LeadStatus`
3. **Validation**: `EmailStr`, `Field(min_length=8)`
4. **Defaults**: `Field(default_factory=datetime.utcnow)`
5. **Optional Fields**: `Optional[str] = None`

### Next Documentation

Continue to:
- [04_ROUTES_V1.md](./04_ROUTES_V1.md) - API endpoints explained
- [10_SERVICES_AI.md](./10_SERVICES_AI.md) - AI services deep dive

---

*Models documentation complete.*
