# Chatbot Stress Testing

Comprehensive stress testing framework for the chatbot backend and AWS Bedrock API.

## Overview

This stress testing suite simulates concurrent users interacting with your chatbot to measure:
- **Response times** (avg, p50, p95, p99)
- **Error rates** and failure patterns
- **Throughput** (requests per second)
- **System capacity** and breaking points
- **AWS Bedrock API** performance under load

## Quick Start

### 1. Install Dependencies

```bash
cd d:\qbot\qbot-backend\tests
pip install -r requirements-stress-test.txt
```

### 2. Configure Test Parameters

Edit `test_config.json`:

```json
{
  "api_url": "https://0wryhzy1j5.execute-api.us-east-1.amazonaws.com/prod",
  "tenant_id": "your-actual-tenant-id",
  "api_key": "your-actual-api-key",
  "concurrent_users": [10, 50, 100, 200, 300],
  "messages_per_user": 6,
  "request_timeout": 60
}
```

⚠️ **Important**: Replace `tenant_id` and `api_key` with your actual credentials.

### 3. Run Tests

**Full test suite:**
```bash
python stress_test_runner.py
```

**Quick smoke test (5 users):**
```bash
python stress_test_runner.py --users 5 --quick-test
```

**Custom load levels:**
```bash
python stress_test_runner.py --users 25 75 150
```

## Test Configuration

| Parameter | Description | Default |
|-----------|-------------|---------|
| `api_url` | Backend API base URL | Production URL |
| `tenant_id` | Tenant ID for testing | Required |
| `api_key` | API key (X-API-Key header) | Required |
| `concurrent_users` | List of load levels to test | `[10, 50, 100, 200, 300]` |
| `messages_per_user` | Messages per conversation | `6` |
| `request_timeout` | Timeout per request (seconds) | `60` |
| `delay_between_messages` | Delay within conversation (seconds) | `1.5` |
| `delay_between_tests` | Recovery time between tests (seconds) | `10` |

## Understanding Results

### Response Time Benchmarks

- **< 2s**: ✅ Excellent user experience
- **2-5s**: ⚠️ Acceptable but noticeable delay
- **> 5s**: ❌ Poor user experience

### Error Rate Thresholds

- **< 1%**: ✅ System is healthy
- **1-5%**: ⚠️ System under moderate stress
- **> 5%**: ❌ System capacity exceeded

### Output Files

All results are saved to `stress_test_results/`:

- `results_TIMESTAMP.json` - Raw JSON data
- `summary_TIMESTAMP.txt` - Human-readable report with recommendations
- `chart_TIMESTAMP.png` - Performance visualization (if matplotlib installed)

### Example Output

```
===============================================================
Load Test Results - 100 Concurrent Users
===============================================================

Test Duration: 45.23s
Total Requests: 600
Successful: 598 (99.7%)
Failed: 2 (0.3%)
Throughput: 13.27 req/s

---------------------------------------------------------------
Response Time Distribution:
---------------------------------------------------------------
  Minimum:  842ms
  Average:  1543ms
  Median:   1489ms
  95th %:   2301ms
  99th %:   3012ms
  Maximum:  3456ms

---------------------------------------------------------------
Performance Assessment:
---------------------------------------------------------------
  ✅ LOW ERROR RATE - System is healthy
  ✅ EXCELLENT response times (p95 < 2s)
```

## Test Scenarios

The framework generates realistic conversations including:

1. **Greetings** - Natural conversation starters
2. **Product Questions** - Queries about services/pricing
3. **Qualification Responses** - Answers to qualification questions
4. **Contact Information** - Name, email, phone sharing
5. **Follow-up Questions** - Continued engagement
6. **Edge Cases** - Long messages, special characters, unicode (10%)

## CLI Options

```bash
# Use custom config file
python stress_test_runner.py --config my_config.json

# Test specific load levels
python stress_test_runner.py --users 10 25 50

# Quick test mode (fewer messages, shorter delays)
python stress_test_runner.py --quick-test

# Skip visualization generation
python stress_test_runner.py --no-viz
```

## Advanced Usage

### Running Individual Tests

```python
from stress_test import ChatbotStressTester

tester = ChatbotStressTester(
    api_url="https://your-api.com/prod",
    tenant_id="your-tenant-id",
    api_key="your-api-key"
)

results = await tester.run_load_test(
    concurrent_users=50,
    messages_per_user=6
)

tester.print_results(results)
```

### Custom Conversation Scenarios

```python
from conversation_scenarios import ConversationScenarios

# Generate custom conversations
conversations = ConversationScenarios.generate_batch_conversations(
    count=100,
    messages_per_conversation=8
)

# Use edge cases
edge_conversation = ConversationScenarios.generate_edge_case_conversation()
```

## Best Practices

1. **Start Small**: Begin with low concurrent users (5-10) to validate setup
2. **Off-Peak Testing**: Run during low-traffic periods to avoid affecting real users
3. **Gradual Increase**: Test incrementally to identify exact breaking point
4. **Monitor Backend**: Watch CloudWatch/logs during tests for backend insights
5. **Cost Awareness**: Bedrock API calls cost money - be mindful of test scale
6. **Recovery Time**: Allow system to recover between high-load tests

## Troubleshooting

**Problem**: `test_config.json not found`
- **Solution**: Create config file in the same directory as the test scripts

**Problem**: `Invalid API key` errors
- **Solution**: Verify your API key in the config file is correct
- Check that the tenant_id matches the API key

**Problem**: All requests timing out
- **Solution**: Verify the API URL is correct and accessible
- Check network connectivity
- Increase `request_timeout` in config

**Problem**: High error rates even at low loads
- **Solution**: Check backend logs for issues
- Verify backend is healthy before testing
- Ensure Bedrock API quota is sufficient

## Metrics Explained

| Metric | Description |
|--------|-------------|
| **p50 (Median)** | 50% of requests complete faster than this |
| **p95** | 95% of requests complete faster than this |
| **p99** | 99% of requests complete faster than this |
| **RPS** | Requests per second (throughput) |
| **Error Rate** | Percentage of failed requests |
| **Total Duration** | Time to complete all conversations |

## What to Look For

### Healthy System
- Error rate < 1%
- p95 response time < 2s
- Linear or near-linear scaling

### System Under Stress
- Error rate 1-5%
- p95 response time 2-5s
- Response times increasing non-linearly

### System at Capacity
- Error rate > 5%
- p95 response time > 5s
- Timeout errors appearing
- AWS Bedrock throttling errors

## Support

For issues or questions:
1. Check backend CloudWatch logs
2. Review error messages in test output
3. Verify AWS Bedrock quota/limits
4. Test with single user first to isolate issues
