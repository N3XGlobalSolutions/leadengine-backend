#!/usr/bin/env bash

# ============================================================

# Deploy Backend → DEV (test.qbotglobal.com)

# Lambda: aob-bot-dev + qbot-sqs-worker-dev

# macOS Apple Silicon (M1/M2/M3) Friendly

# ============================================================

set -Eeuo pipefail

# ---------- Config ----------

REGION="us-east-1"
S3_BUCKET="qbot-lambda-deployment"
S3_KEY="dev/qbot-lambda.zip"

LAMBDA_API="aob-bot-dev"
LAMBDA_WORKER="qbot-sqs-worker-dev"

# ---------- Paths ----------

export PATH="/opt/homebrew/bin:$HOME/.cargo/bin:$HOME/.local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR"

# ---------- Helpers ----------

log() {
echo "[$(date '+%H:%M:%S')] $1"
}

check_command() {
command -v "$1" >/dev/null 2>&1 || {
echo "❌ Missing required command: $1"
exit 1
}
}

deploy_lambda() {
local function_name="$1"

log "🚀 Updating Lambda: $function_name"

aws lambda update-function-code \
    --function-name "$function_name" \
    --s3-bucket "$S3_BUCKET" \
    --s3-key "$S3_KEY" \
    --region "$REGION" \
    --no-cli-pager >/dev/null

log "⏳ Waiting for update to complete..."

aws lambda wait function-updated \
    --function-name "$function_name" \
    --region "$REGION"

log "✅ $function_name updated successfully"
}

# ---------- Validation ----------

check_command aws
check_command git
check_command zip

if [ ! -d "$BACKEND_DIR" ]; then
echo "❌ Backend directory not found:"
echo "$BACKEND_DIR"
exit 1
fi

START_TIME=$(date +%s)

# ---------- Build ----------

cd "$BACKEND_DIR"

BRANCH=$(git branch --show-current)
COMMIT=$(git rev-parse --short HEAD)

log "🔧 Branch: $BRANCH"
log "📝 Commit: $COMMIT"

log "📦 Building Lambda package..."
bash create_lambda_package.sh

if [ ! -f "qbot-lambda.zip" ]; then
echo "❌ qbot-lambda.zip not found"
exit 1
fi

ZIP_SIZE=$(du -h qbot-lambda.zip | cut -f1)
log "📦 Package Size: $ZIP_SIZE"

# ---------- Upload ----------

log "☁️ Uploading package to S3..."

aws s3 cp \
  qbot-lambda.zip \
  "s3://$S3_BUCKET/$S3_KEY" \
  --region "$REGION"
log "✅ Upload complete"

# ---------- Deploy ----------

deploy_lambda "$LAMBDA_API"
deploy_lambda "$LAMBDA_WORKER"

# ---------- Done ----------

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

echo ""
echo "===================================="
echo "✅ Deployment Successful"
echo "🌐 Environment : DEV"
echo "🌐 URL         : test.qbotglobal.com"
echo "🌿 Branch      : $BRANCH"
echo "📝 Commit      : $COMMIT"
echo "⏱ Duration    : ${DURATION}s"
echo "===================================="
