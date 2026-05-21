"""Enhanced stress test runner with visualization and advanced features."""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime
from typing import List
from dataclasses import asdict

# Import the stress test module
from stress_test import ChatbotStressTester, LoadTestResults


def create_results_directory() -> Path:
    """Create and return results directory."""
    results_dir = Path("stress_test_results")
    results_dir.mkdir(exist_ok=True)
    return results_dir


def save_results_json(results: List[LoadTestResults], results_dir: Path) -> str:
    """Save results as JSON."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = results_dir / f"results_{timestamp}.json"
    
    with open(filename, 'w') as f:
        json.dump([asdict(r) for r in results], f, indent=2)
    
    return str(filename)


def save_results_summary(results: List[LoadTestResults], results_dir: Path) -> str:
    """Save human-readable summary."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = results_dir / f"summary_{timestamp}.txt"
    
    with open(filename, 'w') as f:
        f.write("=" * 80 + "\n")
        f.write("CHATBOT STRESS TEST SUMMARY\n")
        f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 80 + "\n\n")
        
        # Summary table
        f.write(f"{'Users':<8} {'Requests':<10} {'Success%':<10} {'Error%':<10} "
                f"{'Avg(ms)':<10} {'p95(ms)':<10} {'p99(ms)':<10} {'RPS':<10}\n")
        f.write("-" * 80 + "\n")
        
        for result in results:
            success_rate = 100 - result.error_rate
            f.write(
                f"{result.concurrent_users:<8} "
                f"{result.total_requests:<10} "
                f"{success_rate:<10.1f} "
                f"{result.error_rate:<10.1f} "
                f"{result.avg_response_time*1000:<10.0f} "
                f"{result.p95_response_time*1000:<10.0f} "
                f"{result.p99_response_time*1000:<10.0f} "
                f"{result.requests_per_second:<10.1f}\n"
            )
        
        # Detailed breakdown
        f.write("\n" + "=" * 80 + "\n")
        f.write("DETAILED RESULTS\n")
        f.write("=" * 80 + "\n\n")
        
        for result in results:
            f.write(f"\n{result.concurrent_users} Concurrent Users:\n")
            f.write("-" * 40 + "\n")
            f.write(f"  Total Duration:     {result.total_duration:.2f}s\n")
            f.write(f"  Total Requests:     {result.total_requests}\n")
            f.write(f"  Successful:         {result.successful_requests}\n")
            f.write(f"  Failed:             {result.failed_requests}\n")
            f.write(f"  Error Rate:         {result.error_rate:.2f}%\n")
            f.write(f"  Throughput:         {result.requests_per_second:.2f} req/s\n")
            f.write(f"\n  Response Times:\n")
            f.write(f"    Min:    {result.min_response_time*1000:.0f}ms\n")
            f.write(f"    Avg:    {result.avg_response_time*1000:.0f}ms\n")
            f.write(f"    Median: {result.p50_response_time*1000:.0f}ms\n")
            f.write(f"    p95:    {result.p95_response_time*1000:.0f}ms\n")
            f.write(f"    p99:    {result.p99_response_time*1000:.0f}ms\n")
            f.write(f"    Max:    {result.max_response_time*1000:.0f}ms\n")
            
            if result.errors_by_type:
                f.write(f"\n  Errors:\n")
                for error_type, count in sorted(result.errors_by_type.items(), key=lambda x: x[1], reverse=True):
                    f.write(f"    {error_type}: {count}\n")
        
        # Recommendations
        f.write("\n" + "=" * 80 + "\n")
        f.write("RECOMMENDATIONS\n")
        f.write("=" * 80 + "\n\n")
        
        # Find breaking point
        max_healthy_users = 0
        for result in results:
            if result.error_rate < 5 and result.p95_response_time < 5:
                max_healthy_users = result.concurrent_users
        
        if max_healthy_users > 0:
            f.write(f"✅ System can handle up to {max_healthy_users} concurrent users comfortably.\n")
        
        # Check for degradation
        if len(results) > 1:
            baseline = results[0]
            for result in results[1:]:
                if result.avg_response_time > baseline.avg_response_time * 2:
                    f.write(f"\n⚠️  Performance degradation observed at {result.concurrent_users} users:\n")
                    f.write(f"   Response time increased by {((result.avg_response_time / baseline.avg_response_time) - 1) * 100:.0f}%\n")
                    break
        
        # Check for errors
        high_error_test = next((r for r in results if r.error_rate > 5), None)
        if high_error_test:
            f.write(f"\n❌ High error rate ({high_error_test.error_rate:.1f}%) detected at {high_error_test.concurrent_users} users.\n")
            f.write(f"   Recommended maximum: {high_error_test.concurrent_users - 50} concurrent users\n")
        
        f.write("\n")
    
    return str(filename)


