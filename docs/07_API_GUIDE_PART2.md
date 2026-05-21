# API Guide - Additional Sections (Part 2)

> **Continuation of 07_API_GUIDE.md with advanced topics**  
> Merge this with 07_API_GUIDE.md for complete guide

---

## Complete Endpoint Reference (Continued)

### User Management Endpoints

#### List All Users

**Endpoint**: `GET /api/users?skip=0&limit=10`

**Purpose**: List users with pagination and RBAC filtering

**Authentication**: JWT token required

**Request**:
```bash
curl -H "Authorization: Bearer $TOKEN" \
  'http://localhost:8000/api/users?skip=0&limit=10'
```

**RBAC Filtering Logic (DynamoDB)**:
```python
if current_user.role in ["platform_admin", "aob_admin"]:
    # Scan/Query GSI for ALL users
    # In DynamoDB, efficient listing often requires a GSI where PK="ALL_USERS"
    # Or strict Scan if volume is low.
    users = table.scan(
        FilterExpression=Attr("PK").begins_with("USER#") & Attr("is_deleted").eq(False)
    )
else:
    # Query Users by Tenant (GSI)
    # GSI1PK = "TENANT#<id>", GSI1SK begins_with "USER#"
    users = table.query(
        IndexName="GSI1",
        KeyConditionExpression=Key("GSI1PK").eq(f"TENANT#{current_user.tenant_id}") & Key("GSI1SK").begins_with("USER#"),
        FilterExpression=Attr("is_deleted").eq(False)
    )
```

**Response** (200 OK):
```json
{
  "users": [
    {
      "user_id": "user_123",
      "email": "agent@company.com",
      "full_name": "Jane Agent",
      "role": "client_agent",
      "tenant_id": "tenant_xyz789",
      "is_active": true
    }
  ],
  "total": 25
}
```

---

## Error Handling - Complete Reference

### HTTP 400 - Bad Request

**Scenario 1: Duplicate Email**
```bash
curl -X POST http://localhost:8000/api/auth/register \
  -d '{"email": "existing@example.com", ...}'
```

Response:
```json
{
  "detail": "Email already registered",
  "status_code": 400
}
```

**Scenario 2: Invalid Lead Status**
```bash
curl -X PUT http://localhost:8000/api/leads/lead_123/status \
  -d '{"status": "invalid"}'
```

Response:
```json
{
  "detail": "Invalid status. Must be: new, contacted, qualified, converted, lost",
  "status_code": 400
}
```

---

### HTTP 401 - Unauthorized

**Scenario 1: Expired Token**
```json
{
  "detail": "Token has expired",
  "status_code": 401,
  "expires_at": "2025-12-10T10:00:00Z"
}
```

**Scenario 2: Invalid API Key**
```json
{
  "detail": "Invalid API key",
  "status_code": 401
}
```

**Scenario 3: Deleted User**
```json
{
  "detail": "User account disabled or deleted",
  "status_code": 401
}
```

---

### HTTP 403 - Forbidden

**Scenario: Cross-Tenant Access**
```json
{
  "detail": "Insufficient permissions to access this tenant",
  "status_code": 403,
  "required": "platform_admin or same tenant",
  "current_role": "client_admin",
  "current_tenant": "tenant_abc",
  "requested_tenant": "tenant_xyz"
}
```

---

### HTTP 429 - Rate Limit

```json
{
  "detail": "Rate limit exceeded: 10 per 1 minute",
  "status_code": 429,
  "retry_after": 45,
  "limit": "10/minute"
}
```

---

## Production Deployment

(See [Deployment Guide in 06_MIDDLEWARE_UTILITIES_WORKERS.md](./06_MIDDLEWARE_UTILITIES_WORKERS.md#deployment-guide) for full details)

### Systemd Service Setup
Standard Gunicorn/Uvicorn service file configuration.

### Nginx Configuration
Standard reverse proxy setup to port 8000.

---

## JavaScript Widget Example

```html
<!DOCTYPE html>
<html>
<head>
    <title>Chat Widget</title>
</head>
<body>
    <div id="chat-widget">
        <div id="chat-messages"></div>
        <input type="text" id="chat-input" placeholder="Type a message..." />
    </div>

    <script>
        const API_URL = 'https://api.example.com';
        const API_KEY = 'aob_your_api_key';
        const TENANT_ID = 'tenant_xyz789';
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
            
            displayMessage('assistant', data.answer);
            
            if (data.suggested_actions?.includes('lead_captured')) {
                console.log('Lead captured!');
            }
        }
        // ... (rest of logic same as before)
    </script>
</body>
</html>
```

---

## Troubleshooting Guide

### Common Issues

#### 1. "No module named 'slowapi'"

**Solution**:
```bash
uv add slowapi
# or
pip install slowapi
```

#### 2. "Qdrant connection timeout"

**Cause**: Qdrant server not running or unreachable

**Solution**:
- Check Qdrant is running: `curl http://qdrant-url:6333/collections`
- App works without Qdrant (lazy connection)
- Documents won't be searchable until Qdrant is available

#### 3. "AccessDeniedException" (DynamoDB or Bedrock)

**Cause**: AWS Credentials missing or IAM role invalid.

**Check**:
- `aws sts get-caller-identity`
- Verify IAM Policy allows `dynamodb:*` and `bedrock:InvokeModel`.

#### 4. Logs show "Rate limit exceeded" constantly

**Cause**: Too many requests from same IP

**Solutions**:
1. Increase limits in config
2. Use Redis for distributed rate limiting
3. Whitelist internal IPs

---

## Advanced Topics

### Scaling to 1000+ Concurrent Users

**Architecture**:
- **AWS Lambda (Serverless)**: Deploy FastAPI via Mangum adapter to Lambda. scales to zero and infinity automatically.
- **DynamoDB**: Already auto-scaling (On-Demand Capacity).
- **Qdrant Cloud**: Managed vector DB for scale.

### Multi-Region Deployment

**Global Table**: Enable DynamoDB Global Tables to replicate data across regions active-active.

### Monitoring & Observability

- **CloudWatch**: Logs and Metrics.
- **X-Ray**: Distributed tracing for performance bottlenecks.
