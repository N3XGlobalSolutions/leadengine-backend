# Lead Management Frontend UI - Data Schema & Design Guide

## Overview

This document outlines all the data stored in the QBot lead management system and provides detailed UI/UX recommendations for displaying it in a tenant dashboard.

---

## 📊 Data Available for Display

### 1. Lead Core Data

From `Lead` model:

| Field | Type | Description | Display |
|-------|------|-------------|---------|
| `lead_id` | UUID | Unique identifier | Hidden/Debug only |
| `tenant_id` | UUID | Organization identifier | Hidden (implicit) |
| `conversation_id` | UUID | Linked conversation | Link to conversation |
| `name` | String (Optional) | Lead's name | **Primary Display** |
| `email` | Email (Optional) | Lead's email | **Contact Info** |
| `phone` | String (Optional) | Lead's phone | **Contact Info** |
| `company` | String (Optional) | Company name | Secondary info |
| `message` | String (Optional) | Lead's initial message | Preview/Details |
| `status` | Enum | Current stage | **Status Badge** |
| `created_at` | DateTime | Capture timestamp | Time ago / Date |
| `metadata` | Dict | Custom data | Expandable JSON |

### 2. Lead Status Lifecycle

```python
class LeadStatus(Enum):
    NEW = "new"              # 🆕 Just captured
    CONTACTED = "contacted"  # 📞 Team reached out
    QUALIFIED = "qualified"  # ✅ Meets criteria
    CONVERTED = "converted"  # 🎉 Became customer
    LOST = "lost"           # ❌ Didn't convert
```

### 3. Conversation Context

From linked `Conversation`:

| Field | Description | Use in UI |
|-------|-------------|-----------|
| `message_count` | Total messages | Engagement indicator |
| `started_at` | When chat began | Timeline context |
| `last_message_at` | Last activity | Recency indicator |
| `extracted_lead_data` | Collected fields during chat | Show data quality |
| `is_active` | Chat still ongoing | Active/Inactive badge |

### 4. Messages History

From `Message` array:

| Field | Description | Display |
|-------|-------------|---------|
| `role` | "user" or "assistant" | Speaker label |
| `content` | Message text | Chat bubble |
| `timestamp` | When sent | Time |
| `sources` | Documents referenced | Source tags |

---

## 🎨 Frontend UI Design Recommendations

### Layout 1: **Lead List View** (Main Dashboard)

**Purpose**: Overview of all leads for quick scanning

```
┌─────────────────────────────────────────────────────────────┐
│  🔍 Filter & Search                                         │
├─────────────────────────────────────────────────────────────┤
│  Status: [All ▼]  Date: [Last 30 days ▼]  🔎 Search...     │
├─────────────────────────────────────────────────────────────┤
│  ┌──────────────────────────────────────────────────────┐  │
│  │ 🆕 NEW (12)  📞 CONTACTED (8)  ✅ QUALIFIED (5)      │  │
│  │ 🎉 CONVERTED (3)  ❌ LOST (2)                        │  │
│  └──────────────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Lead Name         Contact         Status      Created     │
│  ─────────────────────────────────────────────────────────│
│  👤 John Doe        📧 john@...     🆕 NEW      2 mins ago │
│     ABC Corp        📞 +1-555...                           │
│     "I'm interested in..."                    [View →]    │
│  ───────────────────────────────────────────────────────  │
│  👤 Sarah Smith     📧 sarah@...    📞 CONTACTED  1h ago  │
│     TechCo          📞 +1-555...                           │
│     "Looking for pricing..."                  [View →]    │
│  ───────────────────────────────────────────────────────  │
│  ...more leads...                                          │
└─────────────────────────────────────────────────────────────┘

Pagination: « Previous  1 2 3 4 5  Next »
```

**Key Features**:
- **Status Filters**: Quick filter by lead stage
- **Time Filters**: Today, This Week, This Month, Custom Range
- **Search**: By name, email, company
- **Sort**: By date (newest/oldest), status, name
- **Pagination**: 20-50 leads per page
- **Quick Actions**: Click name to view details

---

### Layout 2: **Lead Detail View** (Individual Lead Page)

**Purpose**: Complete view of single lead with full conversation

