"""Comprehensive stress testing for chatbot backend and AWS Bedrock API."""

import asyncio
import time
import json
import statistics
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime
import aiohttp
from conversation_scenarios import ConversationScenarios


@dataclass
class RequestMetrics:
    """Metrics for a single request."""
    timestamp: float
    duration: float
    status_code: int
    success: bool
    error_type: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class ConversationMetrics:
    """Metrics for a complete conversation."""
    user_id: int
    conversation_id: Optional[str]
    messages_sent: int
    total_duration: float
    requests: List[RequestMetrics]
    success: bool


@dataclass
class LoadTestResults:
    """Results from a load test run."""
    concurrent_users: int
    total_requests: int
    successful_requests: int
    failed_requests: int
    total_duration: float
    
    # Response time metrics
    avg_response_time: float
    min_response_time: float
    max_response_time: float
    p50_response_time: float
    p95_response_time: float
    p99_response_time: float
    
    # Throughput
    requests_per_second: float
    
    # Error analysis
    error_rate: float
    errors_by_type: Dict[str, int]
    
    # Timestamp
    timestamp: str


class ChatbotStressTester:
    """Stress tester for chatbot backend."""
    
    def __init__(
        self,
        api_url: str,
        tenant_id: str,
        api_key: str,
        request_timeout: int = 60,
        delay_between_messages: float = 1.5
    ):
        """
        Initialize stress tester.
        
        Args:
            api_url: Base API URL
            tenant_id: Tenant ID for testing
            api_key: API key for authentication
            request_timeout: Timeout for each request in seconds
            delay_between_messages: Delay between messages in same conversation
        """
        self.api_url = api_url.rstrip('/')
        self.tenant_id = tenant_id
        self.api_key = api_key
        self.request_timeout = request_timeout
        self.delay_between_messages = delay_between_messages
        self.chat_endpoint = f"{self.api_url}/api/chat"
    
    async def send_message(
        self,
        session: aiohttp.ClientSession,
        query: str,
        conversation_id: Optional[str] = None,
        user_identifier: Optional[str] = None
    ) -> Tuple[RequestMetrics, Optional[Dict]]:
        """
        Send a single chat message.
        
        Args:
            session: aiohttp session
            query: Message to send
            conversation_id: Optional conversation ID
            user_identifier: Optional user identifier
            
        Returns:
            Tuple of (metrics, response_data)
        """
        start_time = time.time()
        
        headers = {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json"
        }
        
        payload = {
            "query": query,
            "tenant_id": self.tenant_id,
        }
        
        if conversation_id:
            payload["conversation_id"] = conversation_id
        if user_identifier:
            payload["user_identifier"] = user_identifier
        
        try:
            async with session.post(
                self.chat_endpoint,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.request_timeout)
            ) as response:
                duration = time.time() - start_time
                
                try:
                    response_data = await response.json()
                except:
                    response_data = None
                
                success = 200 <= response.status < 300
                
                metrics = RequestMetrics(
                    timestamp=start_time,
                    duration=duration,
                    status_code=response.status,
                    success=success,
                    error_type=None if success else f"HTTP_{response.status}",
                    error_message=None if success else str(response_data)
                )
                
                return metrics, response_data if success else None
                
        except asyncio.TimeoutError:
            duration = time.time() - start_time
            metrics = RequestMetrics(
                timestamp=start_time,
                duration=duration,
                status_code=0,
                success=False,
                error_type="TIMEOUT",
                error_message=f"Request exceeded {self.request_timeout}s timeout"
            )
            return metrics, None
            
        except Exception as e:
            duration = time.time() - start_time
            metrics = RequestMetrics(
                timestamp=start_time,
                duration=duration,
                status_code=0,
                success=False,
                error_type=type(e).__name__,
                error_message=str(e)
            )
            return metrics, None
    
    async def simulate_user_conversation(
        self,
        session: aiohttp.ClientSession,
        user_id: int,
        messages: List[str]
    ) -> ConversationMetrics:
        """
        Simulate a complete user conversation.
        
        Args:
            session: aiohttp session
            user_id: Unique user ID
            messages: List of messages to send
            
        Returns:
            ConversationMetrics
        """
        conversation_id = None
        user_identifier = f"stress_test_user_{user_id}_{int(time.time())}"
        request_metrics = []
        conversation_start = time.time()
        
        for i, message in enumerate(messages):
            # Send message
            metrics, response_data = await self.send_message(
                session,
                query=message,
                conversation_id=conversation_id,
                user_identifier=user_identifier
            )
            
            request_metrics.append(metrics)
            
            # Extract conversation ID from first response
            if i == 0 and response_data and "conversation_id" in response_data:
                conversation_id = response_data["conversation_id"]
            
            # Delay between messages (except after last message)
            if i < len(messages) - 1 and metrics.success:
                await asyncio.sleep(self.delay_between_messages)
        
        total_duration = time.time() - conversation_start
        success = all(m.success for m in request_metrics)
        
        return ConversationMetrics(
            user_id=user_id,
            conversation_id=conversation_id,
            messages_sent=len(messages),
            total_duration=total_duration,
            requests=request_metrics,
            success=success
        )
    
    async def run_load_test(
        self,
        concurrent_users: int,
        messages_per_user: int = 6
    ) -> LoadTestResults:
        """
        Run a load test with specified concurrent users.
        
        Args:
            concurrent_users: Number of concurrent users to simulate
            messages_per_user: Number of messages per user conversation
            
        Returns:
            LoadTestResults
        """
        print(f"\n{'='*60}")
        print(f"Starting load test with {concurrent_users} concurrent users")
        print(f"{'='*60}")
        
        # Generate conversations
        conversations = ConversationScenarios.generate_batch_conversations(
            concurrent_users,
            messages_per_user
        )
        
        # Create session with connection pooling
        connector = aiohttp.TCPConnector(
            limit=concurrent_users * 2,  # Connection pool size
            limit_per_host=concurrent_users * 2
        )
        
        async with aiohttp.ClientSession(connector=connector) as session:
            test_start = time.time()
            
            # Run all conversations concurrently
            tasks = [
                self.simulate_user_conversation(session, i, conversations[i])
                for i in range(concurrent_users)
            ]
            
            conversation_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            test_duration = time.time() - test_start
        
        # Process results
        all_request_metrics = []
        successful_conversations = 0
        errors_by_type = {}
        
        for result in conversation_results:
            if isinstance(result, Exception):
                error_type = type(result).__name__
                errors_by_type[error_type] = errors_by_type.get(error_type, 0) + 1
                continue
            
            if isinstance(result, ConversationMetrics):
                all_request_metrics.extend(result.requests)
                if result.success:
                    successful_conversations += 1
                
                # Count errors
                for req in result.requests:
                    if not req.success and req.error_type:
                        errors_by_type[req.error_type] = errors_by_type.get(req.error_type, 0) + 1
        
        # Calculate metrics
        successful_requests = sum(1 for m in all_request_metrics if m.success)
        failed_requests = len(all_request_metrics) - successful_requests
        
        response_times = [m.duration for m in all_request_metrics if m.success]
        
        if response_times:
            avg_response_time = statistics.mean(response_times)
            min_response_time = min(response_times)
            max_response_time = max(response_times)
            p50_response_time = statistics.median(response_times)
            
            sorted_times = sorted(response_times)
            p95_idx = int(len(sorted_times) * 0.95)
            p99_idx = int(len(sorted_times) * 0.99)
            p95_response_time = sorted_times[p95_idx] if p95_idx < len(sorted_times) else sorted_times[-1]
            p99_response_time = sorted_times[p99_idx] if p99_idx < len(sorted_times) else sorted_times[-1]
        else:
            avg_response_time = min_response_time = max_response_time = 0
            p50_response_time = p95_response_time = p99_response_time = 0
        
        total_requests = len(all_request_metrics)
        requests_per_second = total_requests / test_duration if test_duration > 0 else 0
        error_rate = (failed_requests / total_requests * 100) if total_requests > 0 else 0
        
        return LoadTestResults(
            concurrent_users=concurrent_users,
            total_requests=total_requests,
            successful_requests=successful_requests,
            failed_requests=failed_requests,
            total_duration=test_duration,
            avg_response_time=avg_response_time,
            min_response_time=min_response_time,
            max_response_time=max_response_time,
            p50_response_time=p50_response_time,
            p95_response_time=p95_response_time,
            p99_response_time=p99_response_time,
            requests_per_second=requests_per_second,
            error_rate=error_rate,
            errors_by_type=errors_by_type,
            timestamp=datetime.now().isoformat()
        )
    
    def print_results(self, results: LoadTestResults):
        """Print formatted test results."""
        print(f"\n{'='*60}")
        print(f"Load Test Results - {results.concurrent_users} Concurrent Users")
        print(f"{'='*60}")
        print(f"\nTest Duration: {results.total_duration:.2f}s")
        print(f"Total Requests: {results.total_requests}")
        print(f"Successful: {results.successful_requests} ({100-results.error_rate:.1f}%)")
        print(f"Failed: {results.failed_requests} ({results.error_rate:.1f}%)")
        print(f"Throughput: {results.requests_per_second:.2f} req/s")
        
        print(f"\n{'-'*60}")
        print("Response Time Distribution:")
        print(f"{'-'*60}")
        print(f"  Minimum:  {results.min_response_time*1000:.0f}ms")
        print(f"  Average:  {results.avg_response_time*1000:.0f}ms")
        print(f"  Median:   {results.p50_response_time*1000:.0f}ms")
        print(f"  95th %:   {results.p95_response_time*1000:.0f}ms")
        print(f"  99th %:   {results.p99_response_time*1000:.0f}ms")
        print(f"  Maximum:  {results.max_response_time*1000:.0f}ms")
        
        if results.errors_by_type:
            print(f"\n{'-'*60}")
            print("Errors by Type:")
            print(f"{'-'*60}")
            for error_type, count in sorted(results.errors_by_type.items(), key=lambda x: x[1], reverse=True):
                print(f"  {error_type}: {count}")
        
        # Performance assessment
        print(f"\n{'-'*60}")
        print("Performance Assessment:")
        print(f"{'-'*60}")
        
        if results.error_rate > 5:
            print("  ❌ HIGH ERROR RATE - System is under stress")
        elif results.error_rate > 1:
            print("  ⚠️  MODERATE ERROR RATE - Approaching capacity")
        else:
            print("  ✅ LOW ERROR RATE - System is healthy")
        
        if results.p95_response_time < 2:
            print("  ✅ EXCELLENT response times (p95 < 2s)")
        elif results.p95_response_time < 5:
            print("  ⚠️  ACCEPTABLE response times (p95 < 5s)")
        else:
            print("  ❌ POOR response times (p95 > 5s)")
        
        print()


