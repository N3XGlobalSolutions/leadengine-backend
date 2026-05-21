# Sales Bot Lead Capture - Analysis & Implementation Plan

**Created**: 2026-01-04  
**Status**: 🔴 CRITICAL FIXES NEEDED

---

## 🎯 Executive Summary

After analyzing the code and documentation, I can confirm:

### ✅ What's Actually Working
1. Backend **IS** sending `suggested_actions: ["lead_captured"]` ✅
2. Lead qualification loop **IS** working ✅
3. Entity extraction **IS** working ✅
4. Lead creation in DynamoDB **IS** working ✅
5. `ChatResponse` model **DOES** include `suggested_actions` field ✅

### ❌ What's Broken (Confirmed Bugs)
1. Frontend **NOT** checking `suggested_actions` from API response ❌
2. Summary JSON **NOT** being parsed/displayed ❌
3. Action buttons **NOT** automatically triggered ❌
4. Greeting question **NOT** properly integrated ❌
5. Progress indicator **MISSING** ❌

**Verdict**: The documentation is accurate - backend is solid, frontend needs fixes.

---

## 📊 Detailed Analysis

### Analysis 1: Backend Lead Capture Flow

**File**: `/home/ubuntu/CODE/app/routes/v1/chat.py`

**Confirmed Working**:
```python
# Lines 56-140
suggested_actions = []

# When all fields collected:
if not missing_fields and not active_step:
    lead_service.create_lead(lead_create)
    conversation_service.mark_lead_captured(conversation_id)
    suggested_actions.append("lead_captured")  # ✅ THIS WORKS

# Return in response (line 176):
return ChatResponse(
    answer=answer,
    conversation_id=conversation.conversation_id,
    message_id=message_id,
    sources=sources,
    suggested_actions=suggested_actions  # ✅ SENT TO FRONTEND
)
```

**Model Definition** (`app/models/conversation.py`):
```python
class ChatResponse(BaseModel):
    answer: str
    conversation_id: str
    message_id: str
    sources: List[str] = []
    suggested_actions: List[str] = []  # ✅ FIELD EXISTS
```

✅ **Backend is working perfectly!**

### Analysis 2: Frontend Response Handling

**Issue**: Frontend receives `suggested_actions` but doesn't use it.

**Expected Response**:
```json
{
  "answer": "Perfect! Here's your summary: {...}",
  "conversation_id": "conv-123",
  "message_id": "msg-456",
  "sources": [],
  "suggested_actions": ["lead_captured"]  // ← Frontend ignores this!
}
```

**Fix Needed**: Frontend must check this field and set `showActions = true`.

### Analysis 3: Summary Display

**Backend Intent**:
```python
# RAG service should generate response like:
"Perfect! I've collected all your information:

**Summary:**
- Name: John Doe
- Phone: +1234567890
- Location: Whitefield
... 

Would you like to schedule a call with our team?"
```

**Frontend Problem**: 
- No parsing logic for summary
- No special rendering for summary messages
- `ChatBubble.tsx` doesn't handle summary display

### Analysis 4: Greeting

Question Flow

**Current State**: Tenant config has `qualification_questions` but first question isn't always "greeting".

**Example** (from doc):
```typescript
questions: [
  {
    id: "greeting",
    label: "Greeting & Introduction",
    instruction: "Greet warmly and ask for name and phone",
    collects: ["name", "phone"]
  },
  // ... more questions
]
```

**Problem**: Not all tenants have this structure. Need validation.

---

## 🔥 Critical Bugs to Fix

### Bug #1: Action Buttons Don't Appear Automatically

**Location**: Frontend `useChatSession.ts`

**Expected Flow**:
```
Backend sends response with suggested_actions: ["lead_captured"]
    ↓
Frontend receives response
    ↓
Frontend checks response.suggested_actions
    ↓
If contains "lead_captured", set message.showActions = true
    ↓
ChatPage renders ActionButtons
```

**Current Flow**:
```
Backend sends response with suggested_actions: ["lead_captured"]
    ↓
Frontend receives response
    ↓
Frontend IGNORES suggested_actions ❌
    ↓
Action buttons never show ❌
```

**Fix**: Add check in `handleBotResponse()`.

### Bug #2: Summary Not Displayed

**Problem**: Backend generates summary text, frontend shows it as plain text.

**Expected**: Formatted display with all collected data.

**Fix**: 
1. Backend: Ensure consistent summary format (structured JSON or clear markers)
2. Frontend: Parse summary and render specially

