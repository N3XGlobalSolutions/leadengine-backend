"""Campaign CRUD service using DynamoDB single-table design."""

import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from app.core.dynamodb import (
    get_dynamodb_table,
    get_item,
    put_item,
    update_item,
    query_items,
    delete_item,
    batch_write,
)
from app.models.outbound.campaign import (
    Campaign,
    CampaignCreate,
    CampaignUpdate,
    CampaignStatus,
    CampaignSummaryStats,
    VALID_TRANSITIONS,
)
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class CampaignService:
    """DynamoDB-based campaign service.

    Access Patterns:
    - Get campaign:              PK=OB_CAMPAIGN#{id}, SK=PROFILE
    - List tenant campaigns:     GSI1PK=TENANT#{tenant_id}, GSI1SK begins_with OB_CAMPAIGN#
    - List by status:            GSI2PK=TENANT#{tenant_id}#{status}, GSI2SK begins_with OB_CAMPAIGN#
    - Check running campaigns:   GSI2PK=TENANT#{tenant_id}#RUNNING
    """

    def _item_to_campaign(self, item: Dict[str, Any]) -> Campaign:
        """Convert a DynamoDB item to a Campaign model."""
        def _parse_dt(val):
            if val is None:
                return None
            if isinstance(val, str):
                return datetime.fromisoformat(val)
            return val

        return Campaign(
            campaign_id=item["campaign_id"],
            tenant_id=item["tenant_id"],
            name=item["name"],
            status=CampaignStatus(item.get("status", "DRAFT")),
            message_type=item.get("message_type", "text") or "text",
            message_template=item.get("message_template", ""),
            template_name=item.get("template_name"),
            template_language=item.get("template_language"),
            template_body=item.get("template_body", "") or "",
            template_params=list(item.get("template_params") or []),
            batch_size=int(item.get("batch_size", 10)),
            message_delay_seconds=int(item.get("message_delay_seconds", 5)),
            batch_delay_seconds=int(item.get("batch_delay_seconds", 60)),
            total_leads=int(item.get("total_leads", 0)),
            sent_count=max(0, int(item.get("sent_count", 0))),
            failed_count=int(item.get("failed_count", 0)),
            delivered_count=int(item.get("delivered_count", 0)),
            read_count=int(item.get("read_count", 0)),
            response_count=int(item.get("response_count", 0)),
            skipped_count=int(item.get("skipped_count", 0)),
            created_at=_parse_dt(item.get("created_at")),
            updated_at=_parse_dt(item.get("updated_at")),
            started_at=_parse_dt(item.get("started_at")),
            completed_at=_parse_dt(item.get("completed_at")),
            created_by=item.get("created_by"),
            s3_file_key=item.get("s3_file_key"),
            original_filename=item.get("original_filename"),
        )

    def create_campaign(
        self, tenant_id: str, data: CampaignCreate, user_id: str = None, user_name: str = None
    ) -> Campaign:
        """Create a new campaign in DRAFT status."""
        campaign_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        item = {
            "PK": f"OB_CAMPAIGN#{campaign_id}",
            "SK": "PROFILE",
            "GSI1PK": f"TENANT#{tenant_id}",
            "GSI1SK": f"OB_CAMPAIGN#{now}",
            "GSI2PK": f"TENANT#{tenant_id}#DRAFT",
            "GSI2SK": f"OB_CAMPAIGN#{now}",
            "campaign_id": campaign_id,
            "tenant_id": tenant_id,
            "name": data.name,
            "status": CampaignStatus.DRAFT.value,
            "message_type": data.message_type,
            "message_template": data.message_template,
            "template_name": data.template_name,
            "template_language": data.template_language,
            "template_body": data.template_body,
            "template_params": data.template_params,
            "batch_size": data.batch_size,
            "message_delay_seconds": data.message_delay_seconds,
            "batch_delay_seconds": data.batch_delay_seconds,
            "total_leads": 0,
            "sent_count": 0,
            "failed_count": 0,
            "delivered_count": 0,
            "read_count": 0,
            "response_count": 0,
            "skipped_count": 0,
            "created_at": now,
            "updated_at": now,
            "created_by": user_name or user_id,
        }

        put_item(item)
        logger.info(f"Campaign created: {campaign_id} for tenant {tenant_id}")

        return self._item_to_campaign(item)

    def get_campaign(self, campaign_id: str) -> Optional[Campaign]:
        """Get a campaign by ID."""
        item = get_item(f"OB_CAMPAIGN#{campaign_id}", "PROFILE")
        if item:
            return self._item_to_campaign(item)
        return None

    def list_campaigns(
        self,
        tenant_id: str,
        status: Optional[CampaignStatus] = None,
        limit: int = 100,
    ) -> List[Campaign]:
        """List campaigns for a tenant, optionally filtered by status."""
        if status:
            items = query_items(
                pk=f"TENANT#{tenant_id}#{status.value}",
                sk_prefix="OB_CAMPAIGN#",
                index_name="GSI2",
                limit=limit,
                scan_forward=False,
            )
        else:
            # Query all statuses and merge
            all_items = []
            for s in CampaignStatus:
                items = query_items(
                    pk=f"TENANT#{tenant_id}#{s.value}",
                    sk_prefix="OB_CAMPAIGN#",
                    index_name="GSI2",
                    limit=limit,
                    scan_forward=False,
                )
                all_items.extend(items)

            # Sort by created_at DESC
            all_items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            items = all_items[:limit]

        return [self._item_to_campaign(item) for item in items]

    def update_campaign(self, campaign_id: str, data: CampaignUpdate) -> Optional[Campaign]:
        """Update editable campaign fields. Only allowed when DRAFT or READY."""
        campaign = self.get_campaign(campaign_id)
        if not campaign:
            return None

        if campaign.status not in (CampaignStatus.DRAFT, CampaignStatus.READY):
            raise ValueError(f"Cannot edit campaign in {campaign.status.value} status")

        updates: Dict[str, Any] = {"updated_at": datetime.now(timezone.utc).isoformat()}

        if data.name is not None:
            updates["name"] = data.name
        if data.message_template is not None:
            updates["message_template"] = data.message_template
        for field in ("message_type", "template_name", "template_language", "template_body", "template_params"):
            value = getattr(data, field)
            if value is not None:
                updates[field] = value
        if data.batch_size is not None:
            updates["batch_size"] = data.batch_size
        if data.message_delay_seconds is not None:
            updates["message_delay_seconds"] = data.message_delay_seconds
        if data.batch_delay_seconds is not None:
            updates["batch_delay_seconds"] = data.batch_delay_seconds

        update_item(f"OB_CAMPAIGN#{campaign_id}", "PROFILE", updates)
        return self.get_campaign(campaign_id)

    def update_campaign_status(
        self, campaign_id: str, new_status: CampaignStatus
    ) -> Optional[Campaign]:
        """Transition a campaign to a new status with validation."""
        campaign = self.get_campaign(campaign_id)
        if not campaign:
            return None

        # Validate transition
        allowed = VALID_TRANSITIONS.get(campaign.status, [])
        if new_status not in allowed:
            raise ValueError(
                f"Invalid transition: {campaign.status.value} → {new_status.value}. "
                f"Allowed: {[s.value for s in allowed]}"
            )

        table = get_dynamodb_table()
        now = datetime.now(timezone.utc).isoformat()

        updates = {
            "status": new_status.value,
            "GSI2PK": f"TENANT#{campaign.tenant_id}#{new_status.value}",
            "updated_at": now,
        }

        if new_status == CampaignStatus.RUNNING and campaign.started_at is None:
            updates["started_at"] = now
        if new_status in (CampaignStatus.COMPLETED, CampaignStatus.FAILED):
            updates["completed_at"] = now

        # Use raw update to also change GSI2PK
        expr_parts = []
        attr_names = {}
        attr_values = {}
        for i, (key, value) in enumerate(updates.items()):
            attr_names[f"#a{i}"] = key
            attr_values[f":v{i}"] = value
            expr_parts.append(f"#a{i} = :v{i}")

        table.update_item(
            Key={"PK": f"OB_CAMPAIGN#{campaign_id}", "SK": "PROFILE"},
            UpdateExpression="SET " + ", ".join(expr_parts),
            ExpressionAttributeNames=attr_names,
            ExpressionAttributeValues=attr_values,
        )

        logger.info(f"Campaign {campaign_id} status: {campaign.status.value} → {new_status.value}")
        return self.get_campaign(campaign_id)

    def has_running_campaign(self, tenant_id: str) -> bool:
        """Check if the tenant already has a RUNNING campaign."""
        items = query_items(
            pk=f"TENANT#{tenant_id}#RUNNING",
            sk_prefix="OB_CAMPAIGN#",
            index_name="GSI2",
            limit=1,
        )
        return len(items) > 0

    def delete_campaign(self, campaign_id: str) -> bool:
        """Delete a campaign. Only DRAFT and ARCHIVED campaigns can be deleted."""
        campaign = self.get_campaign(campaign_id)
        if not campaign:
            return False

        if campaign.status not in (CampaignStatus.DRAFT, CampaignStatus.ARCHIVED):
            raise ValueError(
                f"Cannot delete campaign in {campaign.status.value} status. "
                "Only DRAFT and ARCHIVED campaigns can be deleted."
            )

        # Delete campaign profile
        delete_item(f"OB_CAMPAIGN#{campaign_id}", "PROFILE")

        # Delete all leads under this campaign
        table = get_dynamodb_table()
        response = table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={
                ":pk": f"OB_CAMPAIGN#{campaign_id}",
                ":sk": "LEAD#",
            },
        )
        for item in response.get("Items", []):
            table.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})

        # Delete audit logs
        response = table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={
                ":pk": f"OB_CAMPAIGN#{campaign_id}",
                ":sk": "AUDIT#",
            },
        )
        for item in response.get("Items", []):
            table.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})

        logger.info(f"Campaign {campaign_id} deleted")
        return True

    def increment_campaign_counter(
        self, campaign_id: str, counter_name: str, amount: int = 1
    ) -> None:
        """Atomically increment a campaign counter (sent_count, failed_count, etc.)."""
        table = get_dynamodb_table()
        table.update_item(
            Key={"PK": f"OB_CAMPAIGN#{campaign_id}", "SK": "PROFILE"},
            UpdateExpression=f"SET #counter = if_not_exists(#counter, :zero) + :amount, #updated = :now",
            ExpressionAttributeNames={
                "#counter": counter_name,
                "#updated": "updated_at",
            },
            ExpressionAttributeValues={
                ":amount": amount,
                ":zero": 0,
                ":now": datetime.now(timezone.utc).isoformat(),
            },
        )

    def get_dashboard_stats(self, tenant_id: str) -> CampaignSummaryStats:
        """Get aggregated dashboard statistics for a tenant's outbound campaigns."""
        campaigns = self.list_campaigns(tenant_id, limit=1000)

        total = len(campaigns)
        running = sum(1 for c in campaigns if c.status == CampaignStatus.RUNNING)
        total_leads = sum(c.total_leads for c in campaigns)
        sent = sum(c.sent_count for c in campaigns)
        failed = sum(c.failed_count for c in campaigns)
        delivered = sum(c.delivered_count for c in campaigns)
        read = sum(c.read_count for c in campaigns)
        responses = sum(c.response_count for c in campaigns)
        skipped = sum(c.skipped_count for c in campaigns)

        def pct(part: int, whole: int) -> float:
            return round(part / whole * 100, 1) if whole > 0 else 0.0

        return CampaignSummaryStats(
            total_campaigns=total,
            running_campaigns=running,
            total_leads=total_leads,
            messages_sent=sent,
            messages_failed=failed,
            messages_delivered=delivered,
            messages_read=read,
            replies=responses,
            skipped_opted_out=skipped,
            delivery_rate=pct(delivered, sent),
            read_rate=pct(read, delivered),
            response_rate=pct(responses, sent),
        )

    def update_lead_count(self, campaign_id: str, count: int) -> None:
        """Update the total_leads count on a campaign."""
        now = datetime.now(timezone.utc).isoformat()
        update_item(f"OB_CAMPAIGN#{campaign_id}", "PROFILE", {
            "total_leads": count,
            "updated_at": now,
        })


# Singleton
campaign_service = CampaignService()