```
┌─────────────────────────────────────────────────────────────┐
│  ← Back to Leads                                            │
├─────────────────────────────────────────────────────────────┤
│  👤 JOHN DOE                              🆕 NEW  [Change ▼]│
│  ABC Corporation                          Created: 2h ago   │
│                                                             │
│  ┌──────────────────────┐  ┌──────────────────────────┐   │
│  │ 📧 CONTACT INFO      │  │ 💬 CONVERSATION STATS    │   │
│  │ Email: john@abc.com  │  │ Messages: 12             │   │
│  │ Phone: +1-555-0123   │  │ Duration: 15 mins        │   │
│  │ Company: ABC Corp    │  │ Started: 2h ago          │   │
│  └──────────────────────┘  │ Last msg: 5 mins ago     │   │
│                            └──────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ 📝 INITIAL MESSAGE                                  │  │
│  │ "I'm interested in your 3BHK apartments in         │  │
│  │  downtown. What's the price range?"                │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ 💬 FULL CONVERSATION HISTORY                        │  │
│  ├─────────────────────────────────────────────────────┤  │
│  │ 👤 John Doe  2h ago                                 │  │
│  │ I'm interested in 3BHK apartments                   │  │
│  │                                                     │  │
│  │ 🤖 Assistant  2h ago                                │  │
│  │ Hello John! Great to hear! We have several 3BHK... │  │
│  │ 📄 Sources: BRD_Document.md                        │  │
│  │                                                     │  │
│  │ 👤 John Doe  2h ago                                 │  │
│  │ What's the price range?                            │  │
│  │                                                     │  │
│  │ 🤖 Assistant  2h ago                                │  │
│  │ Our 3BHK apartments range from ₹60L to ₹80L...    │  │
│  │ ...                                                 │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ 🏷️ METADATA (Custom Fields)                        │  │
│  │ Budget: ₹60-70 Lakhs                                │  │
│  │ Location Preference: Downtown                       │  │
│  │ Timeline: 3-6 months                                │  │
│  │ Lead Source: Widget                                 │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                             │
│  Actions:                                                  │
│  [📞 Mark as Contacted] [✅ Qualify] [📧 Send Email]      │
└─────────────────────────────────────────────────────────────┘
```

---

### Layout 3: **Analytics Dashboard** (Overview Page)

**Purpose**: High-level metrics and trends

```
┌─────────────────────────────────────────────────────────────┐
│  📊 LEAD ANALYTICS - Last 30 Days                          │
├─────────────────────────────────────────────────────────────┤
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │ 🆕 NEW   │  │ 📞 CONT  │  │ ✅ QUAL  │  │ 🎉 CONV  │  │
│  │   28     │  │   15     │  │   8      │  │   3      │  │
│  │  +12%    │  │  +5%     │  │  -2%     │  │  +50%    │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ 📈 LEAD TREND (Daily)                               │  │
│  │ 20│                                          ●       │  │
│  │ 15│                                 ●     ●          │  │
│  │ 10│                       ●    ●                    │  │
│  │  5│           ●     ●                               │  │
│  │  0└──────────────────────────────────────────────  │  │
│  │    Mon Tue Wed Thu Fri Sat Sun                      │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ 🏆 CONVERSION FUNNEL                                │  │
│  │                                                     │  │
│  │ New           ████████████████████████  100 (100%)  │  │
│  │ Contacted     ████████████████          54  (54%)   │  │
│  │ Qualified     ██████████                32  (32%)   │  │
│  │ Converted     ███                       8   (8%)    │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ ⏱️ AVERAGE RESPONSE TIME                            │  │
│  │ First Contact: 2.5 hours                            │  │
│  │ Lead Qualification: 24 hours                        │  │
│  │ Conversion: 7 days                                  │  │
│  └─────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 🔌 API Endpoints for Frontend

### Get All Leads (with filters)
```http
GET /api/leads/tenants/{tenant_id}/all?status=new&skip=0&limit=20
Authorization: Bearer {jwt_token}

Response:
[
  {
    "lead_id": "uuid-123",
    "tenant_id": "tenant-456",
    "conversation_id": "conv-789",
    "name": "John Doe",
    "email": "john@example.com",
    "phone": "+1-555-0123",
    "company": "ABC Corp",
    "message": "I'm interested in...",
    "status": "new",
    "created_at": "2025-12-28T10:00:00Z",
    "metadata": {
      "budget": "60-70L",
      "location": "Downtown"
    }
  }
]
```

### Get Single Lead
```http
GET /api/leads/{lead_id}
Authorization: Bearer {jwt_token}

Response: {Lead object with full details}
```

### Update Lead Status
```http
PATCH /api/leads/{lead_id}/status
Authorization: Bearer {jwt_token}
Body: {"status": "contacted"}

Response: {Updated Lead object}
```

### Get Conversation for Lead
```http
GET /api/conversations/{conversation_id}
Authorization: Bearer {jwt_token}

