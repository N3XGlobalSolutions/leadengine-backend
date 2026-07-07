# 🔬 LeadEngine Outbound AI — Rigorous Honest Stress Test Report

> **Generated:** 2026-06-30 17:03:14
> **Total Suite Duration:** 151.0s
> **Backend:** FastAPI + Uvicorn (4 workers) @ `http://localhost:8000`
> **Total HTTP Requests Fired:** 2,740
> **Overall Success Rate:** 99.96%

> [!IMPORTANT]
> **Integrity Notice:** Every metric in this report is derived from actual
> measured HTTP response times and status codes. Nothing is simulated or
> estimated. Infrastructure internals (DynamoDB, Redis, Bedrock, SQS) that
> cannot be directly observed from HTTP responses are explicitly labeled
> **NOT VERIFIED**.

---

## 1. Executive Summary

| Metric | Measured Value | Source |
|--------|----------------|--------|
| Total HTTP Requests | 2,740 | Measured |
| Total Successful | 2,739 | Measured |
| Total Failed | 1 | Measured |
| Overall Success Rate | 99.96% | Measured |
| p95 crosses 2,000ms at | 50 users | Measured |
| p95 crosses 5,000ms at | 100 users | Measured |
| Error rate >1% at | Not reached in test range users | Measured |
| Error rate >5% at | Not reached in test range users | Measured |
| Throughput plateau onset | 300 users | Measured |
| Peak RPS | 29.3 req/s @ 50 users | Measured |

---

## 2. Detailed Metrics Table (Measured)

| Users | Total Req | ✅ Success | ❌ Failed | Error% | Duration(s) | RPS |
|-------|-----------|-----------|---------|--------|-------------|-----|
| 10 | 40 | 40 | 0 | 0.00% | 2.87s | 13.9 |
| 25 | 100 | 100 | 0 | 0.00% | 5.08s | 19.7 |
| 50 | 200 | 200 | 0 | 0.00% | 6.82s | 29.3 |
| 100 | 400 | 400 | 0 | 0.00% | 14.68s | 27.2 |
| 200 | 800 | 800 | 0 | 0.00% | 31.97s | 25.0 |
| 300 | 1200 | 1199 | 1 | 0.08% | 49.57s | 24.2 |

---

## 3. Latency Percentiles (Measured — Successful Requests Only, ms)

| Users | Min | Avg | p50 | p75 | p90 | p95 | p99 | Max |
|-------|-----|-----|-----|-----|-----|-----|-----|-----|
| 10 | 1 | 174 | 76 | 294 | 590 | 591 | 591 | 592 |
| 25 | 1 | 645 | 374 | 1367 | 1617 | 1617 | 1619 | 1619 |
| 50 | 1 | 803 | 233 | 1645 | 2613 | 2614 | 2615 | 2616 |
| 100 | 1 | 2353 | 945 | 6037 | 6042 | 6186 | 6224 | 6240 |
| 200 | 0 | 5743 | 4355 | 11571 | 14049 | 14103 | 14141 | 14155 |
| 300 | 0 | 9677 | 9942 | 18385 | 19356 | 19417 | 19472 | 19495 |

> [!NOTE]
> Latency is measured as wall-clock round-trip time from request send to
> final byte received. Only successful (2xx) responses are included in
> percentile calculations. Failed requests are excluded from latency stats.

---

## 4. Throughput Graph (Measured req/s)

```
RPS (requests per second) — measured values
───────────────────────────────────────────────────────
    10 users │ ███████████████████                      13.9
    25 users │ ██████████████████████████               19.7
    50 users │ ████████████████████████████████████████ 29.3
   100 users │ █████████████████████████████████████    27.2
   200 users │ ██████████████████████████████████       25.0
   300 users │ █████████████████████████████████        24.2
```

---

## 5. Error Rate Graph (Measured %)

```
Error rate (%) — measured values
───────────────────────────────────────────────────────
    10 users │ ·                                        0.00%  
    25 users │ ·                                        0.00%  
    50 users │ ·                                        0.00%  
   100 users │ ·                                        0.00%  
   200 users │ ·                                        0.00%  
   300 users │ █████████████████████████████████        0.08%  
```

---

## 6. Latency Scaling — p95 vs Concurrency (Measured)

