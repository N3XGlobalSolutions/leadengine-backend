"""Check user records in DynamoDB to diagnose the users list issue."""

import sys
import os
import json

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.dynamodb import get_dynamodb_table

def check_users():
    """Scan and print all user records in DynamoDB."""
    table = get_dynamodb_table()
    
    output = []
    output.append("\n=== SCANNING FOR ALL USER RECORDS ===\n")
    
    # Scan for all items that look like users
    response = table.scan()
    items = response.get('Items', [])
    
    user_items = []
    for item in items:
        pk = item.get('PK', '')
        sk = item.get('SK', '')
        
        # Look for anything that might be a user
        if 'USER' in pk.upper() or 'email' in item or 'hashed_password' in item:
            user_items.append(item)
            output.append(f"Found potential user record:")
            output.append(f"  PK: {pk}")
            output.append(f"  SK: {sk}")
            output.append(f"  Email: {item.get('email', 'N/A')}")
            output.append(f"  Full Name: {item.get('full_name', 'N/A')}")
            output.append(f"  Role: {item.get('role', 'N/A')}")
            output.append(f"  Is Deleted: {item.get('is_deleted', False)}")
            output.append(f"  Is Active: {item.get('is_active', True)}")
            output.append("")
    
    output.append(f"\n=== SUMMARY ===")
    output.append(f"Total items in table: {len(items)}")
    output.append(f"Potential user records found: {len(user_items)}")
    
    output.append("\n=== CHECKING USER# PREFIX WITH PROFILE SK ===\n")
    response = table.scan(
        FilterExpression="begins_with(PK, :pk) AND SK = :sk",
        ExpressionAttributeValues={
            ":pk": "USER#",
            ":sk": "PROFILE"
        }
    )
    profile_users = response.get('Items', [])
    output.append(f"Users with PK=USER#* and SK=PROFILE: {len(profile_users)}")
    for item in profile_users:
        output.append(f"  - {item.get('email')} ({item.get('full_name')})")
    
    # Print to console
    result = '\n'.join(output)
    print(result)
    
    # Save to file
    with open('user_check_results.txt', 'w') as f:
        f.write(result)
    
    print("\n\nResults saved to user_check_results.txt")

if __name__ == "__main__":
    check_users()
