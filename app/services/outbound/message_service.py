"""Outbound message tracking service."""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.dynamodb import put_item, get_dynamodb_table, query_items
from app.models.outbound.message import OutboundMessage, OutboundMessageStatus
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class MessageService:
    """Tracks individual messages sent during campaign execution."""

    def create_message(
        self,
        campaign_id: str,
        lead_id: str,
        tenant_id: str,
        phone: str,
        rendered_message: str,
        provider: str = "telegram",
    ) -> OutboundMessage:
        """Record a new outbound message."""
        message_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        item = {
            "PK": f"OB_MSG#{message_id}",
            "SK": "PROFILE",
            "GSI1PK": f"OB_CAMPAIGN#{campaign_id}",
            "GSI1SK": f"MSG#{now}",
            "GSI2PK": f"OB_LEAD#{lead_id}",
            "GSI2SK": f"MSG#{now}",
            "message_id": message_id,
            "campaign_id": campaign_id,
            "lead_id": lead_id,
            "tenant_id": tenant_id,
            "phone": phone,
            "rendered_message": rendered_message,
            "status": OutboundMessageStatus.PENDING.value,
            "provider": provider,
            "retry_count": 0,
            "created_at": now,
        }

        put_item(item)
        return OutboundMessage(
            message_id=message_id,
            campaign_id=campaign_id,
            lead_id=lead_id,
            tenant_id=tenant_id,
            phone=phone,
            rendered_message=rendered_message,
            provider=provider,
            created_at=datetime.fromisoformat(now),
        )

    def update_message_status(
        self,
        message_id: str,
        status: OutboundMessageStatus,
        provider_message_id: str = None,
        error_message: str = None,
    ) -> None:
        """Update a message's delivery status."""
        table = get_dynamodb_table()
        now = datetime.now(timezone.utc).isoformat()

        updates = {"#status": status.value}
        attr_names = {"#status": "status"}
        attr_values = {":status": status.value}
        expr_parts = ["#status = :status"]

        if provider_message_id:
            attr_names["#pmid"] = "provider_message_id"
            attr_values[":pmid"] = provider_message_id
            expr_parts.append("#pmid = :pmid")

        if error_message:
            attr_names["#err"] = "error_message"
            attr_values[":err"] = error_message
            expr_parts.append("#err = :err")

        if status == OutboundMessageStatus.SENT:
            attr_names["#sent"] = "sent_at"
            attr_values[":sent"] = now
            expr_parts.append("#sent = :sent")

        if status == OutboundMessageStatus.DELIVERED:
            attr_names["#del"] = "delivered_at"
            attr_values[":del"] = now
            expr_parts.append("#del = :del")

        table.update_item(
            Key={"PK": f"OB_MSG#{message_id}", "SK": "PROFILE"},
            UpdateExpression="SET " + ", ".join(expr_parts),
            ExpressionAttributeNames=attr_names,
            ExpressionAttributeValues=attr_values,
        )

    def get_campaign_messages(
        self, campaign_id: str, limit: int = 100
    ) -> List[OutboundMessage]:
        """Get all messages for a campaign."""
        items = query_items(
            pk=f"OB_CAMPAIGN#{campaign_id}",
            sk_prefix="MSG#",
            index_name="GSI1",
            limit=limit,
            scan_forward=False,
        )

        messages = []
        for item in items:
            created_at = item.get("created_at")
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)

            sent_at = item.get("sent_at")
            if isinstance(sent_at, str):
                sent_at = datetime.fromisoformat(sent_at)

            delivered_at = item.get("delivered_at")
            if isinstance(delivered_at, str):
                delivered_at = datetime.fromisoformat(delivered_at)

            messages.append(OutboundMessage(
                message_id=item["message_id"],
                campaign_id=item["campaign_id"],
                lead_id=item["lead_id"],
                tenant_id=item["tenant_id"],
                phone=item["phone"],
                rendered_message=item.get("rendered_message", ""),
                status=OutboundMessageStatus(item.get("status", "PENDING")),
                provider=item.get("provider", "telegram"),
                provider_message_id=item.get("provider_message_id"),
                error_message=item.get("error_message"),
                retry_count=int(item.get("retry_count", 0)),
                created_at=created_at,
                sent_at=sent_at,
                delivered_at=delivered_at,
            ))

        return messages


# Singleton
message_service = MessageService()
