# AWS Lambda Deployment via Console (UI Guide)

**Step-by-step guide for deploying QBot Backend to AWS Lambda using AWS Console**

---

## 📋 Table of Contents

1. [Prerequisites](#prerequisites)
2. [Step 1: Create Deployment Package](#step-1-create-deployment-package)
3. [Step 2: Create IAM Role](#step-2-create-iam-role)
4. [Step 3: Create Lambda Function](#step-3-create-lambda-function)
5. [Step 4: Configure Environment Variables](#step-4-configure-environment-variables)
6. [Step 5: Create API Gateway](#step-5-create-api-gateway)
7. [Step 6: Test Deployment](#step-6-test-deployment)
8. [Step 7: Monitor and Debug](#step-7-monitor-and-debug)

---

## Prerequisites

Before starting, ensure you have:
- ✅ AWS Account with admin access
- ✅ Project code ready (`/home/ubuntu/CODE`)
- ✅ Qdrant EC2 IP address
- ✅ AWS credentials for Bedrock
- ✅ `lambda_handler.py` created (already done)
- ✅ `mangum` dependency added to `pyproject.toml` (already done)

---

## Step 1: Create Deployment Package

### 1.1 Create Deployment ZIP Locally

Open terminal on your Ubuntu machine:

```bash
cd /home/ubuntu/CODE

# Create package directory
mkdir -p lambda_package
cd lambda_package

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
  langchain-text-splitters \
  requests \
  slowapi

# Copy application code
cp -r ../app .
cp ../lambda_handler.py .
cp ../main.py .

# Create ZIP (max 50MB for direct upload)
zip -r ../qbot-lambda.zip .
cd ..
```

### 1.2 Check Package Size

```bash
ls -lh qbot-lambda.zip

# If > 50MB, you'll need to use S3 upload (see note below)
# If < 50MB, you can upload directly via console
```

**📝 Note**: If your package is larger than 50MB:
- Upload `qbot-lambda.zip` to S3 first
- Use "Upload from S3" option in Lambda console
- Or use Lambda Layers (see original guide)

---

## Step 2: Create IAM Role

### 2.1 Navigate to IAM

1. **Open AWS Console**: https://console.aws.amazon.com
2. **Click**: Services → Security, Identity, & Compliance → **IAM**
3. Or search: Type "IAM" in top search bar

### 2.2 Create Role

1. **Left sidebar** → Click **Roles**
2. **Click**: Orange **"Create role"** button (top right)

### 2.3 Configure Role

**Step 1: Select trusted entity**
- **Trusted entity type**: Select **"AWS service"**
- **Use case**: Select **"Lambda"**
- **Click**: **Next**

**Step 2: Add permissions**
- **Search and select these policies** (check the boxes):
  - ✅ `AmazonDynamoDBFullAccess`
  - ✅ `AmazonS3FullAccess`
  - ✅ `AmazonSQSFullAccess`
  - ✅ `AmazonBedrockFullAccess`
  - ✅ `AWSLambdaBasicExecutionRole`
  
- **Click**: **Next**

**Step 3: Name and create**
- **Role name**: `QBotLambdaRole`
- **Description**: "Execution role for QBot backend Lambda function"
- **Click**: **Create role** (bottom)

✅ **Role created!** Note the ARN (you'll need it later)

---

## Step 3: Create Lambda Function

### 3.1 Navigate to Lambda

1. **AWS Console** → Services → Compute → **Lambda**
2. Or search: "Lambda" in top search bar
3. **Region**: Ensure you're in **us-east-1** (top right dropdown)

### 3.2 Create Function

1. **Click**: Orange **"Create function"** button (right side)

### 3.3 Configure Function

**Function creation page:**

**Basic information:**
- **Option**: Select **"Author from scratch"** (top, should be selected)
- **Function name**: `qbot-backend`
- **Runtime**: Select **"Python 3.12"** from dropdown
- **Architecture**: **x86_64** (default)

**Permissions:**
- **Execution role**: Select **"Use an existing role"**
- **Existing role**: Select **"QBotLambdaRole"** (the one you created)

**Advanced settings** (expand):
- **Enable function URL**: ✅ Check this box
- **Auth type**: **"NONE"** (or "AWS_IAM" if you want secured)
- **Configure CORS**: ✅ Check this

**Click**: **Create function** (bottom orange button)

⏳ Wait a few seconds for function creation...

✅ **Function created!** You'll see success message.

---

## Step 4: Upload Code

### 4.1 Navigate to Code Tab

You should already be on the function page. If not:
1. **Lambda** → **Functions** → **qbot-backend**

### 4.2 Upload ZIP

**In the "Code" tab:**

1. **Scroll down** to "Code source" section
2. **Click**: **Upload from** dropdown button (right side)
3. **Select**: **".zip file"**

**Upload dialog:**
- **Click**: **Upload** button
- **Select**: Your `qbot-lambda.zip` file (browse from `/home/ubuntu/CODE/`)
- **Click**: **Save** (bottom)

⏳ **Wait for upload** (may take 1-2 minutes for large files)

**If file > 50MB:**
1. First upload to S3:
   - S3 Console → Create bucket → Upload `qbot-lambda.zip`
2. Then in Lambda:
   - **Upload from** → **"Amazon S3 location"**
   - Enter S3 URL: `s3://your-bucket-name/qbot-lambda.zip`

### 4.3 Verify Upload

After upload completes:
- **Code source editor** should show your files
- **Expand folders** to verify:
  - ✅ `lambda_handler.py` (at root)
  - ✅ `main.py` (at root)
  - ✅ `app/` folder
  - ✅ Dependencies (fastapi, mangum, etc.)

---

## Step 5: Configure Function Settings

### 5.1 General Configuration

**Click tab**: **Configuration** (top of page, next to "Code")

**Left sidebar** → **General configuration** → **Edit** button

**Settings to change:**
- **Memory**: `2048 MB` (slider or type)
- **Timeout**: `15 min 0 sec` (900 seconds)
- **Ephemeral storage**: `1024 MB`

**Click**: **Save** (bottom)

### 5.2 Environment Variables

**Left sidebar** → **Environment variables** → **Edit**

**Click**: **Add environment variable** button

**Add these variables ONE BY ONE:**

| Key | Value | Notes |
|-----|-------|-------|
| `AWS_ACCESS_KEY` | Your AWS access key | From your .env |
| `AWS_SECRET_KEY` | Your AWS secret key | From your .env |
| `AWS_BEARER_TOKEN_BEDROCK` | Your bearer token | From your .env |
| `AWS_REGION` | `us-east-1` | Or your region |
| `Qdrant_URL` | `http://52.44.135.67:6333` | Your Qdrant EC2 IP |
| `QDRANT_API_KEY` | Your Qdrant API key | From your .env |
| `QDRANT_COLLECTION_NAME` | `aob_documents` | Collection name |
| `USE_DYNAMODB` | `true` | Enable DynamoDB |
| `DYNAMODB_TABLE_NAME` | `qbot_main_table` | Your table name |
| `BEDROCK_LLM_MODEL_ID` | `amazon.nova-lite-v1:0` | Or nova-pro |
| `BEDROCK_EMBEDDING_MODEL_ID` | `amazon.titan-embed-text-v2:0` | Embedding model |
| `BEDROCK_EMBEDDING_DIMENSIONS` | `1024` | Vector size |
| `JWT_SECRET_KEY` | Your 32+ char secret | From your .env |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Token expiry |
| `CORS_ORIGINS` | `["https://yourfrontend.com"]` | Frontend URL |
| `APP_NAME` | `QBot Backend` | App name |
| `DEBUG` | `false` | Production mode |

**For each variable:**
1. Type **Key** in left box
2. Type **Value** in right box
3. Click **Add environment variable** for next one

**After all added** → **Click**: **Save** (bottom)

---

## Step 6: Create API Gateway

### 6.1 Navigate to API Gateway

**AWS Console** → Services → Networking & Content Delivery → **API Gateway**

Or use function URL (easier):
1. Go back to Lambda function page
2. **Configuration** tab → **Function URL**
3. You should see a URL like: `https://abc123.lambda-url.us-east-1.on.aws`
4. **Click** to copy
5. ✅ **This is your API endpoint!** Test it.

**For custom API Gateway:**

### 6.2 Create REST API

1. **Click**: **Create API** (orange button)
2. **Find**: "REST API" (not Private)
3. **Click**: **Build** button

**Create API page:**
- **Choose protocol**: REST
- **Create new API**: New API
- **API name**: `QBot API`
- **Description**: "QBot Backend API"
- **Endpoint type**: Regional

**Click**: **Create API**

### 6.3 Create Resource

1. **Actions** dropdown → **Create Resource**
2. **Resource Name**: `proxy`
3. **Resource Path**: `{proxy+}` (enable greedy parameter)
4. ✅ Check **"Enable API Gateway CORS"**
5. **Click**: **Create Resource**

### 6.4 Create Method

1. **Select** the `{proxy+}` resource
2. **Actions** dropdown → **Create Method**
3. **Dropdown appears** → Select **ANY**
4. **Click checkmark** ✓

**Setup page:**
- **Integration type**: Lambda Function
- **Use Lambda Proxy integration**: ✅ Check this
- **Lambda Region**: us-east-1
- **Lambda Function**: Type `qbot-backend` (autocomplete should find it)

**Click**: **Save**

**Permission popup** ("Add Permission to Lambda Function"):
- **Click**: **OK**

### 6.5 Deploy API

1. **Actions** dropdown → **Deploy API**

**Deploy API dialog:**
- **Deployment stage**: [New Stage]
- **Stage name**: `prod`
- **Stage description**: "Production"

**Click**: **Deploy**

### 6.6 Get API URL

After deployment:
- **Stages** (left sidebar) → **prod**
- **Top of page**: You'll see **Invoke URL**
- Example: `https://abc123.execute-api.us-east-1.amazonaws.com/prod`

✅ **Copy this URL** - this is your API endpoint!

---

## Step 7: Test Deployment

### 7.1 Test Health Endpoint

**In your browser or using curl:**

```bash
# Replace with your actual URL
curl https://YOUR-API-URL/health
```

**Expected response:**
```json
{
  "status": "healthy",
  "latency_ms": 150,
  "timestamp": 1234567890.123,
  "details": {
    "database": "connected"
  },
  "version": "2.0.0"
}
```

### 7.2 Test via Lambda Console

1. **Go to**: Lambda → Functions → qbot-backend
2. **Tab**: **Test** (top)
3. **Click**: **Create new test event**

**Test event:**
```json
{
  "path": "/health",
  "httpMethod": "GET",
  "headers": {},
  "body": null
}
```

**Event name**: `HealthCheckTest`

**Click**: **Save** → **Test**

**Check response** - should see health check JSON

### 7.3 Test Chat Endpoint

**After logging in and getting token:**

```bash
# Get token
TOKEN=$(curl -X POST https://YOUR-API-URL/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"your_password"}' \
  | jq -r '.access_token')

# Test chat
curl -X POST https://YOUR-API-URL/api/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "query": "Hello!",
    "tenant_id": "your_tenant_id"
  }'
```

---

## Step 8: Monitor and Debug

### 8.1 View Logs

**CloudWatch Logs:**

1. **Lambda function page** → **Monitor** tab
2. **Click**: **View CloudWatch logs**
3. Or: Services → CloudWatch → Log groups → `/aws/lambda/qbot-backend`

**Click on latest log stream** to see execution logs

### 8.2 Check Metrics

**Lambda Metrics:**

1. **Monitor** tab on function page
2. View:
   - Invocations (request count)
   - Duration (response time)
   - Errors (failed requests)
   - Throttles (rate limits)

### 8.3 Common Issues

**"Unable to import module"**
- **Check**: Code source has `lambda_handler.py` at root
- **Fix**: Re-upload ZIP with correct structure

**"Task timed out"**
- **Check**: Configuration → Timeout setting
- **Fix**: Increase to 900 seconds (15 min)

**"Memory limit exceeded"**
- **Check**: Configuration → Memory
- **Fix**: Increase to 2048 or 3008 MB

**"Cannot connect to Qdrant"**
- **Check**: Environment variable `Qdrant_URL`
- **Check**: EC2 security group allows port 6333
- **Test**: Manually test `curl http://EC2_IP:6333/collections`

---

## 📊 Complete Deployment Checklist

### Before Deployment
- [ ] Created `lambda_handler.py`
- [ ] Added `mangum` to dependencies
- [ ] Created deployment ZIP package
- [ ] Uploaded ZIP to S3 (if > 50MB)

### AWS Console Steps
- [ ] Created IAM role (`QBotLambdaRole`)
- [ ] Created Lambda function (`qbot-backend`)
- [ ] Uploaded code (ZIP file)
- [ ] Configured memory (2048 MB)
- [ ] Configured timeout (900 sec)
- [ ] Added all environment variables
- [ ] Created API Gateway or got Function URL
- [ ] Tested health endpoint
- [ ] Tested chat endpoint

### Verification
- [ ] Health check returns 200
- [ ] CloudWatch logs show no errors
- [ ] Qdrant connection working
- [ ] DynamoDB accessible
- [ ] Bedrock LLM responding

---

## 🎯 Quick Navigation Summary

**To create Lambda function:**
```
AWS Console → Lambda → Create function
→ Author from scratch → Python 3.12 → Use existing role
→ Upload ZIP → Configure settings → Add env variables
```

**To create API Gateway:**
```
AWS Console → API Gateway → Create API → REST API
→ Create resource (proxy+) → Create method (ANY)
→ Integrate with Lambda → Deploy to stage (prod)
```

**To view logs:**
```
Lambda → Your function → Monitor → CloudWatch logs
```

**To test:**
```
Lambda → Your function → Test → Create test event
→ Or use Function URL / API Gateway URL
```

---

## 🆘 Need Help?

### Stuck on a step?
1. Check CloudWatch logs for errors
2. Verify environment variables are set correctly
3. Test Qdrant connection from local machine first
4. Ensure IAM role has all required permissions

### Resources
- **AWS Lambda Docs**: https://docs.aws.amazon.com/lambda/
- **API Gateway Docs**: https://docs.aws.amazon.com/apigateway/
- **Troubleshooting**: See `docs/AWS_LAMBDA_DEPLOYMENT.md`

---

**Your QBot Backend is now deployed to AWS Lambda! 🎉**

Use your API URL (`https://YOUR-API-ID.execute-api.us-east-1.amazonaws.com/prod`) in your frontend application.