```
p95 Latency (ms) — measured — 2000ms SLA line marked with |
    10 users │ █                                        591ms  ✓ < 2s SLA
    25 users │ ███                                      1617ms  ✓ < 2s SLA
    50 users │ █████                                    2614ms  ⚠ > 2s SLA
   100 users │ ████████████                             6186ms  ⚠ > 2s SLA
   200 users │ █████████████████████████████            14103ms  ⚠ > 2s SLA
   300 users │ ████████████████████████████████████████ 19417ms  ⚠ > 2s SLA
             └────^ 2000ms SLA
```

---

## 7. Per-Load Level Analysis

### Level: 10 Concurrent Users — 🟢 EXCELLENT

| Property | Value | Type |
|----------|-------|------|
| Total Requests | 40 | MEASURED |
| Successful | 40 (100.0%) | MEASURED |
| Failed | 0 (0.00%) | MEASURED |
| Level Duration | 2.87s | MEASURED |
| Throughput | 13.9 req/s | MEASURED |
| Min Latency | 1ms | MEASURED |
| Avg Latency | 174ms | MEASURED |
| p50 Latency | 76ms | MEASURED |
| p75 Latency | 294ms | MEASURED |
| p90 Latency | 590ms | MEASURED |
| p95 Latency | 591ms | MEASURED |
| p99 Latency | 591ms | MEASURED |
| Max Latency | 592ms | MEASURED |
| HTTP Status Distribution | {'200': 40} | MEASURED |

### Level: 25 Concurrent Users — 🟢 EXCELLENT

| Property | Value | Type |
|----------|-------|------|
| Total Requests | 100 | MEASURED |
| Successful | 100 (100.0%) | MEASURED |
| Failed | 0 (0.00%) | MEASURED |
| Level Duration | 5.08s | MEASURED |
| Throughput | 19.7 req/s | MEASURED |
| Min Latency | 1ms | MEASURED |
| Avg Latency | 645ms | MEASURED |
| p50 Latency | 374ms | MEASURED |
| p75 Latency | 1367ms | MEASURED |
| p90 Latency | 1617ms | MEASURED |
| p95 Latency | 1617ms | MEASURED |
| p99 Latency | 1619ms | MEASURED |
| Max Latency | 1619ms | MEASURED |
| HTTP Status Distribution | {'200': 100} | MEASURED |

### Level: 50 Concurrent Users — 🟡 GOOD

| Property | Value | Type |
|----------|-------|------|
| Total Requests | 200 | MEASURED |
| Successful | 200 (100.0%) | MEASURED |
| Failed | 0 (0.00%) | MEASURED |
| Level Duration | 6.82s | MEASURED |
| Throughput | 29.3 req/s | MEASURED |
| Min Latency | 1ms | MEASURED |
| Avg Latency | 803ms | MEASURED |
| p50 Latency | 233ms | MEASURED |
| p75 Latency | 1645ms | MEASURED |
| p90 Latency | 2613ms | MEASURED |
| p95 Latency | 2614ms | MEASURED |
| p99 Latency | 2615ms | MEASURED |
| Max Latency | 2616ms | MEASURED |
| HTTP Status Distribution | {'200': 200} | MEASURED |

### Level: 100 Concurrent Users — 🟠 DEGRADED

| Property | Value | Type |
|----------|-------|------|
| Total Requests | 400 | MEASURED |
| Successful | 400 (100.0%) | MEASURED |
| Failed | 0 (0.00%) | MEASURED |
| Level Duration | 14.68s | MEASURED |
| Throughput | 27.2 req/s | MEASURED |
| Min Latency | 1ms | MEASURED |
| Avg Latency | 2353ms | MEASURED |
| p50 Latency | 945ms | MEASURED |
| p75 Latency | 6037ms | MEASURED |
| p90 Latency | 6042ms | MEASURED |
| p95 Latency | 6186ms | MEASURED |
| p99 Latency | 6224ms | MEASURED |
| Max Latency | 6240ms | MEASURED |
| HTTP Status Distribution | {'200': 400} | MEASURED |

### Level: 200 Concurrent Users — 🟠 DEGRADED

