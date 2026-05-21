"""User management service using DynamoDB.

This is the DynamoDB implementation of UserService using single-table design.
"""

import uuid
from datetime import datetime
from typing import List, Optional
from decimal import Decimal

from app.core.dynamodb import (
    get_item,
    put_item,
    update_item,
    delete_item,
    query_items,
    get_dynamodb_table,
)
from app.models.user import User, UserCreate, UserResponse, UserRole, UserUpdate
from app.utils.security import get_password_hash

# Map legacy / renamed roles stored in DB to current valid UserRole values
_LEGACY_ROLE_MAP = {
    "aob_admin":         "platform_admin",
    "admin":             "platform_admin",
    "superadmin":        "platform_admin",
    "super_admin":       "platform_admin",
    "operator":          "platform_operator",
    "platform_operator": "platform_operator",
    "client_admin":      "client_admin",
    "sales_team":        "sales_team",
    "sales":             "sales_team",
}


def _normalize_role(raw_role: str) -> UserRole:
    """Return the nearest valid UserRole enum for any legacy role string."""
    if not raw_role:
        return UserRole.CLIENT_ADMIN
    normalized = _LEGACY_ROLE_MAP.get(raw_role.lower())
    if normalized:
        return UserRole(normalized)
    # If it's already a valid enum value, keep it
    try:
        return UserRole(raw_role)
    except ValueError:
        pass
    # Default fallback
    return UserRole.CLIENT_ADMIN


