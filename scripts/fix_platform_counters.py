"""
One-time script to recalculate and fix platform counters.
Run this from the qbot-backend directory:
    python -m scripts.fix_platform_counters
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from app.core.dynamodb import get_dynamodb_table, put_item, get_item


def fix_platform_counters():
    """Count actual tenants and update the platform counter."""
    table = get_dynamodb_table()
    
    print("🔍 Counting actual tenants...")
    
    # Scan for all tenants
    tenants = []
    last_key = None
    
    while True:
        scan_kwargs = {
            "FilterExpression": "begins_with(PK, :pk) AND SK = :sk",
            "ExpressionAttributeValues": {":pk": "TENANT#", ":sk": "PROFILE"},
        }
        
        if last_key:
            scan_kwargs["ExclusiveStartKey"] = last_key
        
        response = table.scan(**scan_kwargs)
        
        for item in response.get("Items", []):
            # Only count non-deleted tenants
            if not item.get("is_deleted", False):
                tenants.append(item.get("name", item.get("tenant_id")))
        
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
    
    actual_count = len(tenants)
    print(f"✅ Found {actual_count} active tenants:")
    for t in tenants:
        print(f"   - {t}")
    
    # Get current platform stats - CORRECT KEY FORMAT
    print("\n📊 Checking current platform stats...")
    current_stats = get_item("STATS#PLATFORM", "COUNTERS")
    
    if current_stats:
        current_count = int(current_stats.get("total_tenants", 0))
        print(f"   Current counter value: {current_count}")
    else:
        print("   No platform stats record found, will create one.")
        current_count = 0
        current_stats = {}
    
    if current_count == actual_count:
        print(f"\n✨ Counter is already correct ({actual_count}). No fix needed!")
        return
    
    # Update the counter - CORRECT KEY FORMAT
    print(f"\n🔧 Updating counter from {current_count} to {actual_count}...")
    
    from datetime import datetime, timezone
    
    stats_item = {
        "PK": "STATS#PLATFORM",
        "SK": "COUNTERS",
        "total_tenants": actual_count,
        "total_users": int(current_stats.get("total_users", 0)),
        "total_conversations": int(current_stats.get("total_conversations", 0)),
        "total_leads": int(current_stats.get("total_leads", 0)),
        "last_updated": datetime.now(timezone.utc).isoformat()
    }
    
    put_item(stats_item)
    
    print(f"✅ Platform counter fixed! total_tenants = {actual_count}")


if __name__ == "__main__":
    fix_platform_counters()

