# AWS Lambda Deployment Guide - QBot Backend

**Complete Guide for Deploying FastAPI Application to AWS Lambda**

---

## 📋 Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Architecture](#architecture)
4. [Required Code Modifications](#required-code-modifications)
5. [Deployment Steps](#deployment-steps)
6. [Qdrant EC2 Connection](#qdrant-ec2-connection)
7. [Environment Variables](#environment-variables)
8. [Testing](#testing)
9. [Troubleshooting](#troubleshooting)
10. [Monitoring](#monitoring)

---

## Overview

### What We're Deploying

- **FastAPI Application**: Multi-tenant SaaS chatbot with RAG
- **Dependencies**: DynamoDB, Qdrant (on EC2), AWS Bedrock, S3, SQS
- **Deployment Target**: AWS Lambda + API Gateway
- **Handler**: Mangum (ASGI adapter for Lambda)

### Why Lambda?

✅ **Auto-scaling**: Handles traffic spikes automatically  
✅ **Cost-effective**: Pay only for compute time  
✅ **No server management**: AWS handles infrastructure  
✅ **Integrations**: Native DynamoDB, S3, SQS, Bedrock access

---

## Prerequisites

### 1. AWS Account Setup
- ✅ AWS Account with appropriate permissions
- ✅ AWS CLI installed and configured
- ✅ IAM role with these policies:
  - `AmazonDynamoDBFullAccess`
  - `AmazonS3FullAccess`
  - `AmazonSQSFullAccess`
  - `AmazonBedrockFullAccess`
  - `AWSLambdaBasicExecutionRole`
  - `AWSLambdaVPCAccessExecutionRole` (if Qdrant in private VPC)

### 2. Qdrant EC2 Instance
- ✅ Qdrant running on EC2
- ✅ EC2 security group allowing inbound on port 6333
- ✅ Public IP or private IP (if in same VPC as Lambda)
- ✅ Optional: API key configured for authentication

### 3. Local Development Tools
```bash
# Required tools
pip install uv  # Fast Python package manager
pip install awscli  # AWS CLI
brew install zip  # For creating deployment packages (macOS)
```

---

## Architecture

### Current Architecture (Before Lambda)

```
┌─────────────────────────────────────────────┐
│   EC2 Instance (uvicorn on port 8000)     │
│   ┌─────────────────────────────────┐     │
│   │  FastAPI Application            │     │
│   └──────────┬──────────────────────┘     │
└──────────────┼─────────────────────────────┘
               │
               ▼
    ┌──────────────────────┐
    │   AWS Services       │
    ├──────────────────────┤
    │  • DynamoDB          │
    │  • S3                │
    │  • SQS               │
    │  • Bedrock           │
    └──────────────────────┘
               │
               ▼
    ┌──────────────────────┐
    │ Qdrant EC2 Instance  │
    │  • Port 6333         │
    │  • Collection: docs  │
    └──────────────────────┘
```

### New Architecture (After Lambda)

```
┌─────────────────────────────────────────────┐
│         API Gateway (REST API)              │
│  https://xyz.execute-api.region.aws.com     │
└──────────────┬──────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────┐
│     AWS Lambda Function                      │
│  ┌────────────────────────────────┐         │
│  │  FastAPI + Mangum Handler      │         │
│  │  • Max Timeout: 900s (15 min)  │         │
│  │  • Memory: 1024-2048 MB        │         │
│  │  • Ephemeral Storage: 512 MB   │         │
│  └────────────┬───────────────────┘         │
└───────────────┼──────────────────────────────┘
                │
     ┌──────────┼──────────┐
     │          │          │
     ▼          ▼          ▼
┌─────────┐ ┌─────────┐ ┌────────────────┐
│DynamoDB │ │ Bedrock │ │ Qdrant (EC2)   │
│         │ │         │ │ http://IP:6333 │
└─────────┘ └─────────┘ └────────────────┘
     ▲          ▲
     │          │
     └──────────┴─────────┐
                │
         ┌──────▼──────┐
         │  S3 + SQS   │
         └─────────────┘
```

---

## Required Code Modifications

### 1. Add Mangum Dependency

**File**: `pyproject.toml`

**Add to dependencies** (after line 28):
```toml
"mangum>=0.19.0",
```

**Complete Updated File**:
```toml
[project]
name = "aob-bot"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "argon2-cffi>=23.1.0",
    "argon2-cffi-bindings>=25.1.0",
    "bcrypt>=5.0.0",
    "beautifulsoup4>=4.14.2",
    "boto3>=1.42.4",
    "fastapi[standard]>=0.122.0",
    "google-generativeai>=0.8.5",
    "langchain>=1.1.2",
    "langchain-text-splitters>=1.0.0",
    "langgraph>=1.0.4",
    "lxml>=6.0.2",
    "mangum>=0.19.0",  # ← ADD THIS
    "motor>=3.7.1",
    "passlib[bcrypt]>=1.7.4",
    "pydantic-settings>=2.12.0",
    "pydantic[email]>=2.12.5",
    "pymongo>=4.15.4",
    "pypdf2>=3.0.1",
    "python-docx>=1.2.0",
    "python-jose[cryptography]>=3.5.0",
    "python-multipart>=0.0.20",
    "qdrant-client>=1.16.1",
    "requests>=2.32.5",
    "slowapi>=0.1.9",
]
```

### 2. Create Lambda Handler

**File**: Create new file `lambda_handler.py` in project root

```python
"""AWS Lambda handler for FastAPI application using Mangum."""

from mangum import Mangum
from main import app

# Create Lambda handler
handler = Mangum(app, lifespan="off")
```

**Why `lifespan="off"`?**
- Lambda cold starts would reinitialize lifespan on every invocation
- Qdrant collection check should happen via separate initialization
- Reduces cold start time

### 3. Update Qdrant Configuration for EC2

**File**: `app/core/config.py`

**Current (Line 27-30)**:
```python
Qdrant_URL: str | None = None  # Remote Qdrant URL
QDRANT_API_KEY: str | None = None  
Qdrant_PATH: str = "data/qdrant_data"  # Local path
QDRANT_COLLECTION_NAME: str = "aob_documents"
```

**For Lambda, ensure these are set in environment variables**:
- `Qdrant_URL=http://YOUR_EC2_IP:6333` (or `https://` if SSL)
- `QDRANT_API_KEY=your_api_key_if_configured`

**No code change needed** - just set environment variable.

### 4. Update Database Connection (Already Correct)

**File**: `app/core/database.py`

Your code already handles remote Qdrant correctly:
```python
# Line 23-33 (already correct)
if settings.Qdrant_URL:
    cls._qdrant_client = QdrantClient(
        url=settings.Qdrant_URL,
        api_key=settings.QDRANT_API_KEY,
    )
else:
    cls._qdrant_client = QdrantClient(path=settings.Qdrant_PATH)
```

✅ **No change needed!**

### 5. Disable Lifespan for Lambda (Optional but Recommended)

**File**: `main.py`

**Option A**: Keep lifespan (slower cold starts)
- No change needed
- Qdrant collection check runs on every cold start

**Option B**: Disable for production (recommended)
```python
# Line 43 - Comment out lifespan for Lambda
app = FastAPI(
    title=settings.APP_NAME,
    description="Multi-tenant SaaS chatbot backend",
    version="2.0.0",
    # lifespan=lifespan,  # ← Comment out for Lambda
)
```

**Instead, ensure collection exists manually**:
```bash
# Run once after deployment via AWS CLI
aws lambda invoke \
  --function-name qbot-backend \
  --payload '{"path": "/health"}' \
  response.json
```

Or create initialization Lambda:
```python
# scripts/init_qdrant.py
from app.services.ai.async_rag_service import async_rag_service
import asyncio

async def init():
    await async_rag_service.ensure_collection()
    print("✅ Qdrant collection initialized")

asyncio.run(init())
```

---

## Deployment Steps

### Step 1: Prepare Project

```bash
# Navigate to project
cd /home/ubuntu/CODE

# Add Mangum to dependencies
# (Already done in pyproject.toml modification above)

# Create lambda_handler.py
# (Already done above)

# Install dependencies including Mangum
uv pip install -e .
```

### Step 2: Create Deployment Package

Lambda deployment packages can be large due to dependencies. We'll use layers for optimization.

#### Option A: Simple Deployment (< 50MB uncompressed)

**1. Create deployment directory**:
```bash
mkdir -p lambda_package
cd lambda_package
```

**2. Install dependencies**:
```bash
# Install all dependencies to this directory
pip install -t . \
  fastapi \
  mangum \
  boto3 \
  qdrant-client \
  pydantic \
  pydantic-settings \
  python-jose \
  passlib \
  bcrypt \
  python-multipart \
  beautifulsoup4 \
  pypdf2 \
  python-docx \
  langchain \
  langchain-text-splitters \
  requests
```

**3. Copy application code**:
```bash
# From your CODE directory
cp -r ../app .
cp ../lambda_handler.py .
cp ../main.py .

# Do NOT copy .env - use Lambda environment variables instead
```

**4. Create ZIP**:
```bash
zip -r ../qbot-lambda.zip .
cd ..
```

**5. Upload to Lambda**:
```bash
# Create Lambda function
aws lambda create-function \
  --function-name qbot-backend \
  --runtime python3.12 \
  --role arn:aws:iam::YOUR_ACCOUNT_ID:role/QBotLambdaRole \
  --handler lambda_handler.handler \
  --zip-file fileb://qbot-lambda.zip \
  --timeout 900 \
  --memory-size 2048 \
  --environment Variables="{
    AWS_ACCESS_KEY=YOUR_KEY,
    AWS_SECRET_KEY=YOUR_SECRET,
    AWS_REGION=us-east-1,
    Qdrant_URL=http://YOUR_EC2_IP:6333,
    QDRANT_API_KEY=your_key,
    BEDROCK_LLM_MODEL_ID=amazon.nova-lite-v1:0,
    USE_DYNAMODB=true,
    DYNAMODB_TABLE_NAME=qbot_main_table
  }"
```

#### Option B: Lambda Layers (Recommended for Large Dependencies)

**1. Create layer for dependencies**:
```bash
mkdir -p lambda_layer/python
cd lambda_layer/python

# Install dependencies
pip install -t . \
  fastapi \
  mangum \
  boto3 \
  qdrant-client \
  pydantic \
  pydantic-settings \
  python-jose \
  passlib \
  bcrypt \
  python-multipart \
  beautifulsoup4 \
  pypdf2 \
  python-docx \
  langchain \
  langchain-text-splitters

cd ..
zip -r qbot-dependencies-layer.zip python
```

**2. Create lambda layer**:
```bash
aws lambda publish-layer-version \
  --layer-name qbot-dependencies \
  --description "QBot Python dependencies" \
  --zip-file fileb://qbot-dependencies-layer.zip \
  --compatible-runtimes python3.12
```

**3. Create function package (just code)**:
```bash
mkdir -p function_package
cd function_package

# Copy only application code
cp -r ../app .
cp ../lambda_handler.py .
cp ../main.py .

zip -r ../qbot-function.zip .
cd ..
```

**4. Create Lambda with layer**:
```bash
# Get layer ARN from previous command output
LAYER_ARN="arn:aws:lambda:REGION:ACCOUNT:layer:qbot-dependencies:1"

aws lambda create-function \
  --function-name qbot-backend \
  --runtime python3.12 \
  --role arn:aws:iam::ACCOUNT_ID:role/QBotLambdaRole \
  --handler lambda_handler.handler \
  --zip-file fileb://qbot-function.zip \
  --layers $LAYER_ARN \
  --timeout 900 \
  --memory-size 2048 \
  --ephemeral-storage Size=1024 \
  --environment Variables="{ ... }" 
```

### Step 3: Create API Gateway

**1. Create REST API**:
```bash
aws apigateway create-rest-api \
  --name "QBot API" \
  --description "QBot Backend API" \
  --endpoint-configuration types=REGIONAL
```

**2. Get API ID and Root Resource ID**:
```bash
API_ID=$(aws apigateway get-rest-apis \
  --query "items[?name=='QBot API'].id" \
  --output text)

ROOT_ID=$(aws apigateway get-resources \
  --rest-api-id $API_ID \
  --query "items[?path=='/'].id" \
  --output text)
```

**3. Create proxy resource**:
```bash
aws apigateway create-resource \
  --rest-api-id $API_ID \
  --parent-id $ROOT_ID \
  --path-part "{proxy+}"
```

**4. Get proxy resource ID**:
```bash
PROXY_ID=$(aws apigateway get-resources \
  --rest-api-id $API_ID \
  --query "items[?path=='/{proxy+}'].id" \
  --output text)
```

**5. Create ANY method**:
```bash
aws apigateway put-method \
  --rest-api-id $API_ID \
  --resource-id $PROXY_ID \
  --http-method ANY \
  --authorization-type NONE
```

**6. Integrate with Lambda**:
```bash
# Get Lambda ARN
LAMBDA_ARN=$(aws lambda get-function --function-name qbot-backend \
  --query 'Configuration.FunctionArn' --output text)

# Create integration
aws apigateway put-integration \
  --rest-api-id $API_ID \
  --resource-id $PROXY_ID \
  --http-method ANY \
  --type AWS_PROXY \
  --integration-http-method POST \
  --uri arn:aws:apigateway:REGION:lambda:path/2015-03-31/functions/$LAMBDA_ARN/invocations
```

**7. Grant API Gateway permission**:
```bash
aws lambda add-permission \
  --function-name qbot-backend \
  --statement-id apigateway-invoke \
  --action lambda:InvokeFunction \
  --principal apigateway.amazonaws.com \
  --source-arn "arn:aws:execute-api:REGION:ACCOUNT:$API_ID/*/*"
```

**8. Deploy API**:
```bash
aws apigateway create-deployment \
  --rest-api-id $API_ID \
  --stage-name prod
```

**9. Get API URL**:
```bash
echo "https://$API_ID.execute-api.REGION.amazonaws.com/prod"
```

---

## Qdrant EC2 Connection

### Scenario 1: Qdrant EC2 with Public IP

**1. Get EC2 Public IP**:
```bash
# From AWS Console: EC2 → Instances → Copy Public IPv4 Address
# Example: 54.123.45.67
```

**2. Update EC2 Security Group**:
```bash
# Add inbound rule:
# Type: Custom TCP
# Port: 6333
# Source: Lambda Security Group (or 0.0.0.0/0 for testing)
```

**3. Set Lambda Environment Variable**:
```bash
aws lambda update-function-configuration \
  --function-name qbot-backend \
  --environment "Variables={
    Qdrant_URL=http://54.123.45.67:6333,
    QDRANT_API_KEY=your_key_if_any,
    ... (other variables)
  }"
```

**4. Test Connection**:
```python
# Test script
import requests

url = "http://54.123.45.67:6333/collections"
headers = {"api-key": "your_key"} if your_key else {}

response = requests.get(url, headers=headers)
print(response.json())
# Should show: {"result": {"collections": [...]}}
```

### Scenario 2: Qdrant EC2 in Private VPC (Recommended for Production)

**1. Configure Lambda VPC**:
```bash
# Lambda needs access to private VPC where Qdrant is running

aws lambda update-function-configuration \
  --function-name qbot-backend \
  --vpc-config SubnetIds=subnet-xxx,subnet-yyy,SecurityGroupIds=sg-zzz
```

**2. VPC Security Groups**:
- **Lambda Security Group**: Outbound to Qdrant on port 6333
- **Qdrant Security Group**: Inbound from Lambda SG on port 6333

**3. Set Private IP**:
```bash
# Use EC2 private IP
Qdrant_URL=http://10.0.1.50:6333
```

**Important**: Lambda in VPC cannot access internet unless you add:
- NAT Gateway (for internet access)
- VPC Endpoints for AWS services (DynamoDB, S3, Bedrock)

### Qdrant API Key Setup (Optional but Recommended)

**1. Enable authentication on Qdrant**:
```bash
# SSH to Qdrant EC2
ssh ec2-user@YOUR_EC2_IP

# Edit Qdrant config
nano /etc/qdrant/config.yaml
```

**2. Add authentication**:
```yaml
service:
  api_key: "your_secure_random_key_here"
```

**3. Restart Qdrant**:
```bash
sudo systemctl restart qdrant
```

**4. Update Lambda**:
```bash
aws lambda update-function-configuration \
  --function-name qbot-backend \
  --environment "Variables={
    Qdrant_URL=http://IP:6333,
    QDRANT_API_KEY=your_secure_random_key_here,
    ...
  }"
```

---

## Environment Variables

### Complete List for Lambda

```bash
# AWS Bedrock
AWS_ACCESS_KEY=AKIA...
AWS_SECRET_KEY=wJal...
AWS_BEARER_TOKEN_BEDROCK=optional
AWS_REGION=us-east-1
BEDROCK_LLM_MODEL_ID=amazon.nova-lite-v1:0
BEDROCK_EMBEDDING_MODEL_ID=amazon.titan-embed-text-v2:0
BEDROCK_EMBEDDING_DIMENSIONS=1024

# DynamoDB
USE_DYNAMODB=true
DYNAMODB_TABLE_NAME=qbot_main_table

# Qdrant
Qdrant_URL=http://YOUR_EC2_IP:6333
QDRANT_API_KEY=your_api_key_if_any
QDRANT_COLLECTION_NAME=aob_documents

# S3 & SQS (if using async ingestion)
AWS_S3_BUCKET_NAME=qbot-documents
AWS_SQS_QUEUE_URL=https://sqs.REGION.amazonaws.com/ACCOUNT/qbot-ingestion
USE_ASYNC_INGESTION=false  # true if using S3/SQS

# JWT
JWT_SECRET_KEY=your_secret_key_at_least_32_chars
ACCESS_TOKEN_EXPIRE_MINUTES=60
REFRESH_TOKEN_EXPIRE_DAYS=30

# CORS (for frontend)
CORS_ORIGINS=["https://yourfrontend.com","http://localhost:3000"]

# Application
APP_NAME=QBot Backend
DEBUG=false
```

### Setting Environment Variables

**Via AWS CLI**:
```bash
aws lambda update-function-configuration \
  --function-name qbot-backend \
  --environment "Variables={
    AWS_ACCESS_KEY=AKIA...,
    AWS_SECRET_KEY=wJal...,
    AWS_REGION=us-east-1,
    Qdrant_URL=http://54.123.45.67:6333,
    USE_DYNAMODB=true,
    DYNAMODB_TABLE_NAME=qbot_main_table,
    BEDROCK_LLM_MODEL_ID=amazon.nova-lite-v1:0,
    JWT_SECRET_KEY=your_secret_32_char_key_here,
    CORS_ORIGINS=[\"https://yourfrontend.com\"]
  }"
```

**Via AWS Console**:
1. Lambda → Functions → qbot-backend
2. Configuration → Environment variables → Edit
3. Add all variables one by one
4. Save

**Via AWS Secrets Manager (Recommended for Production)**:
```bash
# Store secrets
aws secretsmanager create-secret \
  --name qbot/prod/env \
  --secret-string '{
    "AWS_SECRET_KEY": "...",
    "JWT_SECRET_KEY": "...",
    "QDRANT_API_KEY": "..."
  }'

# Access in Lambda
import boto3
import json

client = boto3.client('secretsmanager')
response = client.get_secret_value(SecretId='qbot/prod/env')
secrets = json.loads(response['SecretString'])
```

---

## Testing

### 1. Test Health Endpoint

```bash
# Get API URL
API_URL="https://YOUR_API_ID.execute-api.REGION.amazonaws.com/prod"

# Test health
curl $API_URL/health
```

**Expected Response**:
```json
{
  "status": "healthy",
  "latency_ms": 150,
  "details": {
    "database": "connected"
  },
  "version": "2.0.0"
}
```

### 2. Test Authentication

```bash
# Register user
curl -X POST $API_URL/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@example.com",
    "password": "Test123!",
    "full_name": "Test User",
    "role": "aob_admin"
  }'

# Login
curl -X POST $API_URL/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@example.com",
    "password": "Test123!"
  }'
```

### 3. Test Chat Endpoint

```bash
# Get token from login response
TOKEN="eyJhbGci..."

# Create tenant first
curl -X POST $API_URL/api/tenants \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test Company",
    "config": {
      "bot_name": "TestBot",
      "plan": "S1"
    }
  }'

# Chat
curl -X POST $API_URL/api/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "query": "Hello!",
    "tenant_id": "TENANT_ID_FROM_ABOVE"
  }'
```

### 4. Check Lambda Logs

```bash
# View recent logs
aws logs tail /aws/lambda/qbot-backend --follow

# Filter errors
aws logs filter-log-events \
  --log-group-name /aws/lambda/qbot-backend \
  --filter-pattern "ERROR"
```

---

## Troubleshooting

### Issue 1: "Unable to import module 'lambda_handler'"

**Cause**: Missing dependencies or incorrect package structure

**Fix**:
```bash
# Ensure lambda_handler.py is in root of ZIP
unzip -l qbot-lambda.zip | grep lambda_handler

# Should see:
# lambda_handler.py
# app/
# main.py
```

### Issue 2: "Task timed out after 3.00 seconds"

**Cause**: Default Lambda timeout is 3 seconds

**Fix**:
```bash
aws lambda update-function-configuration \
  --function-name qbot-backend \
  --timeout 900  # 15 minutes
```

### Issue 3: "Cannot connect to Qdrant"

**Cause**: Security group, network, or URL issue

**Debug**:
```bash
# Test from Lambda console
import requests
url = "http://YOUR_EC2_IP:6333/collections"
print(requests.get(url).text)
```

**Fixes**:
- Check EC2 security group allows port 6333
- Verify EC2 IP address is correct
- If VPC, ensure Lambda has VPC access
- Check Qdrant is running: `systemctl status qdrant`

### Issue 4: "Memory limit exceeded"

**Cause**: Large dependencies or data processing

**Fix**:
```bash
aws lambda update-function-configuration \
  --function-name qbot-backend \
  --memory-size 3008  # Max is 10240 MB
```

### Issue 5: Lambda in VPC can't access DynamoDB

**Cause**: No internet access from VPC

**Fix**: Add VPC Endpoint for DynamoDB
```bash
aws ec2 create-vpc-endpoint \
  --vpc-id vpc-xxx \
  --service-name com.amazonaws.REGION.dynamodb \
  --route-table-ids rtb-xxx
```

---

## Monitoring

### CloudWatch Metrics

**Key Metrics to Watch**:
1. **Invocations**: Total requests
2. **Duration**: Response time
3. **Errors**: Failed invocations
4. **Throttles**: Rate limit hits
5. **ConcurrentExecutions**: Parallel instances

**Set Alarms**:
```bash
# Alarm for errors
aws cloudwatch put-metric-alarm \
  --alarm-name qbot-errors \
  --alarm-description "QBot Lambda errors > 10" \
  --metric-name Errors \
  --namespace AWS/Lambda \
  --statistic Sum \
  --period 300 \
  --threshold 10 \
  --comparison-operator GreaterThanThreshold \
  --dimensions Name=FunctionName,Value=qbot-backend \
  --evaluation-periods 1 \
  --alarm-actions arn:aws:sns:REGION:ACCOUNT:alerts
```

### Custom Logging

Your app already has structured logging! In Lambda, logs go to CloudWatch:

```python
# Existing logging in code
logger.info("Processing chat request", extra={
    "tenant_id": tenant_id,
    "user": user_id
})
```

**View in CloudWatch**:
```bash
aws logs tail /aws/lambda/qbot-backend \
  --filter-pattern '{$.tenant_id = "xxx"}' \
  --follow
```

### X-Ray Tracing (Optional)

**Enable**:
```bash
aws lambda update-function-configuration \
  --function-name qbot-backend \
  --tracing-config Mode=Active
```

**Add IAM permission**:
```json
{
  "Effect": "Allow",
  "Action": [
    "xray:PutTraceSegments",
    "xray:PutTelemetryRecords"
  ],
  "Resource": "*"
}
```

---

## Production Checklist

### Before Deployment

- [ ] All unit tests passing
- [ ] Environment variables documented
- [ ] Secrets stored in AWS Secrets Manager
- [ ] IAM roles configured with least privilege
- [ ] VPC security groups configured
- [ ] Qdrant EC2 accessible from Lambda
- [ ] DynamoDB tables created
- [ ] S3 buckets created (if using async ingestion)
- [ ] SQS queues created (if using async ingestion)

### After Deployment

- [ ] Health endpoint returns 200
- [ ] Authentication working
- [ ] Chat endpoint working
- [ ] Qdrant connection verified
- [ ] DynamoDB reads/writes working
- [ ] CloudWatch alarms set up
- [ ] API Gateway custom domain configured (optional)
- [ ] CORS configured for frontend domain
- [ ] Load testing completed
- [ ] Documentation updated with API URL

### Performance Optimization

- [ ] Lambda memory optimized (test 512MB, 1024MB, 2048MB)
- [ ] Cold start time < 5 seconds
- [ ] Qdrant responses < 2 seconds
- [ ] Consider provisioned concurrency for high traffic
- [ ] Lambda layers for dependencies (reduces package size)
- [ ] Connection pooling for DynamoDB

---

## Cost Estimation

### Lambda Costs

**Assumptions**:
- 1M requests/month
- Average duration: 2 seconds
- Memory: 2048 MB

**Monthly Cost**:
```
Compute: $0.0000166667 per GB-second
Requests: $0.20 per 1M requests

= (1,000,000 * 2 * 2GB * $0.0000166667) + (1,000,000 * $0.20/1M)
= $66.67 + $0.20
= ~$67/month
```

**API Gateway**: ~$3.50/million requests
**DynamoDB**: Pay per request (on-demand) or provisioned capacity
**Qdrant EC2**: t3.medium ~$30/month

**Total**: ~$100-150/month for moderate traffic

---

## Next Steps

1. **Deploy to staging**:
   - Create separate Lambda function
   - Test all endpoints
   - Run load tests

2. **Set up CI/CD**:
   - GitHub Actions for auto-deploy
   - Automated testing before deployment

3. **Configure custom domain**:
   - api.yourdomain.com → API Gateway
   - SSL certificate via ACM

4. **Optimize cold starts**:
   - Use provisioned concurrency for critical endpoints
   - Minimize dependencies
   - Keep Lambda warm with CloudWatch Events

5. **Add monitoring dashboard**:
   - CloudWatch Dashboard
   - Grafana for Qdrant metrics
   - Custom business metrics

---

## Summary

✅ **FastAPI → Lambda**: Use Mangum adapter  
✅ **Dependencies**: Lambda layers or deployment package  
✅ **Qdrant**: Connect via HTTP to EC2 IP:6333  
✅ **Environment**: All config via Lambda environment variables  
✅ **API Gateway**: Proxy all requests to Lambda  
✅ **Security**: VPC, security groups, IAM roles  
✅ **Monitoring**: CloudWatch Logs, Metrics, Alarms  

**Your FastAPI app is now serverless! 🚀**
