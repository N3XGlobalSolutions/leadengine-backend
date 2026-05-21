# AWS Lambda Quick Start Guide

**TL;DR**: Deploy your FastAPI app to AWS Lambda in 5 minutes

---

## 🚀 Quick Commands

### 1. Prepare Code (Already Done!)
```bash
# ✅ lambda_handler.py created
# ✅ pyproject.toml updated with mangum
# ✅ deploy_lambda.sh script created
```

### 2. Set Qdrant URL
```bash
# Get your EC2 Qdrant IP
EC2_IP="YOUR_QDRANT_EC2_IP"  # Example: 54.123.45.67

#Test connection
curl http://$EC2_IP:6333/collections
```

### 3. Deploy Using Script
```bash
# Automated deployment
./deploy_lambda.sh

# Or manual:
# See full guide: docs/AWS_LAMBDA_DEPLOYMENT.md
```

### 4. Set Environment Variables
```bash
aws lambda update-function-configuration \
  --function-name qbot-backend \
  --environment "Variables={
    AWS_ACCESS_KEY=YOUR_KEY,
    AWS_SECRET_KEY=YOUR_SECRET,
    AWS_REGION=us-east-1,
    Qdrant_URL=http://$EC2_IP:6333,
    USE_DYNAMODB=true,
    DYNAMODB_TABLE_NAME=qbot_main_table,
    BEDROCK_LLM_MODEL_ID=amazon.nova-lite-v1:0,
    JWT_SECRET_KEY=your_32_character_secret_key_here,
    CORS_ORIGINS=[\\\"https://yourfrontend.com\\\"]
  }"
```

### 5. Test
```bash
# Test health
aws lambda invoke \
  --function-name qbot-backend \
  --payload '{"path": "/health", "httpMethod": "GET"}' \
  response.json

cat response.json
```

---

## 📁 Files Created

1. **`lambda_handler.py`** - Lambda entry point with Mangum
2. **`deploy_lambda.sh`** - Automated deployment script
3. **`docs/AWS_LAMBDA_DEPLOYMENT.md`** - Complete 900-line guide

---

## 🔧 What Was Modified

### pyproject.toml
```diff
+ "mangum>=0.19.0",
```

### New Files
- `lambda_handler.py` - Lambda ASGI adapter
- `deploy_lambda.sh` - Automated deployment
- `docs/AWS_LAMBDA_DEPLOYMENT.md` - Full documentation

### No Changes Needed
- ✅ `app/core/config.py` - Already supports remote Qdrant
- ✅ `app/core/database.py` - Already uses URL if provided
- ✅ `main.py` - FastAPI app works as-is

---

## 🌐 Qdrant EC2 Connection

### Step 1: Get Qdrant IP
```bash
# AWS Console → EC2 → Your Qdrant Instance → Copy Public IPv4
# Example: 54.123.45.67
```

### Step 2: Update Security Group
```bash
# EC2 → Security Groups → Qdrant Instance SG
# Add Inbound Rule:
#   Type: Custom TCP
#   Port: 6333
#   Source: 0.0.0.0/0 (or Lambda SG)
```

### Step 3: Test Connection
```bash
# From your local machine
curl http://54.123.45.67:6333/collections

# Expected response:
# {"result":{"collections":[{"name":"aob_documents"}]}}
```

### Step 4: Set in Lambda
```bash
# Environment variable
Qdrant_URL=http://54.123.45.67:6333
```

---

## ⚙️ Environment Variables Checklist

**Required**:
- [ ] `AWS_ACCESS_KEY` - For Bedrock
- [ ] `AWS_SECRET_KEY` - For Bedrock
- [ ] `AWS_REGION` - us-east-1
- [ ] `Qdrant_URL` - http://EC2_IP:6333
- [ ] `USE_DYNAMODB` - true
- [ ] `DYNAMODB_TABLE_NAME` - qbot_main_table
- [ ] `JWT_SECRET_KEY` - 32+ character secret

**Optional**:
- [ ] `QDRANT_API_KEY` - If Qdrant has auth
- [ ] `CORS_ORIGINS` - Frontend domains
- [ ] `DEBUG` - false for production

---

## 📊 Architecture

```
Client Request
    ↓
API Gateway (https://xyz.execute-api.region.amazonaws.com/prod)
    ↓
AWS Lambda (qbot-backend)
    ├─→ DynamoDB (conversations, leads, tenants)
    ├─→ Bedrock (LLM + embeddings)
    └─→ Qdrant EC2 (http://IP:6333) - vector database
```

---

## 🧪 Testing

### After Deployment

```bash
# 1. Get API URL
API_URL=$(aws lambda get-function-url-config \
  --function-name qbot-backend \
  --query 'FunctionUrl' --output text)

# 2. Test health
curl $API_URL/health

# 3. Test root
curl $API_URL/

# 4. View logs
aws logs tail /aws/lambda/qbot-backend --follow
```

---

## 🐛 Common Issues

### Issue: Can't connect to Qdrant
```bash
# Check EC2 is running
aws ec2 describe-instances --filters "Name=tag:Name,Values=Qdrant"

# Check security group allows 6333
# Check Qdrant service: ssh to EC2 and run
systemctl status qdrant
```

### Issue: Lambda timeout
```bash
# Increase timeout to 15 minutes
aws lambda update-function-configuration \
  --function-name qbot-backend \
  --timeout 900
```

### Issue: Module import error
```bash
# Verify ZIP structure
unzip -l qbot-lambda.zip | head -20

# Should see:
# lambda_handler.py
# main.py
# app/
# (dependencies)
```

---

## 📚 Full Documentation

See **`docs/AWS_LAMBDA_DEPLOYMENT.md`** for:
- ✅ Detailed architecture diagrams
- ✅ Step-by-step deployment with AWS CLI
- ✅ VPC configuration for private Qdrant
- ✅ Lambda layers for large dependencies
- ✅ API Gateway setup
- ✅ Monitoring and CloudWatch
- ✅ Cost estimation
- ✅ Troubleshooting guide
- ✅ Production checklist

---

## 💰 Cost Estimate

**For 1M requests/month**:
- Lambda: ~$67/month
- API Gateway: ~$3.50/month
- DynamoDB: ~$20-50/month (on-demand)
- Qdrant EC2 (t3.medium): ~$30/month
- **Total**: ~$120-150/month

---

## ✅ Next Steps

1. Run `./deploy_lambda.sh`
2. Set environment variables
3. Setup API Gateway (see full docs)
4. Test all endpoints
5. Monitor CloudWatch logs
6. Configure custom domain (optional)

---

## 🆘 Need Help?

- **Full Guide**: `docs/AWS_LAMBDA_DEPLOYMENT.md`
- **Deployment Script**: `./deploy_lambda.sh`
- **AWS Lambda Docs**: https://docs.aws.amazon.com/lambda/
- **Mangum Docs**: https://mangum.io/

**Ready to deploy? Run `./deploy_lambda.sh`! 🚀**
