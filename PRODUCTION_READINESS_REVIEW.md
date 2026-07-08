# LeadEngine Backend — Production Readiness Review

**Target:** Inbound + outbound WhatsApp/Telegram lead-automation SaaS, multi-tenant, scaling to **100,000 users**
**Codebase:** `leadengine-backend` — FastAPI, ~26,700 LOC Python, DynamoDB + Qdrant + Redis (+ vestigial MongoDB)
**Review date:** 2026-07-08
**Reviewers:** 4 parallel specialist passes — Architecture/Scalability, Security, Code Quality/Performance, Infra/Observability/Ops
**Method:** Static read of source + git history + running the app locally. Every finding cites `file:line`.

---

## 1. Verdict

> **NOT production-ready. Do not put real customer traffic on this build.**

The application *functions* in a demo/dev setting, and several parts are genuinely well-built (structured JSON logging, Bedrock retry config, idempotent inbound dedup, argon2 password hashing, a clean single-`DynamoDBClient` singleton). But there are **hard blockers across every dimension** — a fully public chat endpoint that trusts client-supplied tenant IDs, a rate limiter that is never even wired up, live secrets on disk, a deployment topology that contradicts itself (Lambda vs. long-lived worker threads), production potentially writing to the *test* database, and full-table scans on hot paths that will make cost and latency explode long before 100k users.

The single most important finding: **three independent reviewers, with no shared context, each flagged the same core issues** (the `test_leadengine` config bug, blocking LLM calls on the async event loop, `.scan()` on the shared table, and the non-functional rate limiter). That convergence means these are not speculative — they are real and they are load-bearing.

**Realistic path to 100k:** ~4–8 weeks of focused hardening across security, deployment/topology, data-access, and observability, then a staged load test. This report gives a prioritized roadmap.

---

## 2. Severity Scoreboard