### Bug #3: No Progress Indicator

**Problem**: User doesn't know "Question 2 of 6".

**Impact**: Poor UX, users may abandon thinking it's endless.

**Fix**: Track step progress from `active_step` ID or `[STEP:id]` tag.

---

## 🛠️ Implementation Plan

### Phase 1: Fix Critical Bugs (Day 1-2)

#### Task 1.1: Fix Action Button Trigger
**Priority**: 🔴 CRITICAL  
**Effort**: 1 hour  
**Files**: 
- `qbot/hooks/useChatSession.ts`

**Implementation**:
```typescript
// In handleBotResponse() function
const handleBotResponse = (response: ChatResponse) => {
  const { answer, suggested_actions, sources } = response;
  
  // Check for lead_captured action
  const shouldShowActions = suggested_actions?.includes('lead_captured');
  
  addMessage(MessageRole.Bot, answer, {
    showActions: shouldShowActions,  // ← NEW
    sources: sources || []
  });
  
  if (shouldShowActions) {
    setIsLeadCaptured(true);
  }
};
```

**Testing**:
- Complete full qualification flow
- Verify action buttons appear after last question
- Verify no manual trigger needed

---

#### Task 1.2: Improve Summary Display
**Priority**: 🔴 CRITICAL  
**Effort**: 3 hours  
**Files**:
- `qbot-backend/app/services/ai/rag_service.py`
- `qbot/components/chat/ChatBubble.tsx`
- `qbot/hooks/useChatSession.ts`

**Backend Enhancement**:
```python
# In answer_query() when all fields collected
if not missing_fields and not active_step:
    # Build structured summary
    summary_text = "Perfect! Here's what I've collected:\n\n"
    summary_text += "**Your Details:**\n"
    for field, value in current_data.items():
        summary_text += f"- {field.title()}: {value}\n"
    summary_text += "\nWould you like to schedule a call with our team?"
    
    return summary_text, []
```

**Frontend Enhancement**:
```tsx
// ChatBubble.tsx
if (message.isLeadSummary) {
  return (
    <div className="summary-card">
      <h3>📋 Your Information</h3>
      {Object.entries(message.summaryData).map(([key, value]) => (
        <div key={key} className="summary-item">
          <span className="summary-label">{key}:</span>
          <span className="summary-value">{value}</span>
        </div>
      ))}
    </div>
  );
}
```

---

#### Task 1.3: Add Progress Indicator
**Priority**: 🟡 HIGH  
**Effort**: 2 hours  
**Files**:
- `qbot/pages/ChatPage.tsx`
- `qbot/hooks/useChatSession.ts`

**Implementation**:
```tsx
// useChatSession.ts
const [questionProgress, setQuestionProgress] = useState({
  current: 0,
  total: 0
});

// Extract from config
useEffect(() => {
  if (config?.leadQualification?.questions) {
    setQuestionProgress({
      current: 0,
      total: config.leadQualification.questions.length
    });
  }
}, [config]);

// Update on each bot response
const extractStepFromResponse = (answer: string) => {
  const match = answer.match(/\[STEP:(\w+)\]/);
  if (match) {
    const stepId = match[1];
    const stepIndex = config.leadQualification.questions.findIndex(
      q => q.id === stepId
    );
    if (stepIndex !== -1) {
      setQuestionProgress(prev => ({
        ...prev,
        current: stepIndex + 1
      }));
    }
  }
};
```

```tsx
// ChatPage.tsx
{questionProgress.total > 0 && !isChatEnded && (
  <div className="progress-indicator">
    Question {questionProgress.current} of {questionProgress.total}
    <div className="progress-bar">
      <div 
        className="progress-fill"
        style={{
          width: `${(questionProgress.current / questionProgress.total) * 100}%`
        }}
      />
    </div>
  </div>
)}
```

---

### Phase 2: Standardize Greeting Question (Day 3)

#### Task 2.1: Validate/Fix Tenant Configs
**Priority**: 🟡 HIGH  
**Effort**: 2 hours  
**Files**: New migration script

