#!/bin/bash
# Lambda Package Creation Script (UV Compatible)
# This script creates a deployment package using uv instead of pip

set -e

echo "🚀 Creating Lambda Deployment Package with UV"
echo "=============================================="

# Clean up any previous build
echo "🧹 Cleaning previous build..."
rm -rf lambda_package
rm -f qbot-lambda.zip

# Create package directory
mkdir -p lambda_package
cd lambda_package

echo "📦 Installing dependencies using UV (from pyproject.toml)..."

# Curated runtime-only package list — dynamically excludes heavy packages
# not actually imported by the app (google-generativeai, motor, pymongo)
# which would push the zip over Lambda's 250MB unzipped limit.
python3 -c "
import tomllib
with open('../pyproject.toml', 'rb') as f:
    data = tomllib.load(f)
# Packages NOT needed at Lambda runtime (unused in app/ code or type-stubs only)
EXCLUDE = {
    'google-generativeai', 'motor', 'pymongo',
    'boto3-stubs', 'botocore-stubs',
}
deps = data['project']['dependencies']
runtime = [d for d in deps if not any(x in d.lower() for x in EXCLUDE)]
print('\n'.join(runtime))
" > /tmp/lambda-requirements.txt
echo "📋 Packages to install: $(wc -l < /tmp/lambda-requirements.txt)"


# Create a temporary virtual environment with Python 3.12 (matches Lambda)
cd ..
echo "🐳 Installing dependencies inside Lambda container..."

docker run --rm \
  -v "$PWD/lambda_package":/var/task \
  -v "/tmp/lambda-requirements.txt:/tmp/requirements.txt" \
  public.ecr.aws/lambda/python:3.12 \
  pip install \
    --no-cache-dir \
    -r /tmp/requirements.txt \
    -t /var/task

# Check size
SIZE=$(du -h qbot-lambda.zip | cut -f1)
echo ""
echo "✅ Package created successfully!"
echo "📊 Package size: $SIZE"
echo "📦 File: $(pwd)/qbot-lambda.zip"

if [ -f qbot-lambda.zip ]; then
    # Check if over 50MB
    SIZE_BYTES=$(stat -f%z qbot-lambda.zip 2>/dev/null || stat -c%s qbot-lambda.zip)
    if [ $SIZE_BYTES -gt 52428800 ]; then
        echo ""
        echo "⚠️  WARNING: Package is larger than 50MB"
        echo "   You'll need to upload to S3 first, then reference in Lambda"
        echo "   Or use Lambda Layers (see documentation)"
    else
        echo "✅ Size is good for direct upload to Lambda console!"
    fi
fi

echo ""
echo "🎯 Next steps:"
echo "   1. Upload qbot-lambda.zip to AWS Lambda via console"
echo "   2. Or upload to S3 if file is too large"
echo "   3. See: docs/AWS_LAMBDA_CONSOLE_GUIDE.md"
