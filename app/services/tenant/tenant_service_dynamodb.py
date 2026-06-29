"""Tenant management service using DynamoDB.

This is the DynamoDB implementation of TenantService with in-memory caching.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional
import secrets
from app.core.redis_client import redis_client
from app.utils.Origin_utils import generate_allowed_origins
from app.core import config
from app.core.dynamodb import (
    get_item,
    put_item,
    update_item,
    delete_item,
    query_items,
    get_dynamodb_table,
)
from app.models.TenantSummary import TenantSummary
from app.models.TenantSummaryPaginationResponse import TenantSummaryPaginationResponse
from app.models.tenant import Tenant, TenantConfig, TenantCreate, TenantUpdate, TenantStatus
from app.utils.encryption import encrypt_secret, is_encrypted


def _strip_none_values(obj):
    """Recursively remove None (null) values from nested dicts before storing to DynamoDB.

    DynamoDB Map attributes can behave unexpectedly with Python None in some SDK
    configurations. Stripping them ensures Pydantic's Optional fields just default
    back to None when the key is absent on the next read.
    """
    if isinstance(obj, dict):
        return {k: _strip_none_values(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_strip_none_values(i) for i in obj]
    return obj


class TenantServiceDynamoDB:
    """DynamoDB-based tenant service with caching.

    Access Patterns:
    - Get tenant by ID:      PK=TENANT#<id>, SK=PROFILE
    - Validate API key:      GSI1PK=APIKEY#<api_key>
    - List all tenants:      Scan with begins_with(PK, 'TENANT#')

    Caching:
    - 5-minute TTL in-memory cache for high-frequency reads
    - Invalidated on updates
    """

    # In-memory cache: {tenant_id: (tenant, expiry_timestamp)}
    _cache = {}
    CACHE_TTL_SECONDS = 300  # 5 minutes

    def create_tenant(self, tenant_data: TenantCreate) -> Tenant:
        """Creates a new tenant."""
        tenant_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()

        config = tenant_data.config or TenantConfig()
        if config.whatsapp_access_token and not is_encrypted(config.whatsapp_access_token):
            config.whatsapp_access_token = encrypt_secret(config.whatsapp_access_token)

        
        # Default status to Onboarding if not specified
        status = tenant_data.status or TenantStatus.ONBOARDING
        # Sync is_active
        is_active = status != TenantStatus.INACTIVE

        if config.client_domain:
            config.allowed_origins = generate_allowed_origins(
                config.client_domain
            )

        tenant_item = {
            # Primary keys
            "PK": f"TENANT#{tenant_id}",
            "SK": "PROFILE",
            # GSI1: API key lookup (only if api_key exists)
            # 'GSI1PK': f'APIKEY#{config.api_key}',  # Added when API key generated
            # 'GSI1SK': f'TENANT#{tenant_id}',
            # Tenant data
            "tenant_id": tenant_id,
            "name": tenant_data.name,
            "config": config.model_dump(),
            "created_at": timestamp,
            "status": status.value,
            "is_active": is_active,
            "is_deleted": False,
            "created_by": tenant_data.created_by,
        }

        # Only add API key GSI fields if api_key exists
        if config.api_key:
            tenant_item["GSI1PK"] = f"APIKEY#{config.api_key}"
            tenant_item["GSI1SK"] = f"TENANT#{tenant_id}"

        put_item(tenant_item)

        keys = redis_client.keys("tenant_summaries:*")

        if keys:
            redis_client.delete(*keys)

        print("TENANT CACHE CLEARED")
        #Update analytics counters
        try:
            from app.services import analytics_service, USE_DYNAMODB
            if USE_DYNAMODB:
                analytics_service.increment_platform_counter("total_tenants")
        except Exception as e:
            print(f"Warning: Failed to update platform analytics: {e}")

        return Tenant(
            tenant_id=tenant_id,
            name=tenant_data.name,
            config=config,
            created_at=datetime.fromisoformat(timestamp),
            status=status,
            is_active=is_active,
            is_deleted=False,
            created_by=tenant_data.created_by,
        )

    def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        """Retrieves a tenant by ID (excludes soft-deleted tenants)."""
        item = get_item(f"TENANT#{tenant_id}", "PROFILE")

        if item and not item.get("is_deleted", False):
            created_at = item.get("created_at")
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)

            deleted_at = item.get("deleted_at")
            if isinstance(deleted_at, str):
                deleted_at = datetime.fromisoformat(deleted_at)

            # Parse config
            config_data = item.get("config", {})
            
            # Data Migration: Fix legacy qualification_questions
            if "qualification_questions" in config_data:
                for q in config_data["qualification_questions"]:
                    if "question" in q and "instruction" not in q:
                        q["instruction"] = q["question"]
                        q["label"] = q.get("label", "Question") # Default
            
            config = TenantConfig(**config_data)

            # Determine Status (Migration Logic)
            # If 'status' exists, use it. Else derive from 'is_active'.
            status_str = item.get("status")
            is_active = item.get("is_active", True)
            
            if status_str:
                try:
                    status = TenantStatus(status_str)
                except ValueError:
                    # Fallback if invalid status string
                    status = TenantStatus.ACTIVE if is_active else TenantStatus.INACTIVE
            else:
                # Backend migration: No status field yet
                status = TenantStatus.ACTIVE if is_active else TenantStatus.INACTIVE

            return Tenant(
                tenant_id=item.get("tenant_id") or item["PK"].replace("TENANT#", ""),
                name=item["name"],
                config=config,
                created_at=created_at,
                status=status,
                is_active=is_active,
                is_deleted=item.get("is_deleted", False),
                deleted_at=deleted_at,
                created_by=item.get("created_by"),
            )
        return None

    def get_tenant_cached(self, tenant_id: str) -> Optional[Tenant]:
        """
        Retrieves a tenant by ID with caching (5-minute TTL).

        Use this for high-frequency reads like chat messages.
        Excludes soft-deleted tenants.
        """
        # Check cache first
        cached = self._cache.get(tenant_id)
        if cached:
            tenant, expiry = cached
            if datetime.now(timezone.utc) < expiry:
                return tenant

        # Cache miss or expired - fetch from DB
        tenant = self.get_tenant(tenant_id)
        if tenant:
            expiry = datetime.now(timezone.utc) + timedelta(seconds=self.CACHE_TTL_SECONDS)
            self._cache[tenant_id] = (tenant, expiry)

        return tenant

    def invalidate_cache(self, tenant_id: str):

        """Invalidates Redis cache for tenant."""

        # Clear single tenant cache
        redis_client.delete(f"tenant:{tenant_id}")

        # Clear tenant list cache
        tenant_keys = redis_client.keys("list_tenants:*")

        if tenant_keys:
            redis_client.delete(*tenant_keys)

        # Clear tenant summary cache
        summary_keys = redis_client.keys("tenant_summaries:*")

        if summary_keys:
            redis_client.delete(*summary_keys)

        print(f"CACHE INVALIDATED FOR TENANT: {tenant_id}")

    def list_tenant_summaries(self, skip: int = 0, limit: int = 100):

        cache_key = f"tenant_summaries:{skip}:{limit}"

        cached_data = redis_client.get(cache_key)

        if cached_data:
            print("CACHE HIT")

            cached_tenants = json.loads(cached_data)

            return [TenantSummary(**tenant) for tenant in cached_tenants]

        print("CACHE MISS")

        table = get_dynamodb_table()

        tenants = []
        last_evaluated_key = None

        while True:
            scan_kwargs = {
                "FilterExpression": "begins_with(PK, :pk) AND SK = :sk",
                "ExpressionAttributeValues": {
                    ":pk": "TENANT#",
                    ":sk": "PROFILE",
                },
            }

            if last_evaluated_key:
                scan_kwargs["ExclusiveStartKey"] = last_evaluated_key

            response = table.scan(**scan_kwargs)

            items = response.get("Items", [])
            last_evaluated_key = response.get("LastEvaluatedKey")

            for item in items:
                if item.get("is_deleted", False):
                    continue

                config = item.get("config", {})

                tenants.append(
                    TenantSummary(
                        id=item.get("tenant_id"),
                        name=item.get("name"),
                        onboardedDate=item.get("created_at"),
                        plan=config.get("plan"),
                        slug=config.get("url_slug"),
                        status=item.get("status"),
                        totalUsers=item.get("totalUsers", 0),
                    )
                )

            if not last_evaluated_key:
                break

        paginated_tenants = tenants[skip: skip + limit]

        # ✅ STORE IN REDIS
        redis_client.setex(
            cache_key,
            300,
            json.dumps([t.model_dump() for t in paginated_tenants])
        )

        return paginated_tenants


    from typing import Optional, Dict, Any

    def tenant_summaries(
            self,
            limit: int = 20,
            cursor: Optional[Dict[str, Any]] = None
    ) -> TenantSummaryPaginationResponse:


        table = get_dynamodb_table()

        scan_kwargs = {
            "FilterExpression": "begins_with(PK, :pk) AND SK = :sk",
            "ExpressionAttributeValues": {
                ":pk": "TENANT#",
                ":sk": "PROFILE",
            },
            "Limit": limit
        }

        # Cursor support
        if cursor:
            scan_kwargs["ExclusiveStartKey"] = cursor

        response = table.scan(**scan_kwargs)

        items = response.get("Items", [])

        tenants = []

        for item in items:

            if item.get("is_deleted", False):
                continue

            config = item.get("config", {})

            tenants.append(
                TenantSummary(
                    id=item.get("tenant_id"),
                    name=item.get("name"),
                    onboardedDate=item.get("created_at"),
                    plan=config.get("plan"),
                    slug=config.get("url_slug"),
                    status=item.get("status"),
                    totalUsers=item.get("totalUsers", 0),
                )
            )

        next_cursor = response.get("LastEvaluatedKey")

        return TenantSummaryPaginationResponse(
            items=tenants,
            nextCursor=next_cursor
        )

    def get_raw_tenant(self, tenant_id: str) -> Optional[dict]:
        """Return the raw DynamoDB item for a tenant regardless of is_deleted status.

        Used by platform admins to diagnose and restore missing tenants.
        """
        from app.core.dynamodb import get_item as _get_item
        return _get_item(f"TENANT#{tenant_id}", "PROFILE")

    def restore_tenant(self, tenant_id: str) -> bool:
        """Restore a soft-deleted tenant by clearing the is_deleted flag.

        Returns True if the tenant existed and was restored, False otherwise.
        """
        raw = self.get_raw_tenant(tenant_id)
        if not raw:
            return False

        update_item(
            f"TENANT#{tenant_id}",
            "PROFILE",
            {"is_deleted": False, "deleted_at": None, "status": TenantStatus.ACTIVE.value, "is_active": True},
        )
        self.invalidate_cache(tenant_id)
        return True

    def update_tenant(
        self, tenant_id: str, update_data: TenantUpdate
    ) -> Optional[Tenant]:
        """Updates tenant information and invalidates cache."""
        # Use mode='json' to ensure Enums are serialized to strings/values, which DynamoDB handles
        update_dict = update_data.model_dump(exclude_unset=True, mode='json')
        if not update_dict:
            return self.get_tenant(tenant_id)

        # Handle config update:
        # Since the frontend sends the COMPLETE config object every time in updateClient,
        # we should simply replace the 'config' attribute in DynamoDB rather than
        # trying to patch individual nested fields.
        
        if "config" in update_dict:
            # Check if we need to merge with existing manual fields like api_key
            current_tenant = self.get_tenant(tenant_id)
            if current_tenant and current_tenant.config:
                # Use mode='json' for current config too
                current_config = current_tenant.config.model_dump(mode='json')
                
                # Get new config data
                new_config_val = update_dict["config"]
                # It's already a dict if we used mode='json' above, but to be safe/consistent:
                if hasattr(new_config_val, "model_dump"):
                    new_config_dict = new_config_val.model_dump(
                        exclude_unset=True,
                        mode='json'
                    )
                else:
                    new_config_dict = new_config_val

                # Auto-generate allowed origins from client domain
                client_domain = new_config_dict.get("client_domain")

                if client_domain:
                    generated_origins = generate_allowed_origins(client_domain)

                    existing_origins = [
                        origin.lower().rstrip("/")
                        for origin in new_config_dict.get(
                            "allowed_origins",
                            []
                        )
                    ]

                    new_config_dict["allowed_origins"] = list(
                        set(existing_origins + generated_origins)
                    )
                
                # Preserve api_key
                if "api_key" in current_config and "api_key" not in new_config_dict:
                    new_config_dict["api_key"] = current_config["api_key"]

                    # Revalidate config after modifications
                validated_config = TenantConfig(**new_config_dict)

                if validated_config.whatsapp_access_token and not is_encrypted(validated_config.whatsapp_access_token):
                    validated_config.whatsapp_access_token = encrypt_secret(validated_config.whatsapp_access_token)

                new_config_dict = validated_config.model_dump(
                        mode="json"
                    )

                # Strip None values from nested config so DynamoDB never
                # receives Python None inside Map attributes (optional fields
                # like `table` default back to None on read when the key is absent).
                new_config_dict = _strip_none_values(new_config_dict)

                update_dict["config"] = new_config_dict

        # Handle Status Update logic
        # 1. If 'status' is updating, sync 'is_active'
        # 2. If 'is_active' is updating (old code), sync 'status' if 'status' not in update
        if "status" in update_dict:
             # Sync is_active
             # status is already a string here due to mode='json'
             update_dict["is_active"] = update_dict["status"] != TenantStatus.INACTIVE.value
        elif "is_active" in update_dict:
            # Legacy update - derive status
            is_active = update_dict["is_active"]
            # To avoid overwriting a more specific status like 'Paused' or 'Onboarding' 
            # with generic 'Active', we should only flip it if it contradicts.
            # But simpler: True -> Active, False -> Inactive. 
            # Ideally frontend sends 'status'.
            update_dict["status"] = TenantStatus.ACTIVE.value if is_active else TenantStatus.INACTIVE.value

        update_item(f"TENANT#{tenant_id}", "PROFILE", update_dict)

        # Invalidate cache after update
        self.invalidate_cache(tenant_id)

        return self.get_tenant(tenant_id)

    def delete_tenant(self, tenant_id: str) -> bool:
        """Soft deletes a tenant (marks as deleted with timestamp)."""
        result = update_item(
            f"TENANT#{tenant_id}",
            "PROFILE",
            {"is_deleted": True, "deleted_at": datetime.now(timezone.utc).isoformat()},
        )


        # Decrement platform counter
        if result is not None:
            # Invalidate cache
            self.invalidate_cache(tenant_id)

            try:
                from app.services import analytics_service, USE_DYNAMODB
                if USE_DYNAMODB:
                    analytics_service.increment_platform_counter("total_tenants", amount=-1)
            except Exception as e:
                print(f"Warning: Failed to update platform analytics: {e}")

        return result is not None

    def generate_api_key(self, tenant_id: str) -> str:
        """Generate a new API key for a tenant."""
        api_key = f"aob_{secrets.token_urlsafe(32)}"

        # Get current tenant to update GSI fields
        table = get_dynamodb_table()

        # Update with API key and GSI fields
        table.update_item(
            Key={"PK": f"TENANT#{tenant_id}", "SK": "PROFILE"},
            UpdateExpression="SET #config.#apikey = :key, #gsi1pk = :gsi1pk, #gsi1sk = :gsi1sk",
            ExpressionAttributeNames={
                "#config": "config",
                "#apikey": "api_key",
                "#gsi1pk": "GSI1PK",
                "#gsi1sk": "GSI1SK",
            },
            ExpressionAttributeValues={
                ":key": api_key,
                ":gsi1pk": f"APIKEY#{api_key}",
                ":gsi1sk": f"TENANT#{tenant_id}",
            },
        )

        # Invalidate cache
        self.invalidate_cache(tenant_id)

        return api_key

    def validate_api_key(self, api_key: str) -> Optional[str]:
        """Validate an API key and return the tenant_id if valid."""
        items = query_items(pk=f"APIKEY#{api_key}", index_name="GSI1", limit=1)

        if items and not items[0].get("is_deleted", False):
            return items[0].get("tenant_id")
        return None


# Export singleton instance
tenant_service_dynamodb = TenantServiceDynamoDB()