class UserServiceDynamoDB:
    """DynamoDB-based user service with optimized access patterns.

    Access Patterns:
    - Get user by ID:    PK=USER#<id>, SK=PROFILE
    - Get user by email: GSI1PK=EMAIL#<email>
    - List tenant users: GSI2PK=TENANT#<tenant_id>
    """

    def create_user(self, user_data: UserCreate) -> UserResponse:
        """Create a new user.

        Checks for existing email via GSI1 lookup before creating.
        """
        # Check if user exists (GSI1 lookup by email)
        existing = query_items(
            pk=f"EMAIL #{user_data.email}", index_name="GSI1", limit=1
        )
        if existing:
            raise ValueError("User with this email already exists")

        # Create user
        user_id = str(uuid.uuid4())
        timestamp = datetime.utcnow().isoformat()

        user_item = {
            # Primary keys
            "PK": f"USER#{user_id}",
            "SK": "PROFILE",
            # GSI1: Email lookup
            "GSI1PK": f"EMAIL#{user_data.email}",
            "GSI1SK": f"USER#{user_id}",
            # GSI2: Tenant users list
            "GSI2PK": f"TENANT#{user_data.tenant_id}"
            if user_data.tenant_id
            else "TENANT#NONE",
            "GSI2SK": f"USER#{timestamp}",
            # User data
            "user_id": user_id,
            "email": user_data.email,
            "full_name": user_data.full_name,
            "role": user_data.role.value,
            "tenant_id": user_data.tenant_id,
            "hashed_password": get_password_hash(user_data.password),
            "created_at": timestamp,
            "is_active": True,
            "is_deleted": False,
        }

        put_item(user_item)

        # Update analytics counters
        try:
            from app.services import analytics_service, USE_DYNAMODB
            if USE_DYNAMODB:
                analytics_service.increment_platform_counter("total_users")
        except Exception as e:
            print(f"Warning: Failed to update platform analytics: {e}")

        # Return without password
        return UserResponse(
            user_id=user_id,
            email=user_data.email,
            full_name=user_data.full_name,
            role=user_data.role,
            tenant_id=user_data.tenant_id,
            created_at=datetime.fromisoformat(timestamp),
            is_active=True,
        )

    def get_user(self, user_id: str) -> Optional[UserResponse]:
        """Get user by ID (excludes soft-deleted users)."""
        item = get_item(f"USER#{user_id}", "PROFILE")

        if item and not item.get("is_deleted", False):
            # Convert ISO string back to datetime for response
            created_at = item.get("created_at")
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)

            last_login = item.get("last_login")
            if isinstance(last_login, str):
                last_login = datetime.fromisoformat(last_login)

            return UserResponse(
                user_id=item["user_id"],
                email=item["email"],
                full_name=item["full_name"],
                role=_normalize_role(item.get("role", "")),
                tenant_id=item.get("tenant_id"),
                created_at=created_at,
                is_active=item.get("is_active", True),
                last_login=last_login,
            )
        return None

    def get_user_by_email(self, email: str) -> Optional[User]:
        """Get user by email (returns full User with password for auth).

        Excludes soft-deleted users.
        """
        items = query_items(pk=f"EMAIL#{email}", index_name="GSI1", limit=1)

        if items and not items[0].get("is_deleted", False):
            item = items[0]

            # Convert timestamps
            created_at = item.get("created_at")
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)

            last_login = item.get("last_login")
            if isinstance(last_login, str):
                last_login = datetime.fromisoformat(last_login)

            deleted_at = item.get("deleted_at")
            if isinstance(deleted_at, str):
                deleted_at = datetime.fromisoformat(deleted_at)

            return User(
                user_id=item["user_id"],
                email=item["email"],
                full_name=item["full_name"],
                role=_normalize_role(item.get("role", "")),
                tenant_id=item.get("tenant_id"),
                hashed_password=item["hashed_password"],
                created_at=created_at,
                is_active=item.get("is_active", True),
                is_deleted=item.get("is_deleted", False),
                deleted_at=deleted_at,
                last_login=last_login,
            )
        return None

    def list_users(
        self, tenant_id: Optional[str] = None, skip: int = 0, limit: int = 1000
    ) -> List[UserResponse]:
        """List users (excludes soft-deleted users).

        If tenant_id provided, queries GSI2 for tenant's users.
        Otherwise, scans for all users (platform admin use case).
        """
        if tenant_id:
            # Query GSI2 for tenant users (sorted by creation time)
            items = query_items(
                pk=f"TENANT#{tenant_id}", index_name="GSI2", limit=limit + skip
            )
        else:
            # Scan for all users (platform admin use case)
            # IMPORTANT: DynamoDB applies Limit BEFORE FilterExpression,
            # so we need to paginate to get all matching items
            table = get_dynamodb_table()
            
            items = []
            scan_params = {
                "FilterExpression": "begins_with(PK, :pk) AND SK = :sk",
                "ExpressionAttributeValues": {":pk": "USER#", ":sk": "PROFILE"},
            }
            
            # Paginate through all results
            while True:
                response = table.scan(**scan_params)
                items.extend(response.get("Items", []))
                
                # Check if there are more items to scan
                last_key = response.get("LastEvaluatedKey")
                if not last_key:
                    break
                    
                # Add ExclusiveStartKey for next page
                scan_params["ExclusiveStartKey"] = last_key

        # Filter soft-deleted users
        active_users = [item for item in items if not item.get("is_deleted", False)]

        # Apply pagination and convert to UserResponse
        users = []
        for item in active_users[skip : skip + limit]:
            created_at = item.get("created_at")
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)

            last_login = item.get("last_login")
            if isinstance(last_login, str):
                last_login = datetime.fromisoformat(last_login)

            users.append(
                UserResponse(
                    user_id=item["user_id"],
                    email=item["email"],
                    full_name=item["full_name"],
                    role=_normalize_role(item.get("role", "")),
                    tenant_id=item.get("tenant_id"),
                    created_at=created_at,
                    is_active=item.get("is_active", True),
                    last_login=last_login,
                )
            )

        return users

    def update_user(
        self, user_id: str, update_data: UserUpdate
    ) -> Optional[UserResponse]:
        """Update a user."""
        update_dict = {
            k: v for k, v in update_data.model_dump().items() if v is not None
        }

        if not update_dict:
            return self.get_user(user_id)

        # Convert enum to value if present
        if "role" in update_dict:
            update_dict["role"] = update_dict["role"].value

        updated = update_item(f"USER#{user_id}", "PROFILE", update_dict)

        if updated:
            return self.get_user(user_id)
        return None

    def delete_user(self, user_id: str) -> bool:
        """Soft delete a user (marks as deleted with timestamp)."""
        result = update_item(
            f"USER#{user_id}",
            "PROFILE",
            {"is_deleted": True, "deleted_at": datetime.utcnow().isoformat()},
        )
        # Decrement platform counter
        if result is not None:
            try:
                from app.services import analytics_service, USE_DYNAMODB
                if USE_DYNAMODB:
                    analytics_service.increment_platform_counter("total_users", amount=-1)
            except Exception as e:
                print(f"Warning: Failed to update platform analytics: {e}")

        return result is not None

    def update_last_login(self, user_id: str):
        """Update user's last login timestamp."""
        update_item(
            f"USER#{user_id}", "PROFILE", {"last_login": datetime.utcnow().isoformat()}
        )

    def reset_user_password(self, user_id: str, new_password: str) -> bool:
        """Reset a user's password (admin operation)."""
        hashed_password = get_password_hash(new_password)
        result = update_item(
            f"USER#{user_id}",
            "PROFILE",
            {"hashed_password": hashed_password}
        )
        return result is not None


# Export singleton instance
user_service_dynamodb = UserServiceDynamoDB()
