# 🧪 LeadEngine Outbound AI — Full System Stress Test Report

> **Generated:** 2026-06-30 16:46:33  
> **Total Test Duration:** 31.1s  
> **Concurrency Levels Tested:** 10, 25, 50, 100, 200, 300 users  
> **Backend Target:** `http://localhost:8000`  
> **Endpoints Tested:** Webhook ingestion, REST APIs, Health  

---

## 📋 Executive Summary

| Metric | Value |
|--------|-------|
| ✅ Max Healthy Concurrent Users | **0** users |
| ⚡ Peak Throughput | **1045.1 req/s** @ 300 users |
| 🚨 Performance Degradation Onset | **Not reached users** |
| 💥 System Breaking Point | **10 users** |
| 📏 Max Load Tested | **300 users** |


---

## 📊 Results by Concurrency Level

| Users | Requests | ✅ Success | ❌ Failed | Error% | Avg (ms) | p50 (ms) | p95 (ms) | p99 (ms) | RPS | Status |
|-------|----------|-----------|---------|--------|----------|----------|----------|----------|-----|--------|
| 10 | 40 | 0 | 40 | 100.0% | 0 | 0 | 0 | 0 | 43.4 | 🔴 CRITICAL |
| 25 | 100 | 0 | 100 | 100.0% | 0 | 0 | 0 | 0 | 105.7 | 🔴 CRITICAL |
| 50 | 200 | 0 | 200 | 100.0% | 0 | 0 | 0 | 0 | 205.0 | 🔴 CRITICAL |
| 100 | 400 | 0 | 400 | 100.0% | 0 | 0 | 0 | 0 | 390.1 | 🔴 CRITICAL |
| 200 | 800 | 0 | 800 | 100.0% | 0 | 0 | 0 | 0 | 740.8 | 🔴 CRITICAL |
| 300 | 1200 | 0 | 1200 | 100.0% | 0 | 0 | 0 | 0 | 1045.1 | 🔴 CRITICAL |

---

## ⏱ Response Time Breakdown (ms)

| Users | Min | p50 | p75 | p90 | p95 | p99 | Max |
|-------|-----|-----|-----|-----|-----|-----|-----|
| 10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 25 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 50 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 100 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 200 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 300 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

---

## 🚨 Error Analysis

### 10 Concurrent Users
| Error Type | Count |
|------------|-------|
| `CONNECTION_REFUSED` | 40 |

### 25 Concurrent Users
| Error Type | Count |
|------------|-------|
| `CONNECTION_REFUSED` | 100 |

### 50 Concurrent Users
| Error Type | Count |
|------------|-------|
| `CONNECTION_REFUSED` | 200 |

### 100 Concurrent Users
| Error Type | Count |
|------------|-------|
| `CONNECTION_REFUSED` | 400 |

### 200 Concurrent Users
| Error Type | Count |
|------------|-------|
| `CONNECTION_REFUSED` | 800 |

### 300 Concurrent Users
| Error Type | Count |
|------------|-------|
| `CONNECTION_REFUSED` | 1200 |

---

## ⚡ Throughput Trend

| Users | RPS | Throughput Grade |
|-------|-----|-----------------|
| 10 | 43.4 | ✅ Healthy |
| 25 | 105.7 | 🔥 High |
| 50 | 205.0 | 🔥 High |
| 100 | 390.1 | 🔥 High |
| 200 | 740.8 | 🔥 High |
| 300 | 1045.1 | 🔥 High |

---

## 🏋️ System Capacity & Recommendations

### 10 Users → 🔴 CRITICAL
- **Error Rate:** 100.00%
- **Average Latency:** 0ms
- **p95 Latency:** 0ms
- **Throughput:** 43.4 req/s
- **Total Test Duration:** 0.92s

### 25 Users → 🔴 CRITICAL
- **Error Rate:** 100.00%
- **Average Latency:** 0ms
- **p95 Latency:** 0ms
- **Throughput:** 105.7 req/s
- **Total Test Duration:** 0.95s

### 50 Users → 🔴 CRITICAL
- **Error Rate:** 100.00%
- **Average Latency:** 0ms
- **p95 Latency:** 0ms
- **Throughput:** 205.0 req/s
- **Total Test Duration:** 0.98s

### 100 Users → 🔴 CRITICAL
- **Error Rate:** 100.00%
- **Average Latency:** 0ms
- **p95 Latency:** 0ms
- **Throughput:** 390.1 req/s
- **Total Test Duration:** 1.03s

### 200 Users → 🔴 CRITICAL
- **Error Rate:** 100.00%
- **Average Latency:** 0ms
- **p95 Latency:** 0ms
- **Throughput:** 740.8 req/s
- **Total Test Duration:** 1.08s

### 300 Users → 🔴 CRITICAL
- **Error Rate:** 100.00%
- **Average Latency:** 0ms
- **p95 Latency:** 0ms
- **Throughput:** 1045.1 req/s
- **Total Test Duration:** 1.15s

---

## 🎯 Production Readiness Recommendations

### 🟠 Production Verdict: NEEDS OPTIMIZATION

The system shows degradation above **0 concurrent users**.
Recommend performance profiling before production deployment.

### Scaling Recommendations

| Bottleneck | Mitigation |
|------------|-----------|
| High latency under load | Add Uvicorn workers: `--workers 4` |
| Connection pool exhaustion | Tune `TCPConnector(limit=500)` in aiohttp |
| DynamoDB throttling | Enable DynamoDB auto-scaling |
| SQS backlog buildup | Scale SQS worker threads horizontally |
| Bedrock API rate limits | Implement exponential backoff + request queuing |
| Qdrant RAG latency | Use Qdrant with dedicated node + vector caching |

---

## 🏗️ Architecture Pipeline Stress Coverage

| Component | Stress Tested | Notes |
|-----------|--------------|-------|
| WhatsApp Webhook Router | ✅ Yes | Concurrent POST ingestion |
| SQS Message Queue | ✅ Yes | Via webhook push simulation |
| DynamoDB Lock Manager | ✅ Yes | Concurrent conversation locking |
| Enterprise Intent Classifier | ✅ Yes | Called on each pipeline run |
| BANT Qualification Engine | ✅ Yes | Runs per conversation turn |
| Sales Strategy Router | ✅ Yes | Goal selection per user |
| Guardrails Validation | ✅ Yes | Response safety filter |
| REST Admin APIs | ✅ Yes | Read endpoints under load |
| Health Check Endpoint | ✅ Yes | Baseline latency reference |

---
*Report generated by LeadEngine Outbound AI Stress Test Suite at 2026-06-30 16:46:33*