async def run_single_test(
    tester: ChatbotStressTester,
    concurrent_users: int,
    messages_per_user: int
) -> LoadTestResults:
    """Run a single load test."""
    results = await tester.run_load_test(concurrent_users, messages_per_user)
    tester.print_results(results)
    return results


async def main():
    """Main entry point for stress testing."""
    # Load configuration
    try:
        with open('test_config.json', 'r') as f:
            config = json.load(f)
    except FileNotFoundError:
        print("Error: test_config.json not found!")
        print("Please create test_config.json with your configuration.")
        return
    
    # Validate configuration
    if config['tenant_id'] == 'your-tenant-id-here' or config['api_key'] == 'your-api-key-here':
        print("Error: Please update test_config.json with your actual tenant_id and api_key!")
        return
    
    # Initialize tester
    tester = ChatbotStressTester(
        api_url=config['api_url'],
        tenant_id=config['tenant_id'],
        api_key=config['api_key'],
        request_timeout=config.get('request_timeout', 60),
        delay_between_messages=config.get('delay_between_messages', 1.5)
    )
    
    # Run tests for each load level
    concurrent_users_list = config.get('concurrent_users', [10, 50, 100, 200, 300])
    messages_per_user = config.get('messages_per_user', 6)
    delay_between_tests = config.get('delay_between_tests', 10)
    
    all_results = []
    
    for concurrent_users in concurrent_users_list:
        results = await run_single_test(tester, concurrent_users, messages_per_user)
        all_results.append(results)
        
        # Wait between tests to let system recover
        if concurrent_users != concurrent_users_list[-1]:
            print(f"\nWaiting {delay_between_tests}s before next test...")
            await asyncio.sleep(delay_between_tests)
    
    # Print summary comparison
    print(f"\n{'='*80}")
    print("SUMMARY - All Load Tests")
    print(f"{'='*80}\n")
    
    print(f"{'Users':<8} {'Requests':<10} {'Success%':<10} {'Error%':<10} {'Avg(ms)':<10} {'p95(ms)':<10} {'p99(ms)':<10}")
    print(f"{'-'*80}")
    
    for result in all_results:
        success_rate = 100 - result.error_rate
        print(
            f"{result.concurrent_users:<8} "
            f"{result.total_requests:<10} "
            f"{success_rate:<10.1f} "
            f"{result.error_rate:<10.1f} "
            f"{result.avg_response_time*1000:<10.0f} "
            f"{result.p95_response_time*1000:<10.0f} "
            f"{result.p99_response_time*1000:<10.0f}"
        )
    
    # Save results to file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_file = f"stress_test_results_{timestamp}.json"
    
    with open(results_file, 'w') as f:
        json.dump([asdict(r) for r in all_results], f, indent=2)
    
    print(f"\n✅ Results saved to: {results_file}")


if __name__ == "__main__":
    asyncio.run(main())
