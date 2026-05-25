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

# Read direct dependencies from pyproject.toml (avoids massive transitive dep tree from uv export)
python3 -c "
import tomllib, sys
with open('../pyproject.toml', 'rb') as f:
    data = tomllib.load(f)
deps = data['project']['dependencies']
# Exclude type-stub packages (not needed at runtime)
runtime_deps = [d for d in deps if not any(x in d.lower() for x in ('stubs', 'types-', 'mypy-boto3'))]
print('\n'.join(runtime_deps))
" > /tmp/lambda-requirements.txt
echo "📋 Packages to install: $(wc -l < /tmp/lambda-requirements.txt)"



# Create a temporary virtual environment with Python 3.12 (matches Lambda)
cd ..
mkdir -p .lambda_venv
cd .lambda_venv

# Create a temporary virtual environment
# Create a temporary virtual environment with Python 3.12 (matches Lambda)
uv venv .venv_lambda --python 3.12
source .venv_lambda/bin/activate

# Install all dependencies from the lock-resolved requirements
uv pip install -r /tmp/lambda-requirements.txt

# Copy site-packages to lambda_package
echo "📁 Copying dependencies to package directory..."
cp -r .venv_lambda/lib/python3.*/site-packages/* ../lambda_package/

deactivate
cd ..

# Copy application code
echo "📋 Copying application code..."
cd lambda_package
cp -r ../app .
cp ../lambda_handler.py .
cp ../main.py .

# Remove unnecessary files to reduce size
echo "🗑️  Removing unnecessary files..."
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
# Keep .dist-info for package metadata (required by pydantic and other packages)
# find . -type d -name "*.dist-info" -exec rm -rf {} + 2>/dev/null || true
find . -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true
find . -type f -name "*.pyc" -delete
find . -type f -name "*.pyo" -delete

# Create ZIP
echo "📦 Creating ZIP file..."
zip -r -q ../qbot-lambda.zip .
cd ..

# Cleanup temp venv
echo "🧹 Cleaning up..."
rm -rf .lambda_venv

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
