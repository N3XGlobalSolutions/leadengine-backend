"""Create a new sales_team user directly in DynamoDB for testing.

Usage:
    python scripts/create_sales_team.py
"""

import sys
import os
import uuid
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.dynamodb import get_dynamodb_table
from app.utils.security import get_password_hash

# ─── CREDENTIALS ───────
NEW_EMAIL     = "sales@qbot.com"
NEW_PASSWORD  = "Sales@123456"
FULL_NAME     = "Sales Team Member"
# ───────────────────────

def create_sales_team():
    table = get_dynamodb_table()
    user_id   = str(uuid.uuid4())
    timestamp = datetime.utcnow().isoformat()

    item = {
        "PK": f"USER#{user_id}",
        "SK": "PROFILE",
        "GSI1PK": f"EMAIL#{NEW_EMAIL}",
        "GSI1SK": f"USER#{user_id}",
        # Sales team is platform-level, so no tenant
        "GSI2PK": "TENANT#NONE",
        "GSI2SK": f"USER#{timestamp}",
        "user_id":         user_id,
        "email":           NEW_EMAIL,
        "full_name":       FULL_NAME,
        "role":            "sales_team",
        "tenant_id":       None,
        "hashed_password": get_password_hash(NEW_PASSWORD),
        "created_at":      timestamp,
        "is_active":       True,
        "is_deleted":      False,
    }

    table.put_item(Item=item)

    print("\n✅ New sales_team user created successfully!")
    print(f"   Email   : {NEW_EMAIL}")
    print(f"   Password: {NEW_PASSWORD}")
    print(f"   User ID : {user_id}")
    print(f"   Role    : sales_team\n")

if __name__ == "__main__":
    create_sales_team()
