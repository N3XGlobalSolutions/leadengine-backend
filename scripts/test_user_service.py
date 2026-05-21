"""Test user service to see what it returns."""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services import user_service

def test_user_service():
    """Test the user service list_users method."""
    
    print("\n=== TESTING USER SERVICE ===\n")
    
    # Test 1: List all users (platform admin use case)
    print("Test 1: List all users (tenant_id=None, like platform admin)")
    users = user_service.list_users(tenant_id=None, skip=0, limit=100)
    print(f"  Found {len(users)} users")
    if users:
        print("  First 5 users:")
        for user in users[:5]:
            print(f"    - {user.email} ({user.full_name}) - {user.role}")
    else:
        print("  ❌ NO USERS RETURNED!")
    
    print("\n" + "="*50 + "\n")
    
    # Test 2: Try with a specific tenant
    print("Test 2: List users for a specific tenant")
    # Let's try the Sky Habitat tenant from our scan
    test_tenant_id = "sky-habitat"
    users = user_service.list_users(tenant_id=test_tenant_id, skip=0, limit=100)
    print(f"  Found {len(users)} users for tenant '{test_tenant_id}'")
    if users:
        for user in users:
            print(f"    - {user.email} ({user.full_name})")
    
    print("\n" + "="*50 + "\n")
    
    # Test 3: Get a specific user by email
    print("Test 3: Get user by email")
    test_email = "admin@qbot.com"
    user = user_service.get_user_by_email(test_email)
    if user:
        print(f"  ✅ Found user: {user.email} ({user.full_name}) - {user.role}")
    else:
        print(f"  ❌ User {test_email} not found!")

if __name__ == "__main__":
    test_user_service()