def create_visualization(results: List[LoadTestResults], results_dir: Path) -> str:
    """Create visualization charts (requires matplotlib)."""
    try:
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("matplotlib not installed. Skipping visualization.")
        return None
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = results_dir / f"chart_{timestamp}.png"
    
    users = [r.concurrent_users for r in results]
    avg_times = [r.avg_response_time * 1000 for r in results]
    p95_times = [r.p95_response_time * 1000 for r in results]
    error_rates = [r.error_rate for r in results]
    
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10))
    
    # Response times
    ax1.plot(users, avg_times, marker='o', label='Average', linewidth=2)
    ax1.plot(users, p95_times, marker='s', label='95th Percentile', linewidth=2)
    ax1.axhline(y=2000, color='g', linestyle='--', label='2s threshold (excellent)', alpha=0.5)
    ax1.axhline(y=5000, color='orange', linestyle='--', label='5s threshold (acceptable)', alpha=0.5)
    ax1.set_xlabel('Concurrent Users', fontsize=12)
    ax1.set_ylabel('Response Time (ms)', fontsize=12)
    ax1.set_title('Response Time vs Concurrent Users', fontsize=14, fontweight='bold')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    
    # Error rates
    ax2.bar(users, error_rates, color=['green' if e < 1 else 'orange' if e < 5 else 'red' for e in error_rates])
    ax2.axhline(y=1, color='orange', linestyle='--', label='1% threshold', alpha=0.5)
    ax2.axhline(y=5, color='red', linestyle='--', label='5% threshold', alpha=0.5)
    ax2.set_xlabel('Concurrent Users', fontsize=12)
    ax2.set_ylabel('Error Rate (%)', fontsize=12)
    ax2.set_title('Error Rate vs Concurrent Users', fontsize=14, fontweight='bold')
    ax2.legend()
    ax2.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig(filename, dpi=150, bbox_inches='tight')
    plt.close()
    
    return str(filename)


async def run_tests(
    config: dict,
    user_levels: List[int] = None,
    quick_test: bool = False
) -> List[LoadTestResults]:
    """Run stress tests with given configuration."""
    tester = ChatbotStressTester(
        api_url=config['api_url'],
        tenant_id=config['tenant_id'],
        api_key=config['api_key'],
        request_timeout=config.get('request_timeout', 60),
        delay_between_messages=config.get('delay_between_messages', 1.5)
    )
    
    if user_levels is None:
        user_levels = config.get('concurrent_users', [10, 50, 100, 200, 300])
    
    messages_per_user = 3 if quick_test else config.get('messages_per_user', 6)
    delay_between_tests = 5 if quick_test else config.get('delay_between_tests', 10)
    
    all_results = []
    
    for i, concurrent_users in enumerate(user_levels):
        print(f"\n[Test {i+1}/{len(user_levels)}]")
        results = await tester.run_load_test(concurrent_users, messages_per_user)
        tester.print_results(results)
        all_results.append(results)
        
        # Wait between tests (except after last one)
        if i < len(user_levels) - 1:
            print(f"\n⏳ Waiting {delay_between_tests}s before next test...")
            await asyncio.sleep(delay_between_tests)
    
    return all_results


