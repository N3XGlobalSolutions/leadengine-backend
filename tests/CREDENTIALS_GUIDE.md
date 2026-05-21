# Example: How to Find Your Tenant ID and API Key

## Finding Your Tenant ID

### Method 1: From the Frontend
1. Open your browser's Developer Tools (F12)
2. Go to the Application/Storage tab
3. Look under Local Storage for your frontend URL
4. Find the key that contains tenant information
5. Copy the tenant ID value

### Method 2: From the Backend Database (DynamoDB)
1. Open AWS Console
2. Navigate to DynamoDB
3. Find your `tenants` table
4. Browse items to find your tenant
5. Copy the `tenant_id` field

### Method 3: Check Admin Settings Page
1. Log in to your admin panel at `/admin/login`
2. Navigate to Settings or Client Management
3. The tenant ID should be visible in the UI or URL

## Generating an API Key

### Using the Backend API

**Option 1: Via Admin Panel**
1. Log in to admin panel
2. Navigate to Settings or API Keys section
3. Click "Generate New API Key"
4. Copy the generated key (format: `aob_xxxxx...`)

**Option 2: Via Direct API Call**

```bash
# First, login to get access token
curl -X POST "https://your-api-url.com/prod/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "admin@example.com",
    "password": "your-password"
  }'

# Use the access token to generate API key
curl -X POST "https://your-api-url.com/prod/api/tenants/{tenant_id}/generate-api-key" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

**Option 3: Manually in Database (DynamoDB)**
1. Open DynamoDB console
2. Find your tenant record
3. Look for `config.api_key` field
4. If exists, copy it. If not, generate a new UUID and add it

## Quick Test Configuration Example

Once you have your credentials, update `test_config.json`:

```json
{
  "api_url": "https://0wryhzy1j5.execute-api.us-east-1.amazonaws.com/prod",
  "tenant_id": "abc123-def456-ghi789",
  "api_key": "aob_1234567890abcdef",
  "concurrent_users": [5, 10, 25],
  "messages_per_user": 4,
  "request_timeout": 60
}
```

## Verify Your Configuration

Run the validation script:

```bash
cd d:\qbot\qbot-backend\tests
python validate_setup.py
```

This will check:
- ✅ All required files exist
- ✅ Dependencies are installed
- ✅ Configuration is properly set

## Common Issues

### "Invalid API key" Error
- Make sure the API key starts with `aob_`
- Verify it matches the key in your tenant's config in DynamoDB
- Ensure you're using the correct tenant_id

### "Tenant not found" Error
- Double-check the tenant_id is correct
- Verify the tenant exists and is active in the database

### Connection Timeout
- Verify the API URL is correct
- Check if the backend is running and accessible
- Test with curl first: `curl https://your-api-url.com/prod/api/tenants/{tenant_id}/welcome`
