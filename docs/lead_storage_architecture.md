# Lead Storage Architecture

## 1. Storage Location
Leads are stored in the **MongoDB** database, specifically in the `leads` collection.

- **Database Name**: `aob_bot_db` (Defined in `.env`)
- **Collection Name**: `leads`

## 2. Rationale
The user question "do we need another collection or DB?" touches on a key design choice: **Shared Database Multi-Tenancy**.

We store all leads in a single `leads` collection rather than separate databases because:
1.  **Simplicity**: Managing one database is operationally simpler than hundreds of tenant databases.
2.  **Aggregation**: It allows the Platform Owner (Super Admin) to easily run global analytics (e.g., "Total leads generated across all clients").
3.  **Performance**: MongoDB can efficiently handle millions of documents in one collection if properly indexed.

**Data Isolation** is enforced at the Application Level:
- Every lead document has a `tenant_id`.
- The API *always* filters queries by `tenant_id`.

## 3. Data Schema
Each document in the `leads` collection follows this structure:

```json
{
  "_id": "ObjectId(...)",
  "lead_id": "uuid-string",
  "tenant_id": "tenant-uuid",     // Critical for isolation
  "conversation_id": "conv-uuid", // Link to the chat session
  "name": "John Doe",
  "email": "john@example.com",
  "phone": "555-0199",
  "status": "new",                // new, contacted, qualified
  "created_at": "ISODate(...)",
  "metadata": {                   // Flexible extra fields
    "company": "Example Corp",
    "notes": "Extracted via AI"
  }
}
```

## 4. Admin Access
Admins do **not** need direct database access. They access data via the Admin API, which safely queries this collection.

### API Endpoint
`GET /api/leads`

**How it works:**
1.  Admin logs in -> receives JWT with `tenant_id`.
2.  Admin requests `/api/leads`.
3.  Backend executes: `db.leads.find({"tenant_id": admin_tenant_id})`.

This ensures that **Tenant A Admin** never sees **Tenant B's** leads, even though they live in the same MongoDB collection.
