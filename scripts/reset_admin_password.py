"""Reset admin password directly in DynamoDB.

Usage:
    python scripts/reset_admin_password.py

This resets the password for admin@qbot.com (Platform Admin).
"""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.dynamodb import update_item
from app.utils.security import get_password_hash

# ─── CONFIG ───────────────────────────────────────────
TARGET_USER_ID = "52b984ad-c74c-4d7e-ae75-1c8ccf439bd9"  # admin@qbot.com
TARGET_EMAIL   = "admin@qbot.com"
NEW_PASSWORD   = "Admin@123456"   # ← change this to whatever you want
# ──────────────────────────────────────────────────────

def reset():
    print(f"Resetting password for: {TARGET_EMAIL}")
    hashed = get_password_hash(NEW_PASSWORD)
    result = update_item(
        pk=f"USER#{TARGET_USER_ID}",
        sk="PROFILE",
        updates={"hashed_password": hashed},
    )
    if result is not None:
        print(f"✅ Password reset successfully.")
        print(f"   Email   : {TARGET_EMAIL}")
        print(f"   Password: {NEW_PASSWORD}")
    else:
        print("❌ Failed to update — check DynamoDB connection / credentials.")

if __name__ == "__main__":
    reset()
