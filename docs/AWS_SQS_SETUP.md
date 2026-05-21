# AWS SQS & Lambda Setup Guide

This guide covers how to set up the infrastructure for **Async Document Ingestion** using AWS S3, SQS, and Lambda.

---

## 1. Create S3 Bucket (Storage)
This bucket will temporarily store uploaded files before processing.

**Using AWS CLI:**
```bash
aws s3 mb s3://aob-bot-documents --region us-east-1
```
*Replace `aob-bot-documents` with a unique name.*

**Configuration:**
- **Block Public Access**: Enabled (On).
- **Encryption**: SSE-S3 (Default).

---

## 2. Create SQS Queue (Message Queue)
This queue triggers the Lambda worker.

**Using AWS CLI:**
```bash
aws sqs create-queue --queue-name aob-bot-ingestion-queue --region us-east-1
```
*(Note: This creates a **Standard Queue** by default. Do not choose FIFO. Standard is cheaper, faster, and sufficient since our worker handles idempotency.)*

**Copy the Queue URL** from the output. It looks like:
`https://sqs.us-east-1.amazonaws.com/123456789012/aob-bot-ingestion-queue`

---

## 3. Create IAM Role for Lambda
The Lambda function needs permission to read SQS, read S3, and write logs.

1. **Create Policy JSON** (`policy.json`):
   ```json
   {
       "Version": "2012-10-17",
       "Statement": [
           {
               "Effect": "Allow",
               "Action": [
                   "sqs:ReceiveMessage",
                   "sqs:DeleteMessage",
                   "sqs:GetQueueAttributes",
                   "s3:GetObject",
                   "logs:CreateLogGroup",
                   "logs:CreateLogStream",
                   "logs:PutLogEvents"
               ],
               "Resource": "*"
           }
       ]
   }
   ```

2. **Create Role & Attach Policy**:
   ```bash
   # Create Role
   aws iam create-role --role-name AOBBotWorkerRole --assume-role-policy-document '{"Version": "2012-10-17","Statement": [{"Effect": "Allow","Principal": {"Service": "lambda.amazonaws.com"},"Action": "sts:AssumeRole"}]}'

   # Attach Policy
   aws iam put-role-policy --role-name AOBBotWorkerRole --policy-name AOBBotWorkerAccess --policy-document file://policy.json
   ```

---

## 4. Deploy Worker Lambda
You need to package the application code for Lambda.

1. **Prepare Deployment Package**:
   ```bash
   cd /home/harshan/aob_bot
   
   # Install dependencies to a local folder
   pip install -r requirements.txt -t package/
   
   # Copy app code
   cp -r app package/
   
   # Zip it
   cd package
   zip -r ../deployment_package.zip .
   cd ..
   ```

2. **Create Lambda Function**:
   ```bash
   aws lambda create-function --function-name AOBBotWorker \
     --zip-file fileb://deployment_package.zip \
     --handler app.worker.handler \
     --runtime python3.11 \
     --role arn:aws:iam::YOUR_ACCOUNT_ID:role/AOBBotWorkerRole \
     --timeout 300 \
     --memory-size 512 \
     --environment "Variables={AWS_S3_BUCKET_NAME=aob-bot-documents,QDRANT_URL=...,QDRANT_API_KEY=...,AWS_BEARER_TOKEN_BEDROCK=...}"
   ```

3. **Add SQS Trigger**:
   ```bash
   aws lambda create-event-source-mapping \
     --function-name AOBBotWorker \
     --batch-size 1 \
     --event-source-arn arn:aws:sqs:us-east-1:YOUR_ACCOUNT_ID:aob-bot-ingestion-queue
   ```

---

## 5. Configure API Application
Update your `.env` file on the API server (EC2 or Lambda API) to point to these new resources.

```env
# Enable Async Mode
USE_ASYNC_INGESTION=true

# Resource Config
AWS_S3_BUCKET_NAME=aob-bot-documents
AWS_SQS_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/123456789012/aob-bot-ingestion-queue
```

---

## 6. Verification
1. Start the API.
2. Call `POST /api/documents/ingest`.
3. Checks:
   - API returns `202 Accepted` immediately.
   - S3 bucket shows new file.
   - SQS queue messages count goes up briefly then down (consumed).
   - Lambda Logs (CloudWatch) show: `Successfully processed ...`.