Response: {Conversation with messages array}
```

---

## 🎯 Key UI/UX Recommendations

### 1. **Status Badges** - Visual Color Coding
```
🆕 NEW          → Blue   (#3B82F6)
📞 CONTACTED    → Yellow (#F59E0B)
✅ QUALIFIED    → Green  (#10B981)
🎉 CONVERTED    → Purple (#8B5CF6)
❌ LOST         → Red    (#EF4444)
```

### 2. **Time Display** - User-Friendly Format
```javascript
// Use relative time for recent leads
"2 mins ago"
"1 hour ago"
"3 days ago"

// Use absolute date for older leads
"Dec 15, 2025"
"Nov 28, 2025, 3:45 PM"
```

### 3. **Search & Filter** - Multi-dimensional
- **Text Search**: Name, email, company, message content
- **Status Filter**: Multi-select checkboxes
- **Date Range**: Custom date picker
- **Sort Options**: Date, Name, Status
- **Saved Filters**: Allow saving common filter combinations

### 4. **Actions** - Context-Aware
```
If status = NEW:
  → [📞 Contact Lead]  [✅ Qualify]  [❌ Mark Lost]

If status = CONTACTED:
  → [✅ Qualify]  [📧 Send Follow-up]  [📅 Schedule Call]

If status = QUALIFIED:
  → [🎉 Mark Converted]  [📄 Create Proposal]

If status = CONVERTED:
  → [📊 View Customer Details]

If status = LOST:
  → [🔄 Reopen]  [📝 Add Notes]
```

### 5. **Notifications** - Real-time Updates
```
🔴 New Lead: "John Doe from ABC Corp"
🔵 Lead Updated: "Sarah Smith → Contacted"
🟢 Lead Converted: "Mike Johnson completed purchase"
```

---

## 💡 Advanced Features

### 1. **Lead Scoring** (from metadata)
```
Display: ★★★★☆ (4/5 stars)

Based on:
- Response speed: Fast (+1)
- Budget match: High (+2)
- Engagement: High (+1)
- Missing info: -0 points
```

### 2. **Bulk Actions**
```
[Select All] [Deselect All]

☑ John Doe
☑ Sarah Smith
☑ Mike Johnson

Actions: [Export CSV] [Change Status →] [Assign to Sales Rep]
```

### 3. **Export Options**
```
Export as:
- 📄 CSV (Excel-compatible)
- 📊 PDF Report
- 📧 Email List (for marketing)
```

### 4. **Lead Assignment**
```
Assign to: [Sales Rep Dropdown ▼]
- John Sales (10 active leads)
- Sarah Manager (5 active leads)
- Auto-assign (Round-robin)
```

---

## 📱 Mobile-Responsive Considerations

### Mobile View (< 768px)
```
┌──────────────────────┐
│ 🔍 Search           │
│ [Filters ▼]         │
├──────────────────────┤
│ 🆕 All (28)         │
│ [NEW ▼] [Today ▼]  │
├──────────────────────┤
│ 👤 John Doe         │
│ 🏢 ABC Corp         │
│ 📧 john@example.com │
│ 🆕 NEW • 2h ago     │
│ [View →]            │
├──────────────────────┤
│ 👤 Sarah Smith      │
│ 🏢 TechCo           │
│ ...                 │
└──────────────────────┘
```

**Mobile Optimizations**:
- Stack cards vertically
- Collapsible details
- Swipe actions (swipe right = contact, swipe left = archive)
- Bottom navigation for quick filters

---

## 🚀 Implementation Priority

### Phase 1: MVP (Week 1)
- ✅ Lead list view with basic filters
- ✅ Lead detail view
- ✅ Status update functionality
- ✅ Basic search

### Phase 2: Enhanced (Week 2-3)
- ✅ Analytics dashboard
- ✅ Advanced filters
- ✅ Conversation history display
- ✅ Bulk actions

### Phase 3: Advanced (Week 4+)
- ✅ Lead scoring
- ✅ Assignment workflow
- ✅ Email integration
- ✅ Export functionality
- ✅ Real-time notifications

---

## 🎨 Example Component Structure (React)

```tsx
// Components
- LeadListPage
  ├── LeadFilters
  ├── LeadStatsBar
  └── LeadTable
      └── LeadRow
          ├── LeadAvatar
          ├── LeadContactInfo
          ├── StatusBadge
          └── ActionMenu

- LeadDetailPage
  ├── LeadHeader
  ├── ContactInfoCard
  ├── ConversationCard
  │   └── MessageList
  │       └── MessageBubble
  ├── MetadataCard
  └── ActionBar

- AnalyticsDashboard
  ├── StatsCards
  ├── LeadTrendChart
  ├── ConversionFunnel
  └── ResponseTimeMetrics
```

---

## 📊 Sample Data Display

### Lead Card Example
```json
{
  "display": {
    "title": "John Doe",
    "subtitle": "ABC Corporation",
    "avatar": "JD",
    "status": {
      "label": "NEW",
      "color": "blue",
      "icon": "🆕"
    },
    "contact": {
      "email": "john@abc.com",
      "phone": "+1-555-0123"
    },
    "preview": "I'm interested in 3BHK apartments in downtown...",
    "meta": {
      "created": "2 hours ago",
      "messages": 12,
      "engagement": "high"
    }
  }
}
```

---

## ✨ Summary

**What You Have Available**:
1. **Lead Details**: Name, email, phone, company, message
2. **Status Lifecycle**: NEW → CONTACTED → QUALIFIED → CONVERTED/LOST
3. **Conversation Context**: Full chat history, message count, timestamps
4. **Metadata**: Flexible custom fields (budget, preferences, etc.)
5. **Analytics**: Lead counts, conversion rates, trends

**What Frontend Should Display**:
- Dashboard with filterable lead list
- Individual lead pages with full conversation
- Analytics/metrics overview
- Status management workflow
- Search and export capabilities

This gives you a complete, production-ready lead management UI! 🎯