| # | Severity | Finding | Area | Evidence |
|---|----------|---------|------|----------|
| S-1 | 🔴 CRITICAL | `/api/chat` is public and trusts client-supplied `tenant_id` — cross-tenant data theft + cost-DoS | Security | `chat.py:37-58`, `conversation.py:64-71` |
| S-2 | 🔴 CRITICAL | Dev & prod share one DynamoDB table + Qdrant collection (`test_leadengine` hardcoded) | Arch / Config | `config.py:112-126` |
| S-3 | 🔴 CRITICAL | Live secrets in plaintext `.env` (AWS key, JWT secret, Telegram, encryption key) | Security | `.env:4-47` |
| S-4 | 🔴 CRITICAL | Deployment topology incoherent — worker threads never run under Lambda; worker Lambda can't parse SQS | Arch / Infra | `main.py:35-65`, `lambda_handler.py:18`, `deploy-backend-dev.sh:112` |
| S-5 | 🔴 CRITICAL | Blocking sync boto3/Bedrock/Claude/LangGraph calls on the async event loop | Perf / Arch | `chat.py:426,676,736,779,822`, `s3_max_state_machine.py:99`, `llm_service.py:192` |
| S-6 | 🔴 CRITICAL | Full-table `.scan()` on tenant/user list hot paths (single-table design) | Perf / Arch | `tenant_service_dynamodb.py:256,318`, `user_service_dynamodb.py:213` |
| S-7 | 🔴 CRITICAL | No signature verification on the inbound WhatsApp webhook — forged message injection | Security | `outbound_webhook.py:49-182` |
| S-8 | 🔴 CRITICAL | CORS reflects any Origin with credentials — whitelist fully bypassed | Security | `request_context.py:50-97`, `documents.py:36-45` |
| S-9 | 🔴 CRITICAL | Path traversal / arbitrary file write via unsanitized upload filename | Security | `documents.py:154-156` |
| S-10 | 🟠 HIGH | Rate limiting is dead code — never wired to the app; advertised as a feature | Security / Arch | `rate_limiter.py:4-8`, `main.py:128` |
| S-11 | 🟠 HIGH | Single-process API server, no worker concurrency; workers live inside the web process | Infra | `Dockerfile:18`, `main.py:37-65` |
| S-12 | 🟠 HIGH | Qdrant + Redis on one self-hosted public-IP EC2 spot box over plaintext HTTP — total SPOF | Infra | `.env:13,16`, `database.py:29` |
| S-13 | 🟠 HIGH | Distributed lock not fenced → double-processing when pipeline > 30s TTL | Arch | `outbound_db_service.py:80-89`, `conversation_orchestrator.py:82` |
| S-14 | 🟠 HIGH | No DLQ / poison-message handling; single-message single-threaded workers | Arch | `run_worker_service.py:43,62`, `outbound_worker.py:91,111` |
| S-15 | 🟠 HIGH | Privilege escalation — `PLATFORM_OPERATOR` can mint `PLATFORM_ADMIN` accounts | Security | `users.py:15-49`, `user_service_dynamodb.py:61-101` |
| S-16 | 🟠 HIGH | In-memory pagination + hard 1000 caps silently drop data & blow memory | Perf | `lead_service_dynamodb.py:146-161`, `conversation_service_dynamodb.py:228-242`, `chat.py:907` |
| S-17 | 🟠 HIGH | Refresh token in query string, no revocation/rotation; 12h default access token | Security | `auth.py:50-61`, `config.py:74` |
| S-18 | 🟠 HIGH | No CI/CD; deploy script broken (`leadengine-lambda.zip` vs `qbot-lambda.zip`) | Infra | absent `.github/`, `deploy-backend-dev.sh:92` vs `create_lambda_package.sh:78` |
| S-19 | 🟠 HIGH | No metrics, no distributed tracing, no error tracking (Sentry) | Observability | `pyproject.toml:5-35` |
| S-20 | 🟠 HIGH | `errors → print() → return falsy` swallows failures as success (44 sites) | Correctness | `dynamodb.py:75-129`, +40 more |
| S-21 | 🟠 HIGH | Single-node Qdrant cannot hold 100k-tenant corpus (~410 GB) — hard scaling wall | Capacity | `async_database.py:24`, `docs/QDRANT_EC2_SETUP.md` |
| S-22 | 🟠 HIGH | `/health` does a heavyweight `ListTables` (currently AccessDenied → always "degraded") | Infra | `main.py:147-154` |
| S-23 | 🟡 MEDIUM | Cross-tenant inbound routing by phone with `items[-1]` (no tenant scope) | Correctness | `outbound_webhook.py:117-130` |
| S-24 | 🟡 MEDIUM | Duplicate-email guard broken by `"EMAIL #"` typo → duplicate accounts possible | Correctness | `user_service_dynamodb.py:67-68` |
| S-25 | 🟡 MEDIUM | `SALES_TEAM` role has unrestricted cross-tenant **write** access | Security | `dependencies.py:98-100` |
| S-26 | 🟡 MEDIUM | `ENCRYPTION_KEY` fails open to a random ephemeral key → undecryptable tokens across instances | Security | `encryption.py:13-26` |
| S-27 | 🟡 MEDIUM | Redis is a hidden hard boot dependency; no pool/timeout/fallback; `keys("*")` invalidation | Perf / Reliability | `redis_client.py:8`, `tenant_service_dynamodb.py:103,211` |
| S-28 | 🟡 MEDIUM | Raw exception `str(e)` leaked to clients (26 routes) | Security | `chat.py:719`, +25 more |
| S-29 | 🟡 MEDIUM | `httpx.AsyncClient` + boto3 clients created per-request/per-send | Perf | `whatsapp_sender.py:111`, `telegram_sender.py:59`, `outbound.py:230,418,442` |
| S-30 | 🟡 MEDIUM | Double LLM call per question (translate + generate) — ~2× cost/latency | Cost | `chat.py:756,779` |
| S-31 | 🟡 MEDIUM | `DEBUG=True` default; no prod fail-fast validation | Config | `config.py:31` |
| S-32 | 🟡 MEDIUM | Unbounded in-memory tenant cache (class dict, never evicted) | Perf | `tenant_service_dynamodb.py:55,181-201` |
| S-33 | 🟡 MEDIUM | No backup/DR story (PITR, Qdrant snapshots, Redis persistence) | Reliability | no IaC; self-hosted stores |
| S-34 | 🟡 MEDIUM | ~4,700 lines of duplicated 1,700-line state machines (s2/s3/s3_international) | Maintainability | `s2_state_machine.py`, `s3_state_machine.py`, `s3_international_state_machine.py` |
| S-35 | 🟡 MEDIUM | Debug `print()` leaks conversation PII to stdout (`"hello world", name, phone…`) | Security / Logs | `s2_state_machine.py:887` |
| S-36 | 🟢 LOW | Startup crash on emoji `print()` under Windows cp1252 (already hit) | Portability | `services/__init__.py:20` |
| S-37 | 🟢 LOW | Dockerfile: root user, `build-essential` in final image, no HEALTHCHECK, no `.dockerignore` | Infra | `Dockerfile:6-18` |
| S-38 | 🟢 LOW | No blue/green/canary; in-place deploy = instant 100% rollout, no rollback | Infra | `deploy-backend-dev.sh:49` |
| S-39 | 🟢 LOW | `pytest` not a declared dependency; ~<20% real coverage; no coverage gate | Testing | `pyproject.toml`, `tests/` |
| S-40 | 🟢 LOW | Stray debug artifacts committed (`test_out.txt`, `verify_output.txt`, `git_log.txt`, DEBUG prints) | Hygiene | repo root, `lambda_handler.py:4` |

