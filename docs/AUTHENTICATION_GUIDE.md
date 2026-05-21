# QBot Authentication & API Key Guide

**Complete guide to understanding QBot's dual authentication system**

---

## 📋 Table of Contents

- [Overview](#overview)
- [Authentication Architecture](#authentication-architecture)
- [API Key System Explained](#api-key-system-explained)
- [Complete Flow Diagrams](#complete-flow-diagrams)
- [Implementation Guide](#implementation-guide)
- [Security Best Practices](#security-best-practices)
- [Troubleshooting](#troubleshooting)

---

## Overview

QBot uses a **dual authentication system** to serve two distinct user types:

| User Type | Authentication Method | Use Case |
|-----------|----------------------|----------|
| **Admins** | JWT Bearer Tokens | Managing tenants, viewing analytics, configuring system |
| **End Users** | API Keys | Using chatbot widget on websites |

**Why two methods?** Because asking website visitors to "login" before using a chatbot would **kill user engagement**. The API key system enables **instant, frictionless access** while maintaining security and tenant isolation.

---

## Authentication Architecture

### System Design

```
┌─────────────────────────────────────────────────────────────────┐
│                    QBOT AUTHENTICATION SYSTEM                   │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────┐                    ┌─────────────────────┐
│   ADMIN PORTAL      │                    │   CLIENT WEBSITE    │
│   (Dashboard)       │                    │   (Widget)          │
└──────────┬──────────┘                    └──────────┬──────────┘
           │                                          │
           │ 1. Login                                 │ 4. Chat Message
           │ POST /api/auth/login                     │ POST /api/chat
           │ {email, password}                        │ X-API-Key: aob_xxx
           │                                          │
           ▼                                          ▼
┌──────────────────────────────────────────────────────────────────┐
│                        FASTAPI BACKEND                           │
│                                                                  │
│  ┌────────────────┐              ┌──────────────────┐          │
│  │  JWT Auth      │              │   API Key Auth   │          │
│  │  Middleware    │              │   Middleware     │          │
│  └────────┬───────┘              └────────┬─────────┘          │
│           │                                │                     │
│  2. Return JWT Token          5. Validate API Key              │
│     "eyJhbGci..."                 tenant_service.validate()     │
│           │                                │                     │
│           ▼                                ▼                     │
│  ┌────────────────────────────────────────────────────┐        │
│  │  3. Generate API Key for Tenant                    │        │
│  │  POST /api/tenants/{id}/generate-api-key           │        │
│  │  Authorization: Bearer {jwt_token}                 │        │
│  │                                                     │        │
│  │  Returns: { api_key: "aob_xxx", tenant_id: "..." } │        │
│  └────────────────────────────────────────────────────┘        │
│                                                                  │
│  6. Process Chat → Return Response                             │
└──────────────────────────────────────────────────────────────────┘
```

---

## API Key System Explained

### What is an API Key?

An API key is a **permanent, tenant-specific credential** that allows public access to the chat endpoint without requiring user authentication.

**Format**: `aob_` + random string  
**Example**: `aob_pBBFGD2rewWz_yfYaO_UeRFbq3...`

### Why Do We Need It?

#### ❌ Without API Keys:

```javascript
// Website visitor wants to ask a question
// They have to:
1. Click "Sign up"
2. Enter email, password
3. Verify email
4. Login
5. THEN ask their question

// Result: 95% of visitors leave ❌
```

#### ✅ With API Keys:

```javascript
// Website visitor clicks chatbot
// Widget immediately sends:
POST /api/chat
Headers: { "X-API-Key": "aob_abc123..." }
Body: { "query": "I need a 3BHK apartment" }

// Response arrives in 2 seconds ✅
// Visitor gets instant help ✅
```

### How API Keys Work

#### Step 1: Generation (One-time Setup)

**Endpoint**: `POST /api/tenants/{tenant_id}/generate-api-key`

**Who calls it?** Admin user (via dashboard)

**Code Example**:
```bash
curl -X POST http://localhost:8000/api/tenants/tenant-123/generate-api-key \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

**Response**:
```json
{
  "api_key": "aob_pBBFGD2rewWz_yfYaO_UeRFbq3VfhB2BLLvWjIEe82",
  "tenant_id": "tenant-123"
}
```

**Backend Logic** (from `app/routes/v1/tenants.py`):
```python
@router.post("/{tenant_id}/generate-api-key", response_model=dict)
async def generate_api_key(
    tenant_id: str,
    current_user: UserResponse = Depends(get_current_user),
):
    """Generate a new API key for tenant's chat widget."""
    # Validate tenant access
    await validate_tenant_access(tenant_id, current_user)

    # Only admins can generate API keys
    if current_user.role not in [
        UserRole.PLATFORM_ADMIN,
        UserRole.AOB_ADMIN,
        UserRole.CLIENT_ADMIN,
    ]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Insufficient permissions"
        )

    # Generate and store API key
    api_key = tenant_service.generate_api_key(tenant_id)
    return {"api_key": api_key, "tenant_id": tenant_id}
```

**What happens inside `tenant_service.generate_api_key()`**:
1. Generates random secure string with `aob_` prefix
2. Hashes the key for storage (security best practice)
3. Stores hash in DynamoDB linked to tenant_id
4. Returns the **plaintext key** (only shown once!)

#### Step 2: Embedding in Widget

**Frontend Integration**:
```html
<!-- Client's website: www.abc-realestate.com -->
<!DOCTYPE html>
<html>
<head>
    <title>ABC Real Estate</title>
</head>
<body>
    <!-- Client's content -->
    <h1>Welcome to ABC Real Estate</h1>
    
    <!-- QBot Widget Configuration -->
    <script>
      window.QBotConfig = {
        apiKey: 'aob_pBBFGD2rewWz_yfYaO_UeRFbq3VfhB2BLLvWjIEe82',
        tenantId: 'tenant-123',
        primaryColor: '#10B981',
        botName: 'PropertyBot'
      };
    </script>
    
    <!-- QBot Widget Script -->
    <script src="https://yourapp.com/widget.js"></script>
</body>
</html>
```

**Widget JavaScript** (simplified):
```javascript
// widget.js - Handles all chat interactions
class QBotWidget {
  constructor(config) {
    this.apiKey = config.apiKey;
    this.tenantId = config.tenantId;
    this.conversationId = null;
  }

  async sendMessage(userMessage) {
    try {
      const response = await fetch('https://yourapi.com/api/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-API-Key': this.apiKey  // ← API key sent here
        },
        body: JSON.stringify({
          query: userMessage,
          tenant_id: this.tenantId,
          conversation_id: this.conversationId,
          user_identifier: this.getUserIdentifier()
        })
      });

      const data = await response.json();
      this.conversationId = data.conversation_id;
      return data;
    } catch (error) {
      console.error('Chat error:', error);
      throw error;
    }
  }

  getUserIdentifier() {
    // Generate or retrieve unique visitor ID
    return localStorage.getItem('qbot_visitor_id') || 
           this.generateVisitorId();
  }
}

// Initialize widget on page load
window.addEventListener('DOMContentLoaded', () => {
  new QBotWidget(window.QBotConfig);
});
```

#### Step 3: Validation on Each Request

**Chat Endpoint** (from `app/routes/v1/chat.py`):
```python
@router.post("", response_model=ChatResponse)
async def chat(
    request: ChatRequest, 
    api_key: Optional[str] = Header(None, alias="X-API-Key")
):
    """Chat with the bot. Supports both JWT auth and API key auth."""
    try:
        # Validate authentication
        if api_key:
            # Widget authentication via API key
            tenant_id_from_key = tenant_service.validate_api_key(api_key)
            if not tenant_id_from_key:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, 
                    detail="Invalid API key"
                )
            
            # ✅ API key is valid!
            # Override tenant_id with the one from API key
            request.tenant_id = tenant_id_from_key
        
        # Continue with chat logic...
        # Get or create conversation
        if request.conversation_id:
            conversation = conversation_service.get_conversation(
                request.conversation_id
            )
        else:
            conversation = conversation_service.create_conversation(
                request.tenant_id, request.user_identifier
            )
        
        # Process message and return response
        answer, sources = rag_service.answer_query(
            query=request.query,
            tenant_id=request.tenant_id,
            conversation_history=conversation_history
        )
        
        return ChatResponse(
            answer=answer,
            conversation_id=conversation.conversation_id,
            sources=sources
        )
        
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise
```

**Validation Logic** (simplified):
```python
# tenant_service.validate_api_key()
def validate_api_key(self, api_key: str) -> Optional[str]:
    """
    Validates API key and returns tenant_id if valid.
    
    Args:
        api_key: The API key from X-API-Key header
        
    Returns:
        tenant_id if valid, None otherwise
    """
    # Hash the incoming key
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    
    # Query DynamoDB for this key hash
    result = self.table.scan(
        FilterExpression=Attr('api_key_hash').eq(key_hash)
    )
    
    if result['Items']:
        tenant = result['Items'][0]
        return tenant['tenant_id']
    
    return None  # Invalid key
```

---

## Complete Flow Diagrams

### Flow 1: Admin Setup (One-Time)

```
┌─────────────────────────────────────────────────────────────────┐
│                    ADMIN SETUP FLOW                             │
└─────────────────────────────────────────────────────────────────┘

Admin Dashboard
     │
     │ 1. Admin logs in
     ├──────► POST /api/auth/login
     │        { "email": "admin@company.com", "password": "***" }
     │
     │ 2. Receives JWT token
     ◄────── { "access_token": "eyJhbGci...", "token_type": "bearer" }
     │
     │ 3. Creates tenant
     ├──────► POST /api/tenants
     │        Authorization: Bearer eyJhbGci...
     │        { "name": "ABC Real Estate", "config": {...} }
     │
     │ 4. Receives tenant_id
     ◄────── { "tenant_id": "tenant-123", ... }
     │
     │ 5. Generates API key for widget
     ├──────► POST /api/tenants/tenant-123/generate-api-key
     │        Authorization: Bearer eyJhbGci...
     │
     │ 6. Receives API key (shown only once!)
     ◄────── { "api_key": "aob_pBBFGD2...", "tenant_id": "tenant-123" }
     │
     │ 7. Copies API key to widget code
     ├──────► Embeds in client's website
     │
     ▼
   DONE - Widget is live on client's website!
```

### Flow 2: End User Chat (Every Time)

```
┌─────────────────────────────────────────────────────────────────┐
│                    END USER CHAT FLOW                           │
└─────────────────────────────────────────────────────────────────┘

Website Visitor (www.abc-realestate.com)
     │
     │ 1. Clicks chatbot widget
     │    Widget loads with embedded API key
     │
     │ 2. Types message: "I need a 3BHK apartment"
     │
Widget JavaScript
     │
     │ 3. Sends message to backend
     ├──────► POST /api/chat
     │        X-API-Key: aob_pBBFGD2...
     │        {
     │          "query": "I need a 3BHK apartment",
     │          "tenant_id": "tenant-123",
     │          "conversation_id": null,
     │          "user_identifier": "visitor_abc123"
     │        }
     │
Backend (chat endpoint)
     │
     │ 4. Validates API key
     ├──────► tenant_service.validate_api_key("aob_pBBFGD2...")
     │        ✅ Returns "tenant-123"
     │
     │ 5. Creates conversation (first message)
     ├──────► conversation_service.create_conversation(
     │          tenant_id="tenant-123",
     │          user_identifier="visitor_abc123"
     │        )
     │        Returns conversation_id="conv-456"
     │
     │ 6. Processes message with RAG
     ├──────► rag_service.answer_query(
     │          query="I need a 3BHK apartment",
     │          tenant_id="tenant-123",
     │          conversation_history=[...]
     │        )
     │        Returns (answer, sources)
     │
     │ 7. Returns response
     ◄────── {
     │        "answer": "We have luxury 3BHK apartments in...",
     │        "conversation_id": "conv-456",
     │        "sources": ["properties.pdf"]
     │      }
     │
Widget JavaScript
     │
     │ 8. Displays response in chat UI
     │    Stores conversation_id for next message
     │
     ▼
Website Visitor sees response instantly! ✅
```

### Flow 3: Follow-up Messages

```
Website Visitor
     │
     │ User types: "What's the price range?"
     │
Widget JavaScript
     │
     ├──────► POST /api/chat
     │        X-API-Key: aob_pBBFGD2...
     │        {
     │          "query": "What's the price range?",
     │          "tenant_id": "tenant-123",
     │          "conversation_id": "conv-456",  ← Previous ID
     │          "user_identifier": "visitor_abc123"
     │        }
     │
Backend
     │
     │ Validates API key ✅
     │ Retrieves existing conversation (conv-456)
     │ Gets conversation history for context
     │ Processes with RAG
     │
     ◄────── {
     │        "answer": "The 3BHK apartments range from ₹80L to ₹1.5Cr",
     │        "conversation_id": "conv-456",  ← Same conversation
     │        "sources": ["properties.pdf"]
     │      }
     │
     ▼
Visitor gets contextual response ✅
```

---

## Implementation Guide

### For Platform Admins (You)

#### 1. Create API Key Generation UI

```javascript
// Admin Dashboard - TenantsPage.jsx
import React, { useState } from 'react';

function GenerateAPIKeyButton({ tenantId, authToken }) {
  const [apiKey, setApiKey] = useState(null);
  const [loading, setLoading] = useState(false);

  const generateKey = async () => {
    setLoading(true);
    try {
      const response = await fetch(
        `http://localhost:8000/api/tenants/${tenantId}/generate-api-key`,
        {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${authToken}`
          }
        }
      );
      
      const data = await response.json();
      setApiKey(data.api_key);
      
      // Show success message
      alert('API Key generated! Copy it now - it won\'t be shown again.');
    } catch (error) {
      console.error('Error generating API key:', error);
      alert('Failed to generate API key');
    } finally {
      setLoading(false);
    }
  };

  const copyToClipboard = () => {
    navigator.clipboard.writeText(apiKey);
    alert('API Key copied to clipboard!');
  };

  return (
    <div>
      <button onClick={generateKey} disabled={loading}>
        {loading ? 'Generating...' : 'Generate API Key'}
      </button>
      
      {apiKey && (
        <div style={{ marginTop: '10px', padding: '10px', background: '#f0f0f0' }}>
          <strong>API Key (copy now):</strong>
          <pre style={{ margin: '5px 0' }}>{apiKey}</pre>
          <button onClick={copyToClipboard}>Copy to Clipboard</button>
        </div>
      )}
    </div>
  );
}

export default GenerateAPIKeyButton;
```

#### 2. Provide Widget Integration Code

```javascript
// Admin Dashboard - show this code to tenant
function WidgetIntegrationCode({ tenantId, apiKey }) {
  const integrationCode = `
<!-- Add this to your website before </body> -->
<script>
  window.QBotConfig = {
    apiKey: '${apiKey}',
    tenantId: '${tenantId}',
    primaryColor: '#10B981',
    botName: 'Assistant'
  };
</script>
<script src="https://yourapp.com/widget.js"></script>
  `.trim();

  return (
    <div>
      <h3>Widget Integration Code</h3>
      <pre style={{ background: '#f5f5f5', padding: '15px' }}>
        {integrationCode}
      </pre>
      <button onClick={() => navigator.clipboard.writeText(integrationCode)}>
        Copy Code
      </button>
    </div>
  );
}
```

### For Tenant Clients

#### Embedding the Widget

**Simple HTML Example**:
```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ABC Real Estate</title>
</head>
<body>
    <h1>Welcome to ABC Real Estate</h1>
    <p>Browse our properties...</p>
    
    <!-- QBot Widget - Add before closing </body> -->
    <script>
      window.QBotConfig = {
        apiKey: 'aob_your_api_key_here',
        tenantId: 'your-tenant-id',
        primaryColor: '#10B981',
        botName: 'PropertyBot'
      };
    </script>
    <script src="https://yourapp.com/widget.js"></script>
</body>
</html>
```

**React Example**:
```javascript
// App.jsx
import React, { useEffect } from 'react';

function App() {
  useEffect(() => {
    // Initialize QBot widget
    window.QBotConfig = {
      apiKey: 'aob_your_api_key_here',
      tenantId: 'your-tenant-id',
      primaryColor: '#10B981',
      botName: 'PropertyBot'
    };
    
    // Load widget script
    const script = document.createElement('script');
    script.src = 'https://yourapp.com/widget.js';
    script.async = true;
    document.body.appendChild(script);
    
    return () => {
      document.body.removeChild(script);
    };
  }, []);

  return (
    <div className="App">
      <h1>Welcome to ABC Real Estate</h1>
      {/* Your app content */}
    </div>
  );
}

export default App;
```

**Next.js Example**:
```javascript
// pages/_app.jsx
import { useEffect } from 'react';
import Script from 'next/script';

function MyApp({ Component, pageProps }) {
  useEffect(() => {
    window.QBotConfig = {
      apiKey: process.env.NEXT_PUBLIC_QBOT_API_KEY,
      tenantId: process.env.NEXT_PUBLIC_QBOT_TENANT_ID,
      primaryColor: '#10B981',
      botName: 'PropertyBot'
    };
  }, []);

  return (
    <>
      <Component {...pageProps} />
      <Script 
        src="https://yourapp.com/widget.js" 
        strategy="afterInteractive" 
      />
    </>
  );
}

export default MyApp;
```

---

## Security Best Practices

### 1. API Key Storage

#### ❌ DON'T Store Keys in Plain Text

```python
# BAD - Never do this
tenant_data = {
    "tenant_id": "tenant-123",
    "api_key": "aob_abc123xyz..."  # ❌ Plain text!
}
```

#### ✅ DO Hash Keys Before Storage

```python
# GOOD - Current implementation
import hashlib

def generate_api_key(tenant_id: str) -> str:
    # Generate random key
    api_key = f"aob_{secrets.token_urlsafe(32)}"
    
    # Hash for storage (one-way)
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    
    # Store hash, not plain text
    self.table.update_item(
        Key={'tenant_id': tenant_id},
        UpdateExpression='SET api_key_hash = :hash',
        ExpressionAttributeValues={':hash': key_hash}
    )
    
    # Return plain text only once (for user to copy)
    return api_key
```

### 2. API Key Rotation

**Implement key regeneration**:
```python
@router.post("/{tenant_id}/regenerate-api-key")
async def regenerate_api_key(
    tenant_id: str,
    current_user: UserResponse = Depends(get_current_user)
):
    """Regenerate API key (invalidates old one)."""
    await validate_tenant_access(tenant_id, current_user)
    
    # Generate new key (overwrites old hash)
    new_key = tenant_service.generate_api_key(tenant_id)
    
    # Log the regeneration for audit
    logger.info(f"API key regenerated for tenant {tenant_id} by {current_user.email}")
    
    return {
        "api_key": new_key,
        "tenant_id": tenant_id,
        "message": "Old API key is now invalid"
    }
```

### 3. Rate Limiting per API Key

```python
# Add rate limiting middleware
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@router.post("", response_model=ChatResponse)
@limiter.limit("10/minute")  # 10 requests per minute per API key
async def chat(
    request: ChatRequest,
    api_key: Optional[str] = Header(None, alias="X-API-Key")
):
    # Chat logic...
    pass
```

### 4. API Key Scope Restriction

```python
# Only allow API keys for chat endpoint
ALLOWED_ENDPOINTS_FOR_API_KEY = [
    "/api/chat",
    "/api/tenants/{id}/welcome"  # Public endpoints
]

# Admin endpoints REQUIRE JWT
ADMIN_ONLY_ENDPOINTS = [
    "/api/tenants",
    "/api/leads",
    "/api/documents/ingest",
    "/api/tenants/{id}/generate-api-key"
]
```

### 5. HTTPS Only

```python
# Force HTTPS in production
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware

if settings.ENVIRONMENT == "production":
    app.add_middleware(HTTPSRedirectMiddleware)
```

### 6. CORS Configuration

```python
# Only allow specific origins
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://abc-realestate.com",
        "https://xyz-properties.com",
        # Add tenant domains
    ],
    allow_credentials=True,
    allow_methods=["POST", "GET"],
    allow_headers=["X-API-Key", "Content-Type"],
)
```

---

## Troubleshooting

### Common Issues

#### Issue 1: "Invalid API Key" Error

**Symptom**:
```json
{
  "detail": "Invalid API key"
}
```

**Causes**:
1. API key typo (extra spaces, missing characters)
2. API key not generated yet
3. API key was regenerated (old one invalid)
4. Wrong tenant_id in request

**Solution**:
```javascript
// Check for common mistakes
const apiKey = window.QBotConfig.apiKey.trim();  // Remove spaces
console.log('API Key:', apiKey);  // Verify format: aob_xxx

// Verify in backend logs
await fetch('/api/chat', {
  headers: {
    'X-API-Key': apiKey,
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    query: "test",
    tenant_id: "tenant-123"  // Must match the tenant that generated key
  })
});
```

#### Issue 2: CORS Error

**Symptom**:
```
Access to fetch at 'http://localhost:8000/api/chat' from origin 
'http://localhost:3000' has been blocked by CORS policy
```

**Solution**:
```python
# app/main.py
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Add your domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

#### Issue 3: 403 Forbidden when Generating Key

**Symptom**:
```json
{
  "detail": "Insufficient permissions"
}
```

**Cause**: User doesn't have admin role

**Solution**:
```python
# User must have one of these roles:
- platform_admin
- aob_admin
- client_admin

# Check user role in database
user = user_service.get_user(email="admin@example.com")
print(user.role)  # Should be one of the above
```

#### Issue 4: API Key Not Persisting

**Symptom**: New key generated each time, old keys don't work

**Cause**: Database UPSERT not working correctly

**Check**:
```python
# Verify tenant has api_key_hash field
tenant = tenant_service.get_tenant(tenant_id)
print(tenant.get('api_key_hash'))  # Should exist after generation
```

---

## Real-World Example: Complete Setup

### Scenario: ABC Real Estate wants to add chatbot to their website

#### Step 1: Platform Admin Creates Tenant

```bash
# Login as admin
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "platform_admin@qbot.com",
    "password": "admin123"
  }'

# Response:
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}

# Create tenant for ABC Real Estate
curl -X POST http://localhost:8000/api/tenants \
  -H "Authorization: Bearer eyJhbGci..." \
  -H "Content-Type: application/json" \
  -d '{
    "name": "ABC Real Estate",
    "config": {
      "plan": "S2",
      "url_slug": "abc-real-estate",
      "bot_name": "PropertyBot",
      "primary_color": "#10B981",
      "welcome_message": "Welcome to ABC Real Estate!"
    }
  }'

# Response:
{
  "tenant_id": "abc-real-estate-123",
  "name": "ABC Real Estate",
  ...
}
```

#### Step 2: Generate API Key

```bash
curl -X POST http://localhost:8000/api/tenants/abc-real-estate-123/generate-api-key \
  -H "Authorization: Bearer eyJhbGci..."

# Response (ONE TIME ONLY!):
{
  "api_key": "aob_pBBFGD2rewWz_yfYaO_UeRFbq3VfhB2BLLvWjIEe82",
  "tenant_id": "abc-real-estate-123"
}
```

#### Step 3: ABC Real Estate Embeds Widget

```html
<!-- On www.abc-realestate.com -->
<!DOCTYPE html>
<html>
<head>
    <title>ABC Real Estate - Find Your Dream Home</title>
</head>
<body>
    <header>
        <h1>ABC Real Estate</h1>
        <nav>...</nav>
    </header>
    
    <main>
        <!-- Website content -->
    </main>
    
    <!-- QBot Widget -->
    <script>
      window.QBotConfig = {
        apiKey: 'aob_pBBFGD2rewWz_yfYaO_UeRFbq3VfhB2BLLvWjIEe82',
        tenantId: 'abc-real-estate-123',
        primaryColor: '#10B981',
        botName: 'PropertyBot'
      };
    </script>
    <script src="https://yourapp.com/widget.js"></script>
</body>
</html>
```

#### Step 4: End User Interaction

```javascript
// User visits www.abc-realestate.com
// Clicks chatbot icon
// Types: "I'm looking for a 3BHK apartment under 1 crore"

// Widget sends:
POST https://yourapi.com/api/chat
Headers:
  X-API-Key: aob_pBBFGD2rewWz_yfYaO_UeRFbq3VfhB2BLLvWjIEe82
  Content-Type: application/json
Body:
{
  "query": "I'm looking for a 3BHK apartment under 1 crore",
  "tenant_id": "abc-real-estate-123",
  "conversation_id": null,
  "user_identifier": "visitor_abc123"
}

// Backend:
1. Validates API key ✅
2. Identifies tenant: ABC Real Estate
3. Creates conversation
4. Searches documents in ABC Real Estate's database
5. Generates personalized response using their property data
6. Returns response

// Widget displays:
"We have several 3BHK apartments under 1 crore in Bangalore. 
Let me show you our luxury apartments in Whitefield ranging 
from ₹80L to ₹95L. Would you like more details?"
```

---

## Comparison with Industry Standards

### How Other Platforms Do It

| Platform | Public Auth Method | Purpose |
|----------|-------------------|---------|
| **Intercom** | API Key | Chat widget on websites |
| **Drift** | API Key | Conversational marketing |
| **Zendesk** | API Key | Support widget |
| **Twilio** | API Key | SMS, Voice, Video |
| **Stripe** | API Key (Publishable) | Payment processing |
| **QBot** | API Key ✅ | AI chatbot widget |

**Key Insight**: API keys for public endpoints are **industry standard** for SaaS platforms that embed in client websites.

---

## Summary

### Key Takeaways

1. **Two Auth Systems are Necessary**
   - JWT for admins (secure, short-lived)
   - API keys for widgets (permanent, public-safe)

2. **API Keys Enable Frictionless UX**
   - No login required for end users
   - Instant chatbot access
   - Higher engagement rates

3. **Security Through Design**
   - Keys are hashed before storage
   - Validation on every request
   - Tenant isolation maintained
   - Easy to rotate/regenerate

4. **Industry Standard Pattern**
   - Used by Intercom, Drift, Zendesk, etc.
   - Proven at scale
   - Familiar to developers

### Quick Reference

```
┌─────────────────────────────────────────────────┐
│  WHEN TO USE EACH AUTH METHOD                   │
├─────────────────────────────────────────────────┤
│                                                 │
│  JWT Bearer Token (Admin):                     │
│  • /api/auth/login ✅                          │
│  • /api/tenants/* ✅                           │
│  • /api/leads/* ✅                             │
│  • /api/documents/* ✅                         │
│                                                 │
│  API Key (Widget):                             │
│  • /api/chat ✅                                │
│  • /api/tenants/{id}/welcome ✅                │
│                                                 │
└─────────────────────────────────────────────────┘
```

---

**Questions or Issues?**

Check the [Troubleshooting](#troubleshooting) section or refer to the [API Documentation](./API_COMPLETE_REFERENCE.md) for more details.