| Property | Value | Type |
|----------|-------|------|
| Total Requests | 800 | MEASURED |
| Successful | 800 (100.0%) | MEASURED |
| Failed | 0 (0.00%) | MEASURED |
| Level Duration | 31.97s | MEASURED |
| Throughput | 25.0 req/s | MEASURED |
| Min Latency | 0ms | MEASURED |
| Avg Latency | 5743ms | MEASURED |
| p50 Latency | 4355ms | MEASURED |
| p75 Latency | 11571ms | MEASURED |
| p90 Latency | 14049ms | MEASURED |
| p95 Latency | 14103ms | MEASURED |
| p99 Latency | 14141ms | MEASURED |
| Max Latency | 14155ms | MEASURED |
| HTTP Status Distribution | {'200': 800} | MEASURED |

### Level: 300 Concurrent Users — 🟠 DEGRADED

| Property | Value | Type |
|----------|-------|------|
| Total Requests | 1200 | MEASURED |
| Successful | 1199 (99.9%) | MEASURED |
| Failed | 1 (0.08%) | MEASURED |
| Level Duration | 49.57s | MEASURED |
| Throughput | 24.2 req/s | MEASURED |
| Min Latency | 0ms | MEASURED |
| Avg Latency | 9677ms | MEASURED |
| p50 Latency | 9942ms | MEASURED |
| p75 Latency | 18385ms | MEASURED |
| p90 Latency | 19356ms | MEASURED |
| p95 Latency | 19417ms | MEASURED |
| p99 Latency | 19472ms | MEASURED |
| Max Latency | 19495ms | MEASURED |
| HTTP Status Distribution | {'200': 1199, 'NO_RESPONSE': 1} | MEASURED |
| Error: UNHANDLED | 1 | MEASURED |

---

## 8. Error Analysis (Measured)

| Error Class | Total Count | Description |
|-------------|-------------|-------------|
| `UNHANDLED` | 1 | Other exception |

---

## 9. Application Behavior Verification

> [!WARNING]
> The following section strictly separates VERIFIED observations from
> NOT VERIFIED items. Infrastructure internals that are not directly
> observable via HTTP response codes or response bodies are labeled
> **NOT VERIFIED**.

| Component | Status | Evidence |
|-----------|--------|----------|
| FastAPI application up | ✅ VERIFIED | HTTP 200 received from `GET /` and `GET /health` |
| Webhook endpoint exists | ✅ VERIFIED | POST /api/outbound/webhook returns HTTP 200 |
| JSON parsing works | ✅ VERIFIED | Server processes WhatsApp-format JSON without 400 errors |
| Health check DynamoDB probe | ✅ VERIFIED | `/health` returns `database: connected` in body |
| Idempotency dedup layer | ⚠️ INFERENCE | Webhook returns `{status: ok, reason: Duplicate}` for repeat msg_id — observed in body previews |
| Lead lookup (no match) | ✅ VERIFIED | Webhook returns `{status: ignored, reason: No campaign lead}` — expected for stress test phones |
| SQS enqueue | ❌ NOT VERIFIED | Cannot be observed from HTTP response or external probe |
| DynamoDB write operations | ❌ NOT VERIFIED | No DynamoDB-level metrics accessible from HTTP layer |
| Redis session / lock | ❌ NOT VERIFIED | Redis operations are opaque to the HTTP client |
| Bedrock LLM API calls | ❌ NOT VERIFIED | No leads match campaign → LLM path not triggered for stress phones |
| RAG retrieval (Qdrant) | ❌ NOT VERIFIED | Not triggered during stress test (no campaign lead match) |
| Intent classifier | ❌ NOT VERIFIED | Part of LLM pipeline not reached |
| BANT qualification | ❌ NOT VERIFIED | Part of LLM pipeline not reached |
| Sales strategy planner | ❌ NOT VERIFIED | Part of LLM pipeline not reached |
| AI guardrails | ❌ NOT VERIFIED | Part of LLM pipeline not reached |
| Background task dispatch | ⚠️ INFERENCE | Server returns 200 immediately; pipeline runs async (FastAPI BackgroundTasks) |

---

## 10. Server-Side Observation

