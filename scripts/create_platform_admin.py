"""Create a new platform_admin user directly in DynamoDB.

Usage:
    python scripts/create_platform_admin.py
"""

import sys
import os
import uuid
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.dynamodb import get_dynamodb_table
from app.utils.security import get_password_hash

# ─── CREDENTIALS  (change these before running) ───────
NEW_EMAIL     = "superadmin@qbot.com"
NEW_PASSWORD  = "Qbot@SuperAdmin2025"
FULL_NAME     = "Super Admin"
# ──────────────────────────────────────────────────────

def create_admin():
    table = get_dynamodb_table()
    user_id   = str(uuid.uuid4())
    timestamp = datetime.utcnow().isoformat()

    item = {
        # Primary key
        "PK": f"USER#{user_id}",
        "SK": "PROFILE",
        # GSI1: email lookup
        "GSI1PK": f"EMAIL#{NEW_EMAIL}",
        "GSI1SK": f"USER#{user_id}",
        # GSI2: tenant users (platform admins have no tenant)
        "GSI2PK": "TENANT#NONE",
        "GSI2SK": f"USER#{timestamp}",
        # User data
        "user_id":         user_id,
        "email":           NEW_EMAIL,
        "full_name":       FULL_NAME,
        "role":            "platform_admin",
        "tenant_id":       None,
        "hashed_password": get_password_hash(NEW_PASSWORD),
        "created_at":      timestamp,
        "is_active":       True,
        "is_deleted":      False,
    }

    table.put_item(Item=item)

    print("\n✅ New platform_admin created successfully!")
    print(f"   Email   : {NEW_EMAIL}")
    print(f"   Password: {NEW_PASSWORD}")
    print(f"   User ID : {user_id}")
    print(f"   Role    : platform_admin\n")

if __name__ == "__main__":
    create_admin()
