# Manual Actions Required (AWS / Infra / Ops)

These items **cannot** be fixed in code alone — they need changes you make in the AWS console / your infrastructure / your secret store. The code has been updated to *support* these (e.g. it now reads `WHATSAPP_APP_SECRET`, requires a real prod table name, fails fast without `ENCRYPTION_KEY`), but **you must perform the actions below** for the app to run securely in production.

Ordered by priority.

---

## 🔴 1. Rotate ALL exposed secrets (do this first, today)

Every secret currently in `.env` was exposed during review and must be treated as compromised. Rotate each:

| Secret | Where to rotate | Notes |
|--------|-----------------|-------|
| `AWS_ACCESS_KEY` / `AWS_SECRET_KEY` (`AKIA5ABYUU23W25HPT3K`) | IAM console → deactivate & delete this key pair | Then switch to **IAM roles** (see #2). Do not create a replacement static key. |
| `JWT_SECRET_KEY` | Generate a new 32+ char random string | Rotating invalidates all existing sessions (expected). |
| `ENCRYPTION_KEY` (Fernet) | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` | ⚠️ Rotating breaks decryption of already-encrypted WhatsApp tokens — you must re-encrypt existing tenant credentials with the new key (decrypt-with-old, encrypt-with-new migration) or have tenants re-enter them. |
| `TELEGRAM_BOT_TOKEN` | Telegram @BotFather → `/revoke` → new token | |
| `QDRANT_API_KEY` (`9994590450`) | Qdrant admin → set a long random key (not a 10-digit number) | |
| `AWS_BEARER_TOKEN_BEDROCK` | Regenerate the Bedrock token | |
| `CLAUDE_API_KEY` (commented in `.env`) | Delete the line; if the key is real, revoke it in the Anthropic console | |

Then remove secrets from the flat `.env` on prod hosts and inject them from a secret store (see #3).

---

## 🔴 2. Switch AWS auth to IAM roles (remove static keys)

The code comment already says "Lambda uses IAM role" but static keys are set in `.env`.

- **Lambda:** attach an execution role with least-privilege policies (DynamoDB on your table, S3 on your bucket, SQS on your queues, Bedrock invoke). Remove `AWS_ACCESS_KEY`/`AWS_SECRET_KEY` from the environment — boto3 will use the role automatically.
- **ECS/Fargate:** use a Task Role (same idea).
- Grant `dynamodb:DescribeTable` on your table (the new `/readyz` health check uses `DescribeTable`, **not** the account-wide `ListTables` that was failing with AccessDenied).

---

## 🔴 3. Move secrets to AWS Secrets Manager / SSM Parameter Store

- Store every secret from #1 in Secrets Manager (or SSM SecureString).
- Inject at deploy time: Lambda env from Secrets Manager, or ECS `secrets:` block.
- Never ship `.env` to production. Use `.env.example` (added) as the template.

---

## 🔴 4. Provision required managed data stores (kill the single public-IP EC2 box)

Currently Qdrant **and** Redis share one public EC2 spot instance (`13.205.49.8`) over plaintext HTTP — a single point of failure with no HA/backups.

- **Redis → ElastiCache or MemoryDB (Multi-AZ).** Put it in a private subnet. Use `rediss://` (TLS) in `REDIS_URL`. Redis is now **required in production** (rate limiting, cache, distributed locks) — the app fails fast on boot if `REDIS_URL` is unset in prod.
- **Qdrant → Qdrant Cloud or a self-managed multi-node cluster** (replication factor ≥ 2), in a private subnet, TLS (`https://`), with **S3 snapshots** scheduled. A single node cannot hold a 100k-tenant corpus (~410 GB) in RAM — plan sharding before onboarding beyond low thousands of tenants.
- Neither store should have a public IP.

---

## 🔴 5. Set production environment variables (the app now enforces these)

Config now **fails fast on boot** in production if these are wrong (see `app/core/config.py::_validate_production_safety`). Set in the prod environment:

```
APP_ENV=production
DEBUG=false
DYNAMODB_TABLE_NAME=<your real prod table>     # REQUIRED — no longer silently falls back to test_leadengine
QDRANT_COLLECTION_NAME=<non-test name>          # must not start with "test_"
AWS_S3_BUCKET_NAME=<non-test bucket>            # must not start with "test-"
REDIS_URL=rediss://<elasticache-endpoint>:6379
ENCRYPTION_KEY=<the rotated Fernet key>
JWT_SECRET_KEY=<the rotated 32+ char key>
WHATSAPP_VERIFY_TOKEN=<meta verify token>
WHATSAPP_APP_SECRET=<meta app secret>          # REQUIRED — inbound webhook now rejects unsigned/forged payloads
```

**Create a separate prod DynamoDB table** (and Qdrant collection / S3 bucket) so prod never shares data with dev/test.

---

## 🟠 6. Create a GSI for tenant/user listing (removes full-table scans)

`list_tenant_summaries` / `list_users(tenant_id=None)` still do a **full-table `Scan`** (expensive, throttles at scale). The proper fix is a GSI, which is an infra change:

- Add a GSI keyed by entity type, e.g. `GSI3PK = "ENTITY#TENANT"` / `"ENTITY#USER"` (sparse attribute set only on tenant/user items).
- Then change those service methods to `Query` the GSI instead of `Scan`. (Code change is small once the index exists — I left the scans in place because querying a non-existent index would crash. Ping me to switch them once the GSI is live.)
- Enable **DynamoDB auto-scaling** (or on-demand) and **PITR** (point-in-time recovery) + on-demand backups.

---

## 🟠 7. Create SQS Dead-Letter Queues + redrive policy

Both the ingestion and outbound queues currently have no DLQ, so a poison message reprocesses forever (re-invoking the LLM and burning cost).

- Create a DLQ for each queue.
- Set `RedrivePolicy` with `maxReceiveCount` (e.g. 5) → DLQ.
- Add a CloudWatch alarm on DLQ `ApproximateNumberOfMessagesVisible > 0`.
- The message handlers are already idempotent (dedup by `message_id`), so redrive is safe.

---

## 🟠 8. Decide the deployment topology (API vs. worker) and stop duplicating workers

The app starts background SQS worker **threads inside the web process** (`main.py` lifespan). This is fine for a single dev process but breaks when you scale:
- Under **Lambda**, lifespan is off, so those threads never run — you must wire the SQS queues as a native **Lambda event source** pointing at a dedicated worker handler (`app/workers/worker.py` for ingestion; an outbound worker handler for replies). The current "worker Lambda" uses `Mangum`, which cannot parse SQS events — reconfigure it.
- Under **ECS/Fargate** (recommended), run the API as one service (multiple workers) and each SQS consumer as its **own service**. Before increasing API workers/replicas, move the workers out of the web process or they run N times and compete with request handling.

(The Dockerfile is hardened but still runs a single web process on purpose, with a comment pointing here.)

---

## 🟠 9. Put the API behind a gateway with WAF + TLS

- CloudFront/ALB or API Gateway in front of the API.
- **WAF** for edge rate limiting / bot control (defense in depth on top of the app-level Redis limiter now wired in).
- Terminate TLS at the edge; the app assumes it sits behind HTTPS (HSTS header is emitted in production).

---

## 🟡 10. Observability stack

Code emits structured JSON logs and has `/livez` + `/readyz`. Still need (infra/accounts):
- **Metrics + tracing:** OpenTelemetry (FastAPI + boto3 + httpx) → X-Ray / SigNoz / Tempo. Add per-dependency latency (Bedrock, Qdrant, DynamoDB).
- **Error tracking:** a Sentry project + DSN.
- **Alarms:** 5xx rate, p99 latency, SQS message age, DLQ depth, Bedrock spend.
- Wire your load balancer/orchestrator liveness→`/livez`, readiness→`/readyz`.

---

## 🟡 11. Delivery & DR

- Wire the new **CI** (`.github/workflows/ci.yml`) — add branch protection requiring it to pass before merge.
- Deploy via **canary** (Lambda alias + CodeDeploy `Canary10Percent5Minutes`, or ECS blue/green) with CloudWatch-alarm auto-rollback. The current in-place deploy is instant 100% with no rollback.
- Add **Infrastructure-as-Code** (Terraform/CDK) — there is none in the repo today.
- Document and test **RTO/RPO**: DynamoDB PITR, Qdrant snapshots, Redis durability.
- Run a **load test** at the 100k-user profile before launch.

---

## 🟡 12. Regenerate the dependency lockfile after `pyproject.toml` change

I added a `[project.optional-dependencies] dev` group (pytest etc.) and pytest config. Regenerate the lock so CI/containers are reproducible:

```bash
uv pip compile pyproject.toml -o requirements.txt      # if you use uv
# or: pip-compile pyproject.toml
```

Install dev tooling locally with: `pip install -e ".[dev]"` then run `pytest`.

---

### Quick reference — what the code now expects from you

| Code change | What you must provide |
|-------------|-----------------------|
| Prod config fail-fast | `APP_ENV=production` + real table/collection/bucket names + `REDIS_URL` + `ENCRYPTION_KEY` + 32-char `JWT_SECRET_KEY` |
| Webhook HMAC | `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN` |
| Redis-backed rate limiter | reachable `REDIS_URL` (ElastiCache) |
| `/readyz` health check | IAM `dynamodb:DescribeTable` on your table |
| Chat lockdown (`CHAT_REQUIRE_KEY=true`) | widget/frontend must send `X-API-Key` or `X-Widget-Key` (see FIXES_APPLIED.md → "Behavioral changes") |