def main():
    """Main entry point with CLI arguments."""
    parser = argparse.ArgumentParser(
        description='Stress test the chatbot backend and AWS Bedrock API',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run full test suite with default levels (10, 50, 100, 200, 300 users)
  python stress_test_runner.py
  
  # Quick smoke test with 5 users
  python stress_test_runner.py --users 5 --quick-test
  
  # Test specific load levels
  python stress_test_runner.py --users 25 50 75
  
  # Custom config file
  python stress_test_runner.py --config my_config.json
        """
    )
    
    parser.add_argument(
        '--config',
        default='test_config.json',
        help='Path to configuration file (default: test_config.json)'
    )
    
    parser.add_argument(
        '--users',
        type=int,
        nargs='+',
        help='Concurrent user levels to test (e.g., --users 10 50 100)'
    )
    
    parser.add_argument(
        '--quick-test',
        action='store_true',
        help='Run quick test with fewer messages and shorter delays'
    )
    
    parser.add_argument(
        '--no-viz',
        action='store_true',
        help='Skip visualization generation'
    )
    
    args = parser.parse_args()
    
    # Load configuration
    try:
        with open(args.config, 'r') as f:
            config = json.load(f)
    except FileNotFoundError:
        print(f"❌ Error: Configuration file '{args.config}' not found!")
        print("\nPlease create a configuration file with the following structure:")
        print(json.dumps({
            "api_url": "https://your-api-url.com/prod",
            "tenant_id": "your-tenant-id",
            "api_key": "your-api-key",
            "concurrent_users": [10, 50, 100, 200, 300],
            "messages_per_user": 6,
            "request_timeout": 60
        }, indent=2))
        sys.exit(1)
    
    # Validate configuration
    if config.get('tenant_id') == 'your-tenant-id-here' or config.get('api_key') == 'your-api-key-here':
        print("❌ Error: Please update your configuration file with actual credentials!")
        sys.exit(1)
    
    print("🚀 Chatbot Stress Test Runner")
    print("=" * 60)
    print(f"API URL: {config['api_url']}")
    print(f"Tenant ID: {config['tenant_id']}")
    print(f"Config: {args.config}")
    
    if args.quick_test:
        print("⚡ Quick test mode enabled")
    
    # Run tests
    results = asyncio.run(run_tests(config, args.users, args.quick_test))
    
    # Create results directory
    results_dir = create_results_directory()
    
    # Save results
    print("\n📊 Saving results...")
    json_file = save_results_json(results, results_dir)
    print(f"✅ JSON results: {json_file}")
    
    summary_file = save_results_summary(results, results_dir)
    print(f"✅ Summary report: {summary_file}")
    
    if not args.no_viz:
        viz_file = create_visualization(results, results_dir)
        if viz_file:
            print(f"✅ Visualization: {viz_file}")
    
    print(f"\n📁 All results saved to: {results_dir}/")
    
    # Print final summary
    print("\n" + "=" * 80)
    print("QUICK SUMMARY")
    print("=" * 80)
    print(f"\n{'Users':<8} {'Success%':<10} {'Error%':<10} {'Avg(ms)':<10} {'p95(ms)':<10}")
    print("-" * 50)
    
    for result in results:
        success_rate = 100 - result.error_rate
        status = "✅" if result.error_rate < 1 else "⚠️" if result.error_rate < 5 else "❌"
        print(
            f"{status} {result.concurrent_users:<6} "
            f"{success_rate:<10.1f} "
            f"{result.error_rate:<10.1f} "
            f"{result.avg_response_time*1000:<10.0f} "
            f"{result.p95_response_time*1000:<10.0f}"
        )
    
    print()


if __name__ == "__main__":
    main()