**Script**: `scripts/validate_tenant_greeting.py`
```python
#!/usr/bin/env python3
"""Ensure all tenants have greeting as first qualification question."""

import boto3
from app.core.config import get_settings

settings = get_settings()
dynamodb = boto3.resource('dynamodb', region_name=settings.AWS_REGION)
table = dynamodb.Table(settings.DYNAMODB_TABLE_NAME)

def fix_tenant_greeting(tenant_id):
    """Ensure tenant has greeting question first."""
    # Get tenant
    response = table.get_item(Key={'PK': f'TENANT#{tenant_id}', 'SK': 'PROFILE'})
    tenant = response.get('Item')
    
    if not tenant:
        print(f"Tenant {tenant_id} not found")
        return
    
    config = tenant.get('config', {})
    questions = config.get('qualification_questions', [])
    
    # Check if first question is greeting
    if not questions or questions[0].get('id') != 'greeting':
        # Insert greeting as first question
        greeting_question = {
            'id': 'greeting',
            'label': 'Greeting & Introduction',
            'instruction': f"Greet the user warmly. Introduce yourself as {config.get('bot_name', 'QBot')}. Ask for their name and phone number to get started.",
            'collects': ['name', 'phone']
        }
        
        questions.insert(0, greeting_question)
        
        # Update tenant
        table.update_item(
            Key={'PK': f'TENANT#{tenant_id}', 'SK': 'PROFILE'},
            UpdateExpression='SET config.qualification_questions = :q',
            ExpressionAttributeValues={':q': questions}
        )
        
        print(f"✅ Fixed greeting for tenant {tenant_id}")
    else:
        print(f"✅ Tenant {tenant_id} already has greeting")

# Run for all tenants
def main():
    # Scan for all tenants
    response = table.scan(
        FilterExpression='begins_with(PK, :pk)',
        ExpressionAttributeValues={':pk': 'TENANT#'}
    )
    
    for item in response['Items']:
        tenant_id = item['PK'].replace('TENANT#', '')
        fix_tenant_greeting(tenant_id)

if __name__ == '__main__':
    main()
```

---

### Phase 3: Enhanced Features (Day 4-5)

#### Task 3.1: Quick Replies Based on Active Step
**Priority**: 🟢 MEDIUM  
**Effort**: 2 hours

**Current**: Keyword-based quick replies  
**Improved**: Context-aware based on active step

```typescript
// useChatSession.ts
const getQuickReplies = (activeStepId: string) => {
  const step = config.leadQualification.questions.find(q => q.id === activeStepId);
  if (!step || !step.optionsKey) return [];
  
  const options = config.leadQualification.options[step.optionsKey];
  return options || [];
};
```

#### Task 3.2: Data Validation
**Priority**: 🟢 MEDIUM  
**Effort**: 3 hours

```python
# llm_service.py - enhance extract_lead_info()
import re

def validate_extracted_data(data: dict) -> tuple[dict, list[str]]:
    """Validate extracted lead data."""
    errors = []
    
    # Phone validation
    if 'phone' in data:
        phone = data['phone']
        if not re.match(r'^\+?[\d\s-()]{10,}$', phone):
            errors.append('phone')
    
    # Email validation
    if 'email' in data:
        email = data['email']
        if not re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', email):
            errors.append('email')
    
    # Name validation
    if 'name' in data:
        name = data['name']
        if len(name) < 2 or len(name) > 100:
            errors.append('name')
    
    return data, errors
```

---

## 📋 Complete TODO Checklist

### 🔴 URGENT (Must Fix Immediately)

- [ ] **Fix action button auto-trigger**
  - [ ] Add `suggested_actions` check in `useChatSession.ts`
  - [ ] Set `showActions: true` when `lead_captured` received
  - [ ] Test end-to-end flow
  - [ ] Verify buttons appear automatically

- [ ] **Improve summary display**
  - [ ] Backend: Format summary with clear structure
  - [ ] Frontend: Add summary parsing logic
  - [ ] Frontend: Create special summary card UI
  - [ ] Test with multiple tenants

- [ ] **Add progress indicator**
  - [ ] Extract total questions from config
  - [ ] Track current question from `[STEP:id]` tags
  - [ ] Display "Question X of Y"
  - [ ] Add progress bar visual

### 🟡 HIGH PRIORITY (This Week)

- [ ] **Standardize greeting question**
  - [ ] Create validation script
  - [ ] Run on all tenants
  - [ ] Verify first question is always greeting
  - [ ] Test chat start flow

- [ ] **Improve quick replies**
  - [ ] Match to active step's `optionsKey`
  - [ ] Display relevant options only
  - [ ] Test with all question types

- [ ] **Error handling**
  - [ ] Handle missing `[STEP:id]` tags
  - [ ] Retry on LLM failures
  - [ ] Show user-friendly error messages

### 🟢 MEDIUM PRIORITY (Next 2 Weeks)

- [ ] **Data validation**
  - [ ] Phone number format validation
  - [ ] Email format validation
  - [ ] Re-ask if invalid