Legend: 🔴 blocks launch · 🟠 fix before scaling · 🟡 fix before/at scale · 🟢 hygiene

---

## 3. Cross-Cutting Themes

Five root causes explain most of the 40 findings. Fixing the *theme* is more valuable than patching each symptom.

### Theme A — "It runs on my laptop" is baked into the code, not just the config
`APP_ENV=development` and `DEBUG=True` are the **defaults** (`config.py:31,34`), and the "production" resource names are hardcoded to `test_leadengine` / `test_leadengine_documents` / `test-leadengine-document` (`config.py:114-135`). The `_env_suffix` helper that was supposed to separate environments exists but is **never used**. Net effect: forget one env var in prod and you silently read/write the test database. This is S-2 + S-31 + S-36 and it is the most dangerous class of bug because it fails *silently*.
**Fix once:** make `APP_ENV` a required field (like `JWT_SECRET_KEY` already is), default `DEBUG=False`, require explicit table/collection/bucket env vars, and add a Pydantic startup validator that **refuses to boot** if `APP_ENV=production` with dev defaults or missing secrets.

### Theme B — The async event loop is being used as if it were synchronous
The hot `/api/chat` path is `async def` but every heavy call inside it — Bedrock `converse`, Anthropic client, LangGraph `graph.invoke()`, DynamoDB, the Bitrix HTTP push — is **synchronous and blocking** (S-5). One 2–10s LLM call freezes *every other concurrent request* on that worker. The correct non-blocking wrapper (`async_llm_service.py`, `run_in_executor`) **already exists in the repo and is simply not used** by the hot path. Your own stress test already shows the signature: throughput plateaus at ~170 req/s regardless of worker count (`tests/honest_stress_test.py`).
**Fix once:** route all model/DB/third-party calls through `asyncio.to_thread`/`run_in_executor` (or native async SDKs: `AsyncAnthropic`, `aioboto3`, `graph.ainvoke()`), and move third-party pushes (Bitrix) off the request path onto the queue.

### Theme C — Multi-tenant isolation is opt-in, not enforced
`tenant_id` flows from the client body and is trusted unless an API key happens to be present (S-1). CORS reflects any origin (S-8). The webhook has no signature check (S-7). `SALES_TEAM` can write across tenants (S-25); an operator can escalate to admin (S-15); inbound routing picks an arbitrary tenant by phone (S-23). For a SaaS, tenant isolation is *the* product invariant — right now a motivated anonymous user can read a competitor's knowledge base, forge their customer conversations, and run up their AWS bill.
**Fix once:** every entry point (route, webhook, worker) must derive `tenant_id` from a **verified** credential (widget key + Origin, JWT, or HMAC-signed webhook), never from unauthenticated request data — and authorization must distinguish read vs. write.

