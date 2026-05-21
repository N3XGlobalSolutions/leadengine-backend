"""Quick validation script to test the stress test framework setup."""

import sys
from pathlib import Path

def check_dependencies():
    """Check if required dependencies are available."""
    missing = []
    
    try:
        import aiohttp
        print("✅ aiohttp installed")
    except ImportError:
        missing.append("aiohttp")
        print("❌ aiohttp not installed")
    
    try:
        import numpy
        print("✅ numpy installed")
    except ImportError:
        missing.append("numpy")
        print("❌ numpy not installed")
    
    try:
        import matplotlib
        print("✅ matplotlib installed (visualization available)")
    except ImportError:
        print("⚠️  matplotlib not installed (visualization will be skipped)")
    
    try:
        import tabulate
        print("✅ tabulate installed")
    except ImportError:
        missing.append("tabulate")
        print("❌ tabulate not installed")
    
    return missing


def check_files():
    """Check if required files exist."""
    files = [
        "stress_test.py",
        "stress_test_runner.py",
        "conversation_scenarios.py",
        "test_config.json",
        "requirements-stress-test.txt",
        "STRESS_TEST_README.md"
    ]
    
    all_exist = True
    for file in files:
        if Path(file).exists():
            print(f"✅ {file} exists")
        else:
            print(f"❌ {file} missing")
            all_exist = False
    
    return all_exist


def check_config():
    """Check if config is properly set."""
    import json
    
    try:
        with open('test_config.json', 'r') as f:
            config = json.load(f)
        
        print("\n📋 Configuration:")
        print(f"   API URL: {config.get('api_url', 'NOT SET')}")
        print(f"   Tenant ID: {config.get('tenant_id', 'NOT SET')}")
        print(f"   API Key: {'SET' if config.get('api_key') and config['api_key'] != 'your-api-key-here' else 'NOT SET'}")
        print(f"   Load Levels: {config.get('concurrent_users', 'NOT SET')}")
        
        if config.get('tenant_id') == 'your-tenant-id-here':
            print("\n⚠️  WARNING: Please update tenant_id in test_config.json")
            return False
        
        if config.get('api_key') == 'your-api-key-here':
            print("\n⚠️  WARNING: Please update api_key in test_config.json")
            return False
        
        print("\n✅ Configuration looks good!")
        return True
        
    except Exception as e:
        print(f"\n❌ Error reading config: {e}")
        return False


def main():
    """Run all checks."""
    print("="*60)
    print("Stress Test Framework Validation")
    print("="*60)
    
    print("\n1. Checking Files...")
    print("-"*60)
    files_ok = check_files()
    
    print("\n2. Checking Dependencies...")
    print("-"*60)
    missing = check_dependencies()
    
    print("\n3. Checking Configuration...")
    print("-"*60)
    config_ok = check_config()
    
    print("\n" + "="*60)
    print("Summary")
    print("="*60)
    
    if files_ok and not missing and config_ok:
        print("\n✅ All checks passed! You're ready to run stress tests.")
        print("\nNext steps:")
        print("  1. Review test_config.json one more time")
        print("  2. Run a quick test: python stress_test_runner.py --users 5 --quick-test")
        print("  3. Run full suite: python stress_test_runner.py")
        return 0
    else:
        print("\n❌ Some issues found:")
        
        if not files_ok:
            print("  - Some required files are missing")
        
        if missing:
            print(f"  - Missing dependencies: {', '.join(missing)}")
            print(f"    Install with: pip install {' '.join(missing)}")
        
        if not config_ok:
            print("  - Configuration needs to be updated")
        
        return 1


if __name__ == "__main__":
    sys.exit(main())
