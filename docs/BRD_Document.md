# N3X – QLead AI Lead Qualification Bot Framework

## Introduction

The **AI Lead Qualification Bot Framework** is designed to strengthen **QLead’s sales and marketing operations** by automating early customer interactions and lead evaluation.

It intelligently engages prospects, asks relevant questions, and qualifies leads in real time—ensuring that sales teams focus on genuine, high-value opportunities.

Developed by **N3X Global Solutions**, the framework integrates **conversational AI, business logic, and CRM connectivity** into a unified system deployable across **QLead.ai** and partner platforms.

**Key Benefits**:

* Faster response times
* Consistent engagement quality
* Scalable foundation for AI-driven sales initiatives

---

## End-to-End Workflow

```
Inbound Channel → Qualification → Scoring → Action → CRM Sync
```

### 1. Entry Points (Inbound)

**Channels**:

* WhatsApp (verified number or Click-to-WhatsApp ads)
* Website chat widget

**Bot Actions**:

* Webhook receives message
* Resolves tenant / brand
* Determines bot tier (S1 / S2 / S3)
* Applies language and compliance rules

---

### 2. Greeting & Consent

* Sends branded greeting
* Explains purpose and data usage
* Requests opt-in if required ("Yes / Agree")

**Outcome**:

* Session initialized with unique session ID
* Contact soft-created

---

### 3. Lead Qualification Dialog (Tiered)

The bot asks structured questions and normalizes natural-language answers (e.g., `"~50L"`, `"under 70"`).

#### S1 – Simple Qualification (≈ 5 Questions)

* Intent / Interest
* Budget range
* Location / Region
* Timeline
* Contact details

**Flow**: Linear, quick qualify or disqualify

---

#### S2 – Medium Qualification (10–15 Questions)

* Use-case / Persona
* Capacity / Size
* Constraints

**Features**:

* Branching logic
* More CRM fields captured

---

#### S3 – Pro Qualification (15–30 Questions)

* All S2 questions
* Comprehensive discovery
* High-detail profiling

---

**Bot Behavior**:

* Confirms captured values ("Budget recorded as ₹60–70L")
* Allows back / skip
* Maintains partial progress

---

### 4. Sales Q&A (Grounded Answers)

At any point, prospects can ask questions such as:

* Pricing
* Features
* Amenities

**Mechanism**:

* Retrieval-Augmented Generation (RAG)
* Uses only tenant-approved documents (PDFs, FAQs, brochures)
* No hallucination or speculation

---

### 5. Lead Scoring & Decisioning

After qualification:

**Computed Outputs**:

* Lead Score: **A / B / C** (tier-specific weighted rules)
* Disposition:

  * Qualified
  * Nurture
  * Disqualified

**Outcome**:

* Automatic next-action selection

---

### 6. Conversion Actions

Based on score and configuration:

* 📞 Book call / site visit
* 🔁 Route to human agent (WhatsApp / Email)
* 🌱 Nurture flow (brochure + follow-up scheduling)

All actions are logged to the session timeline.

---

### 7. CRM Synchronization

* Create / Update Lead, Contact, Deal
* Attach chat transcript

**Reliability**:

* Retry with exponential backoff
* Operator alerts on repeated failures

---

### 8. Governance, Privacy & Safety

* Tenant-level data isolation
* PII minimization
* Consent & audit logging
* Safe fallback to human if knowledge unavailable

---

## Story Descriptions

### Story 1 — S1 Simple Bot: Quick Qualification

**Client**: Sky Habitat Real Estate
**User**: Priya (working professional)

**Flow**:

1. Priya clicks an Instagram ad
2. Bot greets and requests consent
3. Asks 5 qualification questions
4. Understands natural language answers
5. Answers budget questions via internal FAQs
6. Offers call scheduling

**Outcome**:

* Lead auto-created in CRM
* Sales team receives fully qualified lead
* Faster conversion, less manual screening

---

## Admin Console – UX Flow

### Step 1: Create Client

**Fields**:

* Client name
* Brand logo
* Locale(s)
* Business vertical

**Output**: Tenant with isolated storage & index

---

### Step 2: Choose Bot Tier

* S1 (5 questions)
* S2 (10–15 questions)
* S3 (15–40 questions)

**Options**:

* Industry presets
* Custom configuration

---

### Step 3: Define Qualification Questions

* Drag & reorder
* Required / optional flags
* Regex validation (email / phone)
* Range validation (budget)

**Scoring**:

* Per-question weights
* Disqualifiers
* A/B/C thresholds

---

### Step 4: Upload Knowledge (RAG)

**Accepted Formats**:

* PDF
* DOCX
* TXT
* CSV (FAQs)
* URLs (optional)

**Pipeline**:

* Chunk → Embed → Index (tenant-scoped)

---

### Step 5: Integrations

**WhatsApp BSP**:

* Sender number
* Template IDs

**CRM (e.g., Bitrix24)**:

* OAuth credentials
* Pipeline & stage mapping

---

### Step 6: Status & Logs

* Index health
* Webhook health
* CRM latency

**Exports**:

* Leads (CSV / XLSX)
* Transcripts (ZIP)
* Config backup (JSON)

---

## Technology Stack

* **API / ETL**: FastAPI, Pydantic
* **Async Jobs**: Celery / RQ
* **RAG**: Sentence Transformers, pgvector / Weaviate
* **Database**: PostgreSQL
* **Frontend**: React.js
* **Infra**: Docker Compose, S3-compatible storage
* **Security**: Per-tenant isolation

---

## Integration Costs

### WhatsApp (Meta India Rates)

| Scenario              | Initiator | Category  | Cost    |
| --------------------- | --------- | --------- | ------- |
| User starts chat      | User      | Service   | ₹0      |
| Business cold message | Business  | Marketing | ₹0.7846 |
| Follow-up after 24h   | Business  | Marketing | ₹0.7846 |
| Transactional message | Business  | Utility   | ~₹0.39  |

### Facebook Messenger

* Free

### Custom Chatbot

* Hosted on N3X servers
* Fully customizable
* No platform cost

### LLM APIs

* Locally deployed open-source models

---

## Commercials

### Setup Cost

* ₹70,000 + GST

### Timeline

* **Week 1**: MVP (S1 flow)
* **Week 2**: S2 / S3 + RAG + scoring
* **Week 3**: Admin Console + tenant management + deployment
* **Week 4 (Optional)**: Testing & optimization

---

## Optional Deployment Phase

* 1-week client-specific bot rollout
* CRM transcript sync
* Gradual enablement of advanced features