- [ ] **Multi-tenant audit**
  - [ ] Check all tenants have valid config
  - [ ] Fix incomplete configurations
  - [ ] Remove hardcoded fallbacks

- [ ] **Analytics tracking**
  - [ ] Track drop-off per question
  - [ ] Time spent per question
  - [ ] Conversion metrics

---

## 🧪 Testing Plan

### Test 1: Happy Path
```
1. User clicks "Start Conversation"
2. Bot greets and asks for name/phone
3. User provides: "Hi I'm John, +1234567890"
4. Bot asks location question
5. User answers all 6 questions
6. Bot displays formatted summary ✅
7. Action buttons appear automatically ✅
8. User clicks "Schedule a Call"
9. Success message shows ✅
10. Lead stored in DynamoDB ✅
```

### Test 2: With User Questions
```
1-4. Same as above
5. User asks: "What's the price range?"
6. Bot answers from RAG with sources
7. Bot continues with next qualification question ✅
8. Complete remaining questions
9. Summary + Actions appear ✅
```

### Test 3: Edge Cases
```
- Invalid phone number → Re-ask
- User refreshes page → Resume conversation
- User abandons → Conversation saved (24h expiry)
- LLM fails → Retry with fallback
- Missing config → Show error gracefully
```

---

## 📊 Success Metrics

### Before Fixes
- ❌ Action buttons: Manual trigger only
- ❌ Summary: Plain text only
- ❌ Progress: Unknown

### After Fixes
- ✅ Action buttons: Auto-appear after lead capture
- ✅ Summary: Formatted card with all data
- ✅ Progress: "Question 3 of 6" indicator
- ✅ Quick replies: Context-aware
- ✅ Validation: Phone/email check
- ✅ Error handling: Graceful degradation

---

## 🎯 Acceptance Criteria

### Definition of Done

1. **Action Buttons**
   - [ ] Appear automatically when all questions answered
   - [ ] No manual trigger needed
   - [ ] Works for all tenants

2. **Summary Display**
   - [ ] Shows formatted card with collected data
   - [ ] Clear visual distinction from chat messages
   - [ ] Includes all qualification fields

3. **Progress Indicator**
   - [ ] Shows current question number
   - [ ] Updates in real-time
   - [ ] Visual progress bar

4. **Greeting Question**
   - [ ] All tenants have greeting as first question
   - [ ] Consistent experience across tenants
   - [ ] No code changes needed per tenant

5. **End-to-End Flow**
   - [ ] User completes full flow without issues
   - [ ] Lead created in DynamoDB
   - [ ] Conversation marked as lead_captured
   - [ ] Action buttons functional

---

## 🚀 Next Steps

### Immediate Actions (Today)

1. **Start with Task 1.1** (Action button fix) - 1 hour
2. **Deploy and test** - 30 min
3. **Move to Task 1.2** (Summary display) - 3 hours
4. **End-to-end testing** - 1 hour

### This Week

- Complete Phase 1 (Critical bugs)
- Complete Phase 2 (Greeting standardization)
- Begin Phase 3 (Enhanced features)

### Success Milestone

**By end of week**: 
- All 3 critical bugs fixed ✅
- All tenants have standardized greeting ✅
- Complete happy path working perfectly ✅

---

## 💡 Technical Notes

### Why Action Buttons Don't Work

**Root Cause**: Frontend receives `suggested_actions` in API response but doesn't check it.

**Evidence**:
- Backend sends it ✅ (line 176 in chat.py)
- Model includes it ✅ (ChatResponse in conversation.py)
- Frontend receives it ✅ (but ignores in useChatSession.ts)

**Fix**: One-line change + test.

### Why Summary Isn't Formatted

**Root Cause**: No special handling for summary messages.

**Current**: Summary is plain text like any other message.  
**Needed**: Parse summary data and render as card.

### Why Progress Hidden

**Root Cause**: Feature not implemented.

**Data Available**:
- Config has total questions ✅
- Responses have `[STEP:id]` tags ✅
- Just need to connect the dots ✅

---

## ✅ Conclusion

**Good News**: 
- Backend is solid and working correctly ✅
- Models have all needed fields ✅
- Data flows correctly ✅

**Bad News**:
- Frontend isn't using the data ❌
- Missing UI components ❌
- Need quick fixes ❌

**Great News**:
- All fixes are straightforward ✅
- No architecture changes needed ✅
- Can be done in 1-2 days ✅

**Ready to implement? Let's do this!** 🚀
