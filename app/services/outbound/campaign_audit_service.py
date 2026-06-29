"""Campaign audit logging service."""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.dynamodb import put_item, get_dynamodb_table
from app.models.outbound.audit import (
    CampaignAuditLog,
    CampaignAuditAction,
    CampaignAuditCreate,
)
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class CampaignAuditService:
    """Logs every campaign action for audit trail."""

    def log_action(self, data: CampaignAuditCreate) -> CampaignAuditLog:
        """Create an audit log entry for a campaign action."""
        log_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        item = {
            "PK": f"OB_CAMPAIGN#{data.campaign_id}",
            "SK": f"AUDIT#{now}#{log_id}",
            "log_id": log_id,
            "campaign_id": data.campaign_id,
            "tenant_id": data.tenant_id,
            "action": data.action.value,
            "user_id": data.user_id,
            "user_name": data.user_name,
            "description": data.description,
            "metadata": data.metadata or {},
            "created_at": now,
        }

        put_item(item)
        logger.info(
            f"Audit: {data.action.value} on campaign {data.campaign_id}",
            extra={"extra_data": {"campaign_id": data.campaign_id, "action": data.action.value}},
        )

        return CampaignAuditLog(
            log_id=log_id,
            campaign_id=data.campaign_id,
            tenant_id=data.tenant_id,
            action=data.action,
            user_id=data.user_id,
            user_name=data.user_name,
            description=data.description,
            metadata=data.metadata,
            created_at=datetime.fromisoformat(now),
        )

    def get_campaign_history(
        self, campaign_id: str, limit: int = 100
    ) -> List[CampaignAuditLog]:
        """Get audit history for a campaign (newest first)."""
        table = get_dynamodb_table()

        response = table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={
                ":pk": f"OB_CAMPAIGN#{campaign_id}",
                ":sk": "AUDIT#",
            },
            ScanIndexForward=False,
            Limit=limit,
        )

        logs = []
        for item in response.get("Items", []):
            created_at = item.get("created_at")
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)

            logs.append(
                CampaignAuditLog(
                    log_id=item["log_id"],
                    campaign_id=item["campaign_id"],
                    tenant_id=item["tenant_id"],
                    action=CampaignAuditAction(item["action"]),
                    user_id=item.get("user_id"),
                    user_name=item.get("user_name"),
                    description=item.get("description", ""),
                    metadata=item.get("metadata"),
                    created_at=created_at,
                )
            )

        return logs


# Singleton
campaign_audit_service = CampaignAuditService()