### Theme D — The deployment story doesn't match the code
The code is written for a **long-lived container** (background SQS worker threads in the FastAPI lifespan, in-process caches). The packaging targets **Lambda** (`Mangum`, `lifespan="off"`), where those threads never start and can't run anyway; the "worker Lambda" is fed SQS events through Mangum, which only understands HTTP (S-4). Meanwhile the API is a single Uvicorn process (`fastapi run`, S-11), the deploy script is broken (S-18), and there's no CI. Nothing here is production-grade because it was never decided *what* production is.
**Fix once:** pick **ECS/Fargate** (recommended — matches the code: multi-worker API service + separate worker services) **or** commit fully to Lambda (delete the thread code, wire a real SQS-event-source worker Lambda). Then build the CI/CD around that one choice.

### Theme E — You are flying blind and unprotected
No metrics, no tracing, no Sentry (S-19); request IDs stop at the API log line and never reach downstream calls; errors are `print()`ed and swallowed as success (S-20); the rate limiter is never attached (S-10); there are no DLQs (S-14); the health check is broken (S-22). At 100k users a p99 spike or a cost runaway would be undebuggable and unbounded.
**Fix once:** OpenTelemetry (FastAPI + boto3 + httpx) → X-Ray/SigNoz, RED metrics + CloudWatch alarms, Sentry, a Redis-backed rate limiter actually wired to the app, and DLQs with depth alarms.

---

## 4. Capacity & Cost Model at 100k Users

Assumptions: 100k users, ~20% daily-active (20k), ~15 inbound messages/active conversation/day ≈ **300k inbound messages/day** (~3.5 msg/s avg, ~10–20 msg/s peak). Each message ≈ 1–3 LLM calls + several DynamoDB ops + 1 embedding.

| Resource | Happy-path estimate | The real risk |
|----------|--------------------|----------------|
| **DynamoDB** | ~3.5M reads + 1.5M writes/day on-demand ≈ **~$4/day** if queries are bounded | **The `.scan()` paths (S-6) blow this up.** A ~50M-item single table scanned even a few hundred times/day on cache-miss = tens of billions of RCU = **thousands of $/day + guaranteed throttling.** This, not baseline traffic, is the dominant cost/scaling risk. |
| **LLM (Bedrock)** | ~5k tokens/message blended → **~1.5B tokens/day** ≈ **$300–900/day (~$9k–27k/mo)** — largest line item | The translate-then-generate double call (S-30) roughly **doubles** this unnecessarily. Verbose prompts + 20-msg history (`chat.py:168`) inflate input tokens. Cost scales linearly with volume — trim prompts, collapse double calls, cache. |
| **Qdrant** | 100k tenants × ~1k chunks × 1024-dim × 4B ≈ **~410 GB** raw + index | A single EC2 node (S-21) **cannot hold this in RAM** (~0.5–1 TB needed). Hard wall well before 100k tenants — must shard/cluster or move to managed Qdrant Cloud. |
| **Redis** | Small (rate-limit + tenant cache) | Currently a raw EC2 box (S-12) — SPOF, loses all state on reboot. Move to ElastiCache/MemoryDB Multi-AZ. |

**Bottom line:** at 100k users your bill is dominated by LLM tokens (controllable) and *catastrophically* by DynamoDB scans (must be eliminated). Fixing S-6 and S-30 alone changes the cost curve from "unsustainable" to "reasonable."

---

## 5. Recommended Target Architecture

