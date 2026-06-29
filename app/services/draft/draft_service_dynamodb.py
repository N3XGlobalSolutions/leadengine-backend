"""Draft autosave service using DynamoDB.

Server-side draft persistence for preventing data loss during client creation
and client settings editing. Drafts are isolated per user and auto-expire
via DynamoDB TTL after 7 days.

DynamoDB Key Design (single-table):
    PK: DRAFT#{user_id}
    SK: TYPE#{draft_type}
    TTL attribute: expires_at (epoch seconds)
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.core.dynamodb import delete_item, get_item, put_item, update_item
from app.models.draft import DraftResponse

# Fields that must NEVER be persisted in a draft
SENSITIVE_FIELDS = frozenset({
    "whatsapp_access_token",
})

# Draft expiration: 7 days
DRAFT_TTL_DAYS = 7


def _strip_sensitive(data: dict) -> dict:
    """Recursively strip sensitive fields from draft data before persisting.

    This is a defense-in-depth measure — the frontend also strips these fields,
    but we enforce it server-side to guarantee secrets never reach the DB.
    """
    if not isinstance(data, dict):
        return data

    cleaned = {}
    for key, value in data.items():
        if key in SENSITIVE_FIELDS:
            continue
        if isinstance(value, dict):
            cleaned[key] = _strip_sensitive(value)
        elif isinstance(value, list):
            cleaned[key] = [
                _strip_sensitive(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            cleaned[key] = value
    return cleaned


def _make_pk(user_id: str) -> str:
    return f"DRAFT#{user_id}"


def _make_sk(draft_type: str) -> str:
    return f"TYPE#{draft_type}"


def _is_expired(expires_at) -> bool:
    """Application-level expiration check.

    DynamoDB TTL deletion is asynchronous and can lag by up to 48 hours.
    We must check expiration at read time to ensure expired drafts are
    never returned to the client.
    """
    try:
        # expires_at is stored as epoch seconds (int or Decimal)
        exp = int(expires_at)
        now = int(datetime.now(timezone.utc).timestamp())
        return exp < now
    except (TypeError, ValueError):
        return True


class DraftServiceDynamoDB:
    """DynamoDB-based draft autosave service.

    Access Patterns:
    - Create draft:     PUT  PK=DRAFT#{user_id}, SK=TYPE#{draft_type}
    - Update draft:     UPDATE PK/SK, refresh timestamps
    - Get latest draft: GET  PK/SK, filter expired
    - Delete draft:     DELETE PK/SK
    """

    def create_draft(
        self, user_id: str, draft_type: str, draft_data: dict
    ) -> DraftResponse:
        """Create a new draft for the given user.

        If a draft already exists for this user+type, it is overwritten.
        This is intentional — each user has at most one draft per type.
        """
        draft_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        timestamp = now.isoformat()
        expires_at = int((now + timedelta(days=DRAFT_TTL_DAYS)).timestamp())

        clean_data = _strip_sensitive(draft_data)

        item = {
            "PK": _make_pk(user_id),
            "SK": _make_sk(draft_type),
            "draft_id": draft_id,
            "user_id": user_id,
            "draft_type": draft_type,
            "draft_data": clean_data,
            "created_at": timestamp,
            "updated_at": timestamp,
            "expires_at": expires_at,
        }

        put_item(item)

        return DraftResponse(
            draft_id=draft_id,
            user_id=user_id,
            draft_type=draft_type,
            draft_data=clean_data,
            created_at=timestamp,
            updated_at=timestamp,
            expires_at=expires_at,
        )

    def update_draft(
        self, user_id: str, draft_type: str, draft_data: dict
    ) -> Optional[DraftResponse]:
        """Update an existing draft's data and refresh expiration.

        Returns None if no draft exists for this user+type.
        """
        pk = _make_pk(user_id)
        sk = _make_sk(draft_type)

        # Check existence first
        existing = get_item(pk, sk)
        if not existing or _is_expired(existing.get("expires_at", 0)):
            return None

        now = datetime.now(timezone.utc)
        timestamp = now.isoformat()
        expires_at = int((now + timedelta(days=DRAFT_TTL_DAYS)).timestamp())

        clean_data = _strip_sensitive(draft_data)

        updated = update_item(pk, sk, {
            "draft_data": clean_data,
            "updated_at": timestamp,
            "expires_at": expires_at,
        })

        if updated is None:
            return None

        return DraftResponse(
            draft_id=existing["draft_id"],
            user_id=user_id,
            draft_type=draft_type,
            draft_data=clean_data,
            created_at=existing.get("created_at", timestamp),
            updated_at=timestamp,
            expires_at=expires_at,
        )

    def get_latest_draft(
        self, user_id: str, draft_type: str
    ) -> Optional[DraftResponse]:
        """Retrieve the user's draft if it exists and has not expired.

        Returns None if:
        - No draft exists
        - The draft has expired (application-level check)
        """
        pk = _make_pk(user_id)
        sk = _make_sk(draft_type)

        item = get_item(pk, sk)
        if not item:
            return None

        # Application-level expiration check
        if _is_expired(item.get("expires_at", 0)):
            return None

        return DraftResponse(
            draft_id=item["draft_id"],
            user_id=item["user_id"],
            draft_type=item["draft_type"],
            draft_data=item.get("draft_data", {}),
            created_at=item.get("created_at", ""),
            updated_at=item.get("updated_at", ""),
            expires_at=int(item.get("expires_at", 0)),
        )

    def delete_draft(self, user_id: str, draft_type: str) -> bool:
        """Delete a draft. Returns True if deletion succeeded."""
        pk = _make_pk(user_id)
        sk = _make_sk(draft_type)
        return delete_item(pk, sk)


# Export singleton instance
draft_service_dynamodb = DraftServiceDynamoDB()
