# CORRECT Lambda Test Events for API Gateway REST API

## 🔧 The Issue

The error `"unable to infer a handler"` means Mangum doesn't recognize the event format.

**You're using**: API Gateway REST API (v1)  
**I provided**: HTTP API format (v2) ❌  

Here are the **CORRECT** test events for REST API:

---

## ✅ Test Event 1: Health Check (REST API Format)

**Event name**: `test-health-rest-api`

```json
{
  "resource": "/health",
  "path": "/health",
  "httpMethod": "GET",
  "headers": {
    "Accept": "application/json"
  },
  "queryStringParameters": null,
  "pathParameters": null,
  "stageVariables": null,
  "requestContext": {
    "resourcePath": "/health",
    "httpMethod": "GET",
    "path": "/prod/health",
    "stage": "prod"
  },
  "body": null,
  "isBase64Encoded": false
}
```

---

## ✅ Test Event 2: User Registration (REST API Format)

**Event name**: `test-user-registration-rest`

```json
{
  "resource": "/api/auth/register",
  "path": "/api/auth/register",
  "httpMethod": "POST",
  "headers": {
    "Accept": "application/json",
    "Content-Type": "application/json"
  },
  "queryStringParameters": null,
  "pathParameters": null,
  "stageVariables": null,
  "requestContext": {
    "resourcePath": "/api/auth/register",
    "httpMethod": "POST",
    "path": "/prod/api/auth/register",
    "stage": "prod"
  },
  "body": "{\"email\":\"lambda.test@example.com\",\"password\":\"TestPass123!\",\"full_name\":\"Lambda Test User\",\"role\":\"aob_admin\"}",
  "isBase64Encoded": false
}
```

---

## ✅ Test Event 3: User Login (REST API Format)

**Event name**: `test-login-rest`

```json
{
  "resource": "/api/auth/login",
  "path": "/api/auth/login",
  "httpMethod": "POST",
  "headers": {
    "Accept": "application/json",
    "Content-Type": "application/json"
  },
  "queryStringParameters": null,
  "pathParameters": null,
  "stageVariables": null,
  "requestContext": {
    "resourcePath": "/api/auth/login",
    "httpMethod": "POST",
    "path": "/prod/api/auth/login",
    "stage": "prod"
  },
  "body": "{\"email\":\"lambda.test@example.com\",\"password\":\"TestPass123!\"}",
  "isBase64Encoded": false
}
```

---

## ✅ Test Event 4: Chat (Tests Bedrock!) - REST API Format

**Event name**: `test-chat-bedrock-rest`

**IMPORTANT**: Replace `YOUR_JWT_TOKEN` and `YOUR_TENANT_ID`

```json
{
  "resource": "/api/chat",
  "path": "/api/chat",
  "httpMethod": "POST",
  "headers": {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Authorization": "Bearer YOUR_JWT_TOKEN"
  },
  "queryStringParameters": null,
  "pathParameters": null,
  "stageVariables": null,
  "requestContext": {
    "resourcePath": "/api/chat",
    "httpMethod": "POST",
    "path": "/prod/api/chat",
    "stage": "prod"
  },
  "body": "{\"query\":\"Hello! What services do you offer?\",\"tenant_id\":\"YOUR_TENANT_ID\"}",
  "isBase64Encoded": false
}
```

---

## 🎯 EASIEST TEST SEQUENCE

### Step 1: Test Health Check

**Use this event**:
```json
{
  "resource": "/health",
  "path": "/health",
  "httpMethod": "GET",
  "headers": {"Accept": "application/json"},
  "requestContext": {
    "httpMethod": "GET",
    "path": "/prod/health",
    "stage": "prod"
  },
  "body": null,
  "isBase64Encoded": false
}
```

**Expected Response**:
```json
{
  "statusCode": 200,
  "body": "{\"status\":\"healthy\",\"latency_ms\":...}",
  "headers": {...}
}
```

### Step 2: Test Registration

**Use the registration event above**

**Expected**: User created ✅

### Step 3: Test Login

**Use the login event above**

**Expected**: Get `access_token` in response ✅

### Step 4: Copy JWT Token

From login response:
```json
{
  "body": "{\"access_token\":\"eyJ...\",\"refresh_token\":\"...\"}"
}
```

**Copy the access_token value!**

### Step 5: Create Tenant (to get tenant_id)

```json
{
  "resource": "/api/tenants",
  "path": "/api/tenants",
  "httpMethod": "POST",
  "headers": {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Authorization": "Bearer YOUR_JWT_FROM_STEP_4"
  },
  "requestContext": {
    "httpMethod": "POST",
    "path": "/prod/api/tenants",
    "stage": "prod"
  },
  "body": "{\"name\":\"Test Company\",\"domain\":\"test.com\"}",
  "isBase64Encoded": false
}
```

**Copy the tenant_id from response!**

### Step 6: Test Chat (BEDROCK TEST!)

Use the chat event above with your JWT and tenant_id

**Expected**: AI response ✅ = **BEDROCK WORKING!**

---

## 🔍 Why You Got That Error

**Your Event** (what I gave you):
```json
{
  "rawPath": "/api/auth/register",  ← HTTP API v2 format
  "requestContext": {
    "http": { ... }  ← HTTP API v2 format
  }
}
```

**What Mangum Expected** (REST API v1):
```json
{
  "resource": "/api/auth/register",  ← REST API v1 format
  "path": "/api/auth/register",     ← REST API v1 format
  "httpMethod": "POST",             ← REST API v1 format
  "requestContext": {
    "httpMethod": "POST"  ← REST API v1 format
  }
}
```

Mangum didn't recognize the HTTP API v2 format, so it couldn't infer the handler!

---

## ✅ Quick Copy-Paste Test

**Try this NOW** - Health Check:

```json
{
  "resource": "/health",
  "path": "/health",
  "httpMethod": "GET",
  "headers": {
    "Accept": "application/json"
  },
  "requestContext": {
    "httpMethod": "GET",
    "path": "/prod/health",
    "stage": "prod"
  },
  "body": null,
  "isBase64Encoded": false
}
```

**Click Test** → Should work now! ✅

---

## 📋 Template for ANY Endpoint

```json
{
  "resource": "YOUR_PATH",
  "path": "YOUR_PATH",
  "httpMethod": "GET_OR_POST",
  "headers": {
    "Content-Type": "application/json"
  },
  "requestContext": {
    "httpMethod": "GET_OR_POST",
    "path": "/prod/YOUR_PATH",
    "stage": "prod"
  },
  "body": "JSON_STRING_OR_NULL",
  "isBase64Encoded": false
}
```

---

**Try the health check event now - it should work!** 🚀