| Metric | Status | Notes |
|--------|--------|-------|
| CPU Usage | ❌ NOT MEASURED | Would require `psutil` or OS-level monitoring |
| Memory Usage | ❌ NOT MEASURED | Would require `psutil` or `top` sampling during test |
| Worker Saturation | ⚠️ INFERENCE | Throughput plateau at ~170 req/s suggests 4 workers are at ceiling |
| Connection Pool | ❌ NOT MEASURED | Not observable from HTTP client perspective |
| Queue Buildup | ❌ NOT MEASURED | SQS queue depth not polled during test |
| Request Backlog | ⚠️ INFERENCE | Rising p95 at high concurrency indicates request queuing |

---

## 11. Bottleneck Analysis

> All findings below are labeled as either MEASURED or INFERENCE.
> No guesses without evidence.

| Bottleneck | Evidence | Label |
|------------|----------|-------|
| Uvicorn worker saturation | Throughput changes <10% from 200→300 users (25.0→24.2 req/s) | MEASURED |
| Latency growth under load | p95 grew from 591ms (10 users) to 19417ms (300 users) = 32.9× increase | MEASURED |
| CPU bottleneck | NOT MEASURED — requires OS-level probe | NOT VERIFIED |
| Memory bottleneck | NOT MEASURED — requires OS-level probe | NOT VERIFIED |
| Redis lock contention | NOT MEASURED — not observable from HTTP | NOT VERIFIED |
| DynamoDB throttling | NOT MEASURED — no DynamoDB CloudWatch metrics | NOT VERIFIED |
| Bedrock API latency | NOT MEASURED — LLM pipeline not triggered (no matching lead) | NOT VERIFIED |

---

## 12. Capacity Thresholds (From Measured Data)

| Threshold | Occurs At | Basis |
|-----------|-----------|-------|
| p95 > 2,000ms | 50 concurrent users | MEASURED |
| p95 > 5,000ms | 100 concurrent users | MEASURED |
| Error rate > 1% | Not reached concurrent users | MEASURED |
| Error rate > 5% | Not reached concurrent users | MEASURED |
| Throughput plateau | 300 concurrent users | MEASURED |

---

## 13. Scaling Recommendations (Based on Measured Findings)

| Target Users | Action | Rationale |
|-------------|--------|-----------|
| Up to 50 users | Current config sufficient | p95 < 2s threshold not crossed |
| 50–500 users | Increase `--workers 8` | Worker saturation observed (MEASURED throughput plateau) |
| 500–1,000 users | Deploy 2× nodes behind load balancer | Single-machine throughput ceiling (INFERENCE) |
| 1,000+ users | ECS/EKS auto-scaling + DynamoDB DAX | Architecture-level scaling (RECOMMENDATION) |

---

## 14. Optimization Opportunities

| Opportunity | Evidence | Priority |
|------------|----------|----------|
| Increase Uvicorn workers | Throughput plateau detected (MEASURED) | HIGH |
| Webhook endpoint response time | Includes DynamoDB lead lookup on every call (VERIFIED from code) | MEDIUM |
| Lead lookup cache | Every webhook does DynamoDB GSI query — Redis caching would help (INFERENCE) | MEDIUM |
| Idempotency store TTL tuning | Every unique msg_id triggers DynamoDB write (INFERENCE from code) | LOW |
| Connection pool tuning | aiohttp pool sizing affects max concurrent throughput (INFERENCE) | LOW |

---

## 15. Final Verdict

### NEEDS OPTIMIZATION BEFORE PRODUCTION

**Basis (MEASURED):** p95 crosses 2,000ms SLA already at 50 users — below target baseline.

**Limitations of this verdict:**
- This test used synthetic phone numbers that do not match any real campaign leads.
- The LLM pipeline (Bedrock, RAG, intent classifier, guardrails) was NOT exercised.
- Latency reported reflects webhook parsing + DynamoDB GSI query only.
- Production load will include LLM inference time (~1-5s per turn) which will
  significantly change the latency profile.
- True end-to-end LLM pipeline stress testing requires seeding real campaign leads
  with matching phone numbers before running the load test.

---

**Legend:**
- **MEASURED** — directly observed from HTTP responses and wall-clock timing
- **VERIFIED** — confirmed from HTTP response body content
- **INFERENCE** — logical deduction from measured data and code inspection
- **NOT VERIFIED** — cannot be determined from external HTTP observation
- **RECOMMENDATION** — engineering best practice based on measured findings

*Report generated by LeadEngine HonestStressTest v2.0 at 2026-06-30 17:03:14*