```
                       ┌─────────────┐
   Internet ──▶ CloudFront + WAF ──▶ ALB ──▶  API tier (ECS Fargate)
                       │ (rate limit,        gunicorn -k uvicorn.workers.UvicornWorker
                       │  bot control)       -w (2–4 × vCPU), min 2 tasks / 2 AZs
                       │                     autoscale on CPU + RPS
                       │
   WhatsApp/Telegram ──┴──▶ /webhook (HMAC-verified) ──▶ SQS ──▶ Worker tier (separate ECS service
                                                                  OR SQS-event-source Lambda)
                                                                  batch=10, concurrent, DLQ+redrive,
                                                                  idempotent, SIGTERM drain

   Data:  DynamoDB (on-demand, PITR on, GSIs for list paths, Streams→counter aggregation)
          Qdrant Cloud / 3-node cluster (RF≥2, private subnet, TLS, S3 snapshots)
          ElastiCache/MemoryDB Redis (Multi-AZ) — rate limit + cache + distributed locks
          S3 (documents, versioning + lifecycle)

   Secrets:  AWS Secrets Manager / SSM — injected at deploy; NO .env in prod; IAM roles (no static keys)
   Observability:  OpenTelemetry → X-Ray/SigNoz · CloudWatch EMF RED metrics + alarms · Sentry
   Delivery:  GitHub Actions → lint → test+coverage gate → build → dev → smoke → approval →
              prod canary (CodeDeploy 10%/5min) with alarm auto-rollback · IaC in Terraform/CDK
```

Why Fargate over Lambda for the API: the code makes blocking SDK calls and Bedrock latency is high, so per-request Lambda billing + cold starts are unattractive at sustained 100k load. Fargate matches the code as written (multi-worker, long-lived). Keep Lambda in your pocket for spiky/async pieces if you prefer.

---

## 6. Remediation Roadmap (phased)

### Phase 0 — Security & data-safety blockers *(before ANY real traffic — days, not weeks)*
- [ ] **S-3** Rotate every secret in `.env` **now** (deactivate the `AKIA…` IAM key first), move to Secrets Manager/SSM, switch AWS to IAM roles. *Treat all current secrets as compromised — they were exposed to tooling during this review.*
- [ ] **S-1** Lock down `/api/chat`: require a verified widget key + Origin (reuse the logic already in `/widget/validate`) or an authenticated session; never trust body `tenant_id`.
- [ ] **S-7** HMAC-verify the WhatsApp webhook (`X-Hub-Signature-256`); remove the hardcoded verify-token default.
- [ ] **S-8** Delete all manual CORS header logic; rely solely on the whitelisted `CORSMiddleware`.
- [ ] **S-9** Sanitize upload filenames (`os.path.basename` + UUID + temp dir); enforce extension/MIME allowlist + max size.
- [ ] **S-2 / S-31** Fix the `test_leadengine` config bug; make `APP_ENV`/table/collection required; `DEBUG=False`; add a prod fail-fast startup validator.
- [ ] **S-15 / S-25** Restrict role assignment (no operator→admin) and split read vs. write tenant access.
- [ ] **S-10** Actually wire slowapi (`app.state.limiter` + `SlowAPIMiddleware`) with Redis storage + per-route limits on `/login`, `/chat`, `/documents/ingest`, `/refresh`.

### Phase 1 — Topology & deployment *(1–2 weeks)*
- [ ] **S-4 / S-11** Choose ECS/Fargate; run the API multi-worker; move the two SQS workers to their own service(s). Delete the in-lifespan thread code (and `lambda_handler.py`/Mangum if going Fargate).
- [ ] **S-12 / S-21** Move Redis → ElastiCache/MemoryDB (Multi-AZ); Qdrant → managed/HA cluster; enforce TLS; kill the public-IP box.
- [ ] **S-18** Stand up GitHub Actions CI/CD with a test gate; fix the deploy-script zip-name bug.
- [ ] **S-22 / S-37** Split `/livez` (unconditional 200) vs `/readyz` (cheap `DescribeTable`); harden the Dockerfile (multi-stage, non-root, HEALTHCHECK, `.dockerignore`).

