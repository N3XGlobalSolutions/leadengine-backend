# System Optimization Plan - High RPS & Stability

## Goal
Improve system performance to handle 50+ concurrent users with <2s average latency and 99.9% success rate (currently 81.7% failure rate at 50 users).

## User Review Required
> [!IMPORTANT]
> Please clarify the following before we proceed to implementation:

1. **Database Mode**: Are you running with `USE_DYNAMODB=true` or `false` (MongoDB)? The optimization strategy differs for each.
2. **Qdrant Specs**: What is the EC2 instance type for Qdrant? (t3.micro might be choking under 50 concurrent vectors searches).
3. **Cache Layer**: Do you prefer **Redis (ElastiCache)** (faster, costlier) or **DynamoDB** (serverless, slightly slower) for semantic caching? *Recommendation: DynamoDB for ease of maintenance.*
4. **Current Flow**: Does the `/chat` endpoint wait for document processing? (It should not).

## Analysis of Failure
- **High Latency (5.4s avg)**: Likely due to serial execution of Embedding -> Vector Search -> LLM Generation.
- **High Error Rate (81%)**: Likely due to Timeouts (Lambda 30s limit or API Gateway 29s limit) or DB Connection Exhaustion.

## Proposed Changes

### 1. Implementation of Semantic Layer (Caching)
**Why**: reducing calls to LLM is the only way to dramatically increase RPS and reduce cost.
- **Component**: `app/services/cache_service.py` [NEW]
- **Storage**: DynamoDB Table (`qbot_response_cache`)
- **Logic**: 
    1. Hash user query + vector embedding.
    2. Check cache for similar query (>0.95 similarity).
    3. Return cached response if found (Latency: <200ms).

### 2. Database Connection Optimization
**Why**: "500 Internal Server Error" often means connection pool exhaustion in Lambda.
- **File**: `app/core/database.py`
- **Change**: Ensure `QdrantClient` and `DynamoDB/Motor` clients are properly reused across warm starts and don't leak connections.
- **Action**: Implement explicit connection checks in `lambda_handler.py`.

### 3. Asynchronous Processing (Fire & Forget)
**Why**: If chat waits for non-critical updates, latency skyrockets.
- **File**: `app/routes/v1/chat.py`
- **Change**: Ensure any side-effects (logging, analytics, updating history) are pushed to SQS, not awaited in the response path.

### 4. Infrastructure Tuning (DevOps)
- **Lambda**: Increase Memory (supports faster CPU) to 2048MB.
- **Qdrant**: Recommend vertical scaling if EC2 CPU is >80%.
- **Provisioned Concurrency**: Enable for 5-10 instances to kill cold starts.

## Verification Plan

### Automated Stress Tests
Run the existing stress test suite:
```bash
# Command to run stress test
locust -f tests/stress_test.py --headless -u 50 -r 10 --run-time 1m
```
**Success Criteria**:
- RPS > 20
- Error Rate < 1%
- Avg Latency < 2000ms

### Manual Steps
1. Deploy updated Lambda.
2. Monitor CloudWatch Logs for "Connection refused" or "Timeout".
3. Verify Cache Hit in DynamoDB Console.
