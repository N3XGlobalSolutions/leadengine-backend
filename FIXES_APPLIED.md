# Fixes Applied — Before / After

This documents every code change made against the findings in `PRODUCTION_READINESS_REVIEW.md`.
Infra/AWS items that **you** must do (secret rotation, ElastiCache, GSIs, DLQs, etc.) are in `MANUAL_ACTIONS_REQUIRED.md`.

**Scope:** All CRITICAL and HIGH code-level findings fixed, plus most MEDIUM/LOW. Items requiring AWS provisioning or risky large refactors are documented instead of forced (see "Deferred" at the bottom).

---

## 🔴 CRITICAL

### S-1 — `/api/chat` trusted client-supplied `tenant_id` (cross-tenant theft + cost-DoS)
**File:** `app/routes/v1/chat.py`
- **Before:** `tenant_id` came from the request body and was only overridden *if* an API key was sent. No key → the raw body `tenant_id` was trusted. Anonymous users could read any tenant's RAG data and burn their LLM budget.
- **After:** The effective tenant is derived from a **verified credential** — `X-API-Key` (resolved via `validate_api_key`) or `X-Widget-Key` (must match the claimed tenant's `config.api_key` **and** a whitelisted `Origin`). With `CHAT_REQUIRE_KEY=true` (default), a request with no valid key is rejected `401` instead of trusting the body.
- ⚠️ **Behavioral change:** the widget/frontend must now send `X-API-Key` or `X-Widget-Key`. See "Behavioral changes" below.

### S-2 / S-31 — Prod could silently use the *test* DB; unsafe defaults
**File:** `app/core/config.py`
- **Before:** `DYNAMODB_TABLE_NAME` returned the literal `"test_leadengine"` unless BOTH `APP_ENV=production` AND an explicit env var were set; `EFFECTIVE_QDRANT_COLLECTION` was hardcoded to `test_leadengine_documents` always; `DEBUG` defaulted to `True`.
- **After:** In production, table/collection/bucket names are **required** and must not be test values (raises on boot otherwise). `DEBUG` defaults to `False`. Added a `@model_validator` (`_validate_production_safety`) that refuses to boot in production with `DEBUG=True`, missing `REDIS_URL`/`ENCRYPTION_KEY`, or a short `JWT_SECRET_KEY`. Access-token default lowered `720`→`30` min.

### S-3 — Live secrets in `.env`
- Code cannot rotate secrets. Added `.env.example` (no secrets), `.dockerignore` (keeps `.env` out of images), and the config now *requires* prod secrets to be injected. **You must rotate + move to Secrets Manager** — see `MANUAL_ACTIONS_REQUIRED.md` §1–3.

### S-5 — Blocking LLM/DB calls froze the async event loop
**File:** `app/routes/v1/chat.py`
- **Before:** `async def chat(...)` performed entirely synchronous, blocking work (Bedrock/Claude/DynamoDB) with **zero `await`s** — one slow LLM call stalled every concurrent request.
- **After:** Converted to a **sync `def chat(...)`**. Starlette now runs it in a worker threadpool, so blocking calls no longer block the loop and requests run concurrently. (Verified: the handler body contained no `await`, so this is behavior-preserving.)

### S-7 — WhatsApp webhook had no signature verification (message forgery)
**File:** `app/routes/v1/outbound_webhook.py`
- **Before:** `POST /webhook` trusted any JSON body. The GET handshake used a hardcoded `"leadengine_verify_token"` default.
- **After:** `_verify_meta_signature()` verifies the `X-Hub-Signature-256` HMAC-SHA256 over the **raw body** using `WHATSAPP_APP_SECRET` (fails closed if unset → `401`). The GET handshake now uses `WHATSAPP_VERIFY_TOKEN` with a constant-time compare and no default.

### S-8 — CORS reflected any Origin with credentials
**Files:** `app/middleware/request_context.py`, `app/middleware/exception_handlers.py`, `app/routes/v1/documents.py`, `main.py`
- **Before:** Three places manually set `Access-Control-Allow-Origin: <reflected origin>` + `Allow-Credentials: true`, bypassing the whitelist.
- **After:** All manual CORS logic removed. A single `CORSMiddleware` (outermost, strict `allow_origins` whitelist) is the only source of CORS headers. Added `SecurityHeadersMiddleware` (nosniff, frame-deny, referrer/permissions policy, HSTS in prod).

### S-9 — Upload path traversal / arbitrary file write
**File:** `app/routes/v1/documents.py`
- **Before:** `file_location = f"temp_{file.filename}"` used the attacker-controlled filename directly; any extension accepted; no size limit (full read into memory).
- **After:** `_validate_and_resolve_upload()` strips path components (`os.path.basename`, blocks `..`) and enforces an extension allowlist (`ALLOWED_UPLOAD_EXTENSIONS`). `_read_upload_with_limit()` enforces `MAX_UPLOAD_SIZE_MB` (413 on exceed). Sync path writes to a server-controlled `tempfile.mkdtemp()` + UUID name, cleaned up in `finally`.

---

## 🟠 HIGH

### S-10 — Rate limiting was dead code (never wired)
**Files:** `app/middleware/rate_limiter.py`, `main.py`, `app/routes/v1/auth.py`, `app/routes/v1/documents.py`
- **Before:** `Limiter(storage_uri="memory://")` was instantiated but never attached; no route used it; `memory://` doesn't work multi-instance.
- **After:** Redis-backed storage (`settings.REDIS_URL`, falls back to `memory://` only in dev), keyed by API/widget key or tenant then IP. Wired via `app.state.limiter` + `SlowAPIMiddleware`. Explicit tighter limits on `/api/auth/login`, `/api/auth/refresh` (`RATE_LIMIT_AUTH=5/min`) and `/api/documents/ingest` (`RATE_LIMIT_INGEST=20/min`); global default (`100/min`) covers `/api/chat`.

### S-13 — Distributed lock not fenced (double-processing)
**Files:** `app/services/outbound/outbound_db_service.py`, `app/services/ai/outbound/conversation_orchestrator.py`
- **Before:** `release_lock` did an unconditional `delete_item`; if the 30s TTL expired mid-pipeline and another worker took the lock, the original worker deleted the new owner's lock.
- **After:** `acquire_lock` writes a unique `owner` token and returns it; `release_lock(owner=...)` deletes only on `ConditionExpression="owner = :owner"`. TTL raised `30`→`120s`. Orchestrator threads the token through.

### S-15 — Privilege escalation (operator → admin)
**File:** `app/routes/v1/users.py`
- **Before:** `POST /api/users` let a `PLATFORM_OPERATOR` create a user with `role=platform_admin`.
- **After:** `_assert_can_assign_role()` — only a `PLATFORM_ADMIN` may create/assign platform-level roles. Applied to both create and update (role change) paths.

### S-22 — Broken heavyweight health check
**File:** `main.py`
- **Before:** `/health` called `ListTables` (needs an IAM perm that was failing → always "degraded"), coupling liveness to a DB round-trip.
- **After:** `/livez` (unconditional 200, never touches deps) + `/readyz` (cheap `DescribeTable` on the app's own table, offloaded via `asyncio.to_thread`, returns 503 when not ready). `/health` kept as a `/readyz` alias. Dockerfile `HEALTHCHECK` hits `/livez`.

### S-25 — SALES_TEAM had cross-tenant *write* access
**Files:** `app/utils/dependencies.py`, `app/routes/v1/leads.py`, `app/routes/v1/documents.py`
- **Before:** `validate_tenant_access` returned `True` for `SALES_TEAM` on read *and* write endpoints.
- **After:** Split into `validate_tenant_access` (read; sales team allowed cross-tenant) and `validate_tenant_write_access` (write; sales team confined to own tenant). Applied the write check to lead status updates and document deletes.

### S-17 — Refresh token in query string; long-lived tokens
**Files:** `app/routes/v1/auth.py`, `app/core/config.py`
- **Before:** `refresh_token: str` param → FastAPI treated it as a **query parameter** (logged in URLs). Access token default 12h.
- **After:** Refresh token accepted via a JSON body model (`RefreshRequest`). Access-token default lowered to 30 min. (Server-side revocation/rotation noted as a follow-up.)

### S-20 / S-28 — Errors `print()`ed and swallowed; `str(e)` leaked to clients
**Files:** routes (`auth.py`, `users.py`, `documents.py`, `leads.py`, `chat.py`, `tenants.py`, `outbound.py`) + service/worker layer (via sweep)
- **Before:** ~44 `print()` error sites (invisible in prod JSON logs, some swallowed write failures as success); ~26 routes returned `detail=str(e)` leaking internal errors.
- **After:** `print()` → `logger.error(..., exc_info=True)` / `logger.warning(...)`; client 500s now return generic messages while the real error is logged. Write helpers in `app/core/dynamodb.py` now log failures at error level. (Service/worker sweep across `dynamodb.py`, `*_service_dynamodb.py`, `auth_service.py`, `bitrix_service.py`, `lqc_evaluator.py`, `campaign_executor_service.py`, workers.)
- **Follow-up (post-audit):** an adversarial re-audit found the sweep had missed the broad-`except`→`str(e)` internal leaks in `tenants.py` (500) and `outbound.py` (ImportError→500) and stray `print()`s in `tenants.py`. Those are now fixed. **Intentionally retained:** `except ValueError`/`AuthenticationError` handlers that return `str(e)` — those carry *controlled, user-facing* validation messages (e.g. "Invalid email or password", "User already exists"), not internal details.

---

## 🟡 MEDIUM

| ID | Fix | File(s) |
|----|-----|---------|
| S-23 | Phone→tenant routing: detect multi-tenant ambiguity, prefer recipient `phone_number_id`, pick deterministically (most-recent) instead of arbitrary `items[-1]`; log ambiguity | `outbound_webhook.py` |
| S-24 | Fixed `"EMAIL #"` typo → `"EMAIL#"` so duplicate-email guard actually fires | `user_service_dynamodb.py` |
| S-26 | `ENCRYPTION_KEY` now **fails fast** outside development instead of generating an ephemeral per-process key | `app/utils/encryption.py` |
| S-27 | Redis client made lazy + pooled (timeouts, retry, `None` when unset — no import crash); cache access wrapped in try/except with DB fallback; `keys("*")` → `scan_iter` | `redis_client.py`, `tenant_service_dynamodb.py` |
| S-29 | Shared pooled `httpx.AsyncClient` for WhatsApp/Telegram senders (was one client per send); closed on shutdown | `whatsapp_sender.py`, `telegram_sender.py`, `main.py` |
| S-32 | Bounded the in-memory tenant cache (max 500 entries, evicts expired/oldest) | `tenant_service_dynamodb.py` |
| S-35 | Removed debug `print("hello world", ...)` that dumped conversation PII to stdout | `s2_state_machine.py` |
| S-16 | Hard-capped previously-unbounded list limits — both `leads.py` (`get_tenant_leads`) and `chat.py` (`get_tenant_conversations`) now use `Query(500, ge=1, le=1000)` (was `10000`). Full `ExclusiveStartKey` cursor pagination is still the deeper follow-up. | `leads.py`, `chat.py` |

---

## 🟢 LOW / Ops hygiene

| ID | Fix |
|----|-----|
| S-36 | Removed emoji from `print`/log strings (crashed on Windows cp1252); Dockerfile sets `PYTHONUTF8=1` |
| S-37 | Multi-stage, **non-root** Dockerfile; `build-essential` kept out of the runtime image; `HEALTHCHECK` on `/livez`; added `.dockerignore` (excludes `.env`, `.venv`, tests, artifacts) |
| S-18 | Fixed the deploy script's zip-name mismatch (`leadengine-lambda.zip` → `qbot-lambda.zip`) that made it always abort |
| S-39 | Added `pytest`/`pytest-asyncio`/`pytest-cov` dev deps + `[tool.pytest.ini_options]` to `pyproject.toml`; added `.github/workflows/ci.yml` (compile + tests + coverage) |
| S-40 | Deleted committed debug artifacts (`test_out.txt`, `verify_output.txt`, `git_log*.txt`, etc.); cleaned debug prints from `lambda_handler.py` |
| — | Added `.env.example` documenting every config knob (incl. new security settings) |

---

## ⚠️ Behavioral changes (verify against your frontend before deploying)

1. **`/api/chat` now requires a key** (`CHAT_REQUIRE_KEY=true`). The widget must send `X-API-Key` **or** `X-Widget-Key` (+ a whitelisted `Origin` for widget keys). If your widget currently posts only a body `tenant_id`, it will get `401`. To stage the rollout you can temporarily set `CHAT_REQUIRE_KEY=false` (dev only) while you update the frontend — but do **not** launch with it false.
2. **`/api/auth/refresh`** now expects the token in the JSON body: `{"refresh_token": "..."}` instead of `?refresh_token=...`.
3. **CORS** is now strictly the whitelist in `CORS_ORIGINS`. Any origin not listed will be blocked (previously all were reflected). Add every real frontend origin to `CORS_ORIGINS`.
4. **Rate limits** now enforced — login/refresh 5/min, ingest 20/min, everything else 100/min per key/tenant/IP. Tune in config if needed.
5. **Production boot is now strict** — the app refuses to start if prod config is unsafe (see S-2/S-31). Set all required env vars (`MANUAL_ACTIONS_REQUIRED.md` §5).
6. **Uploads** limited to `pdf,docx,txt` and `MAX_UPLOAD_SIZE_MB` (25 default).

---

## Deferred (documented, not forced — need infra or risky refactor)

- **S-6** (full-table scans → GSI query): needs a new DynamoDB GSI first (querying a non-existent index would crash). Scans left in place; see `MANUAL_ACTIONS_REQUIRED.md` §6.
- **S-14** (SQS DLQs): queue/redrive config is an AWS action; handlers are already idempotent. See §7.
- **S-4 / S-11** (Lambda vs container topology, multi-worker, externalized workers): a deployment decision. Dockerfile hardened but kept single-process with a pointer to §8.
- **S-12 / S-21** (managed HA Redis/Qdrant, TLS): infra. See §4.
- **S-19** (metrics/tracing/Sentry): needs accounts/collectors. See §10.
- **S-30** (collapse translate+generate double LLM call): an optimization that changes prompt behavior; skipped to avoid response-quality regression without an eval harness.
- **S-34** (deduplicate the three ~1,700-line state machines): large refactor; risky without the missing test coverage. Recommend doing it *after* adding tests.
- **S-16 (cursor pagination only)** — the unbounded-limit bug is *fixed* (hard `le=1000` caps); native `ExclusiveStartKey` cursor pagination remains a deeper follow-up.

---

## Verification (evidence-backed)

All 40 findings were re-audited against the actual code by 6 independent verifiers (not trusting this report):
**26 FIXED · 12 DEFERRED (infra/refactor, documented) · 0 falsely claimed.** The 2 items previously PARTIAL (S-16 unbounded chat limit, S-28 leftover `str(e)` leaks in `tenants.py`/`outbound.py`) were closed in the post-audit pass above. Live server verified: `/livez` 200, `/readyz` 200, `/api/chat` without a key → 401, login rate-limit → 429 after 5 attempts.
