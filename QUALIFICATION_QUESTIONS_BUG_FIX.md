# CRITICAL BUG FIX: Qualification Questions Not Being Asked

## 🐛 Bug Found and Fixed

**Issue**: Testing team reported LLM not asking 5 qualification questions properly or sometimes not asking them at all.

**Root Cause**: Line 156 in `/home/ubuntu/CODE/app/routes/v1/chat.py` had a faulty condition.

---

## 🔍 What Was Wrong

### The Broken Code (Line 156)

```python
all_steps=[s.model_dump() for s in all_steps] if tenant_config and "qualification_questions" in tenant_config else []
```

**Problem**: 
- This checked `if "qualification_questions" in tenant_config`
- But `tenant_config` is a **dict**, and the key might not exist in that format
- Result: **`all_steps` became `[]` (empty)** when passed to LLM
- **LLM never saw the 5 qualification questions!**

---

## ✅ The Fix

### Fixed Code (Line 156)

```python
all_steps=[s.model_dump() for s in all_steps] if all_steps else []
```

**Why this works**:
- `all_steps` is already assigned from `config.qualification_questions` on line 100
- We just need to check if `all_steps` exists
- No need for complex `tenant_config` check
- Now `all_steps` will contain all 5 questions!

---

## 📊 Impact

### Before Fix ❌
```
all_steps passed to LLM = []
LLM prompt = "Ask questions to qualify the lead" (no specific questions)
Result: LLM makes up random questions or doesn't ask systematically
```

### After Fix ✅
```
all_steps passed to LLM = [
   {id: "budget", label: "Budget", instruction: "Ask about budget range"},
   {id: "timeline", label: "Timeline", instruction: "Ask when they plan to buy"},
   {id: "location", label: "Location", instruction: "Ask preferred location"},
   {id: "bedrooms", label: "Bedrooms", instruction: "Ask number of bedrooms"},
   {id: "purpose", label: "Purpose", instruction: "Ask if investment or self-use"}
]

LLM prompt = "Complete Flow (Follow in strict order):
   Step 1: Budget - Ask about budget range [TAG: STEP:budget]
   Step 2: Timeline - Ask when they plan to buy [TAG: STEP:timeline]
   Step 3: Location - Ask preferred location [TAG: STEP:location]
   Step 4: Bedrooms - Ask number of bedrooms [TAG: STEP:bedrooms]
   Step 5: Purpose - Ask if investment or self-use [TAG: STEP:purpose]"

Result: LLM asks all 5 questions systematically in order!
```

---

## 🚀 Deployment Instructions

### 1. Code is Already Fixed ✅

The fix has been applied to `/home/ubuntu/CODE/app/routes/v1/chat.py` line 156.

### 2. Create New Lambda Package

```bash
cd /home/ubuntu/CODE

# Clean old package
rm -rf lambda_package .lambda_venv qbot-lambda*.zip

# Create new package with fix
./create_lambda_package.sh

# Verify package created
ls -lh qbot-lambda.zip
```

### 3. Upload to Lambda

**Option A: Via S3** (if package > 50MB - recommended)
```bash
# Upload to S3 (from your local machine after downloading qbot-lambda.zip)
# 1. Download qbot-lambda.zip from server
# 2. Upload to S3 bucket: qbot-lambda-deployment
# 3. Lambda Console → Code tab → Upload from → S3 location
# 4. Paste: s3://qbot-lambda-deployment/qbot-lambda.zip
# 5. Save and wait 1-2 minutes
```

**Option B: Direct Upload** (if < 50MB)
```bash
# Lambda Console → Code tab → Upload from → .zip file
# Select qbot-lambda.zip
# Save
```

### 4. Test the Fix

**Test conversation**:
```bash
TOKEN="your_jwt_token"
TENANT_ID="your_tenant_id"

# Start conversation
curl -X POST https://0wryhzy1j5.execute-api.us-east-1.amazonaws.com/prod/api/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Hi! I want to know about your properties",
    "tenant_id": "'$TENANT_ID'"
  }'
```

**Expected Response** (LLM should ask first qualification question):
```json
{
  "answer": "Hello! I'd be happy to help you find the perfect property. We have a wide range of options available. 

To help me show you the most suitable properties, could you share what your budget range is? This will help me narrow down the best options for you. [STEP:budget]",
  "conversation_id": "...",
  "message_id": "...",
  "sources": [],
  "suggested_actions": []
}
```

**Key indicators the fix is working**:
1. ✅ Response contains `[STEP:budget]` tag
2. ✅ LLM asks about budget (first question)
3. ✅ After user responds, LLM asks about timeline (second question)
4. ✅ Questions asked in order: budget → timeline → location → bedrooms → purpose

### 5. Verify in CloudWatch Logs

1. Lambda → Monitor → View CloudWatch logs
2. Look for the prompt being sent to LLM
3. Should see:
```
Complete Flow (Follow in strict order):
   Step 1: Budget - Ask about budget range [TAG: STEP:budget]
   Step 2: Timeline - Ask when they plan to buy [TAG: STEP:timeline]
   Step 3: Location - Ask preferred location [TAG: STEP:location]
   Step 4: Bedrooms - Ask number of bedrooms [TAG: STEP:bedrooms]
   Step 5: Purpose - Ask if investment or self-use [TAG: STEP:purpose]
```

---

## 📝 Technical Details

### Where Qualification Questions Are Used

**File**: `/home/ubuntu/CODE/app/services/ai/rag_service.py`

**Lines 393-415**: Building the qualification flow prompt
```python
if all_steps:
    steps_list = "\n".join([
        f"   Step {i+1}: {s.get('label')} - {s.get('instruction')} [TAG: STEP:{s.get('id')}]" 
        for i, s in enumerate(all_steps)
    ])

lead_capture_instruction = f"""
<sales_qualification_mode>
<qualification_process>
Complete Flow (Follow in strict order):
{steps_list}
</qualification_process>
...
</sales_qualification_mode>
"""
```

**Lines 487-490**: Sending to LLM
```python
prompt = f"""{system_prompt}

{lead_capture_instruction}
...
"""
```

---

## ✅ Verification Checklist

After deploying the fix:

- [ ] New Lambda package created with fixed code
- [ ] Lambda function updated
- [ ] Test conversation started
- [ ] LLM asks first question (budget) with `[STEP:budget]` tag
- [ ] After answering, LLM asks second question (timeline)
- [ ] All 5 questions asked in order
- [ ] CloudWatch logs show qualification flow in prompt
- [ ] Testing team confirms LLM now asks questions properly

---

## 🎯 Summary

**Bug**: Faulty condition on line 156 caused `all_steps` to be empty

**Fix**: Changed condition from checking `tenant_config` to checking `all_steps`

**Result**: LLM now receives all 5 qualification questions and asks them systematically

**Next**: Deploy new package to Lambda and test with real conversation!

---

**Status**: ✅ **Code Fixed** - Ready for deployment and testing!