### Phase 2 — Scale & correctness *(2–3 weeks)*
- [ ] **S-5** Offload all LLM/DB/third-party calls off the event loop (reuse `async_llm_service`; `graph.ainvoke()`; enqueue Bitrix).
- [ ] **S-6 / S-16** Replace `.scan()` with GSI-backed `Query`; native cursor pagination; bounded page sizes (≤100); maintained counters instead of COUNT scans.
- [ ] **S-13** Fence the distributed lock (owner UUID + conditional delete + longer/renewed TTL).
- [ ] **S-14** DLQ + `maxReceiveCount` on both queues; batch + concurrent workers; alarms on DLQ depth.
- [ ] **S-19** OpenTelemetry + RED metrics + Sentry; propagate request/trace IDs into downstream + SQS attributes.
- [ ] **S-20 / S-28 / S-35** Replace all `print()` with structured `logger`; stop returning `str(e)` to clients; remove PII debug prints; audit `put_item` callers that ignore the boolean.

### Phase 3 — Hardening & efficiency *(1–2 weeks)*
- [ ] **S-30** Collapse translate+generate into one prompt; trim prompt/history sizes.
- [ ] **S-27 / S-29 / S-32** Lazy pooled Redis with fallback + `SCAN`; shared pooled `httpx`/S3/SQS clients; bounded tenant cache.
- [ ] **S-33 / S-38** DynamoDB PITR, Qdrant snapshots→S3, Redis durability; documented+tested RTO/RPO; canary deploys with auto-rollback.
- [ ] **S-24 / S-23 / S-26** Fix the `EMAIL #` typo; tenant-scope phone routing; `ENCRYPTION_KEY` fail-fast.
- [ ] **S-34 / S-39** Deduplicate state machines into a shared base (unblocks testing); add `pytest`/`pytest-cov` + CI coverage gate; write tests for the S3 state machines.

---

## 7. Pre-Launch Checklist (condensed)

**Must be TRUE before real traffic:**
- [ ] No secret lives in a file on any prod host; all in Secrets Manager; static AWS keys deleted.
- [ ] Every `tenant_id` is derived from a verified credential; webhook is HMAC-verified; CORS is whitelist-only.
- [ ] Prod uses a distinct DynamoDB table + Qdrant collection; app refuses to boot with dev defaults.
- [ ] Rate limiting is wired and Redis-backed; login/chat/ingest are limited.
- [ ] One coherent deploy target; API is multi-worker; workers run as their own service with DLQs.
- [ ] Redis & Qdrant are managed/HA with TLS; no public-IP datastore.
- [ ] CI runs tests + a coverage gate; deploys are canary with alarm-based rollback.
- [ ] Metrics + tracing + Sentry live; alarms on 5xx rate, p99 latency, SQS age, DLQ depth, LLM spend.
- [ ] A load test at the 100k-user profile passes without event-loop stalls or DynamoDB throttling.

---

## 8. What's Already Good (keep it)

Credit where due — these are done right and should be preserved through the refactors:
- **Structured JSON logging** with request-ID context vars (`logging_config.py`) — solid foundation; just route everything through it.
- **Bedrock retry/timeout config** — adaptive mode, `max_attempts=10` (`llm_service.py:52-59`).
- **Inbound idempotency** — atomic conditional-put dedup with 24h TTL (`outbound_db_service.py:101-115`).
- **Outbound sender resilience** — exponential backoff on 5xx/timeout (`whatsapp_sender.py`, `telegram_sender.py`).
- **Password hashing** — argon2/bcrypt, not homegrown.
- **The async wrapper already exists** (`async_llm_service.py`) — the fix for S-5 is adoption, not invention.
- **`.env` is correctly gitignored and never committed** (verified against git history) — the secret exposure is on-disk hygiene, not a repo leak.
- **Single `DynamoDBClient` singleton** (`dynamodb.py`) — the right pattern; extend it to S3/SQS.

---

*Appendix — this report consolidates four independent specialist reviews. Where multiple reviewers flagged the same issue (S-1/S-2/S-5/S-6/S-10), confidence is high. Line numbers reference the repo state at review time; re-verify after edits.*
