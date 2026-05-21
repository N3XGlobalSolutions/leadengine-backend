"""Audit log service using DynamoDB."""

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from app.core.dynamodb import get_dynamodb_table, put_item, query_items
from app.models.audit import AuditLog, AuditLogCreate

class AuditServiceDynamoDB:
    """DynamoDB-based audit log service."""

    def log_activity(self, log_data: AuditLogCreate) -> AuditLog:
        """Create a new audit log entry."""
        log_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()

        log_item = {
            "PK": "AUDIT#SYSTEM",
            "SK": f"LOG#{timestamp}#{log_id}",
            "log_id": log_id,
            "action": log_data.action,
            "user_id": log_data.user_id,
            "user_name": log_data.user_name,
            "user_role": log_data.user_role,
            "tenant_id": log_data.tenant_id,
            "description": log_data.description,
            "created_at": timestamp,
            "ip_address": log_data.ip_address,
        }

        put_item(log_item)

        return AuditLog(
            log_id=log_id,
            action=log_data.action,
            user_id=log_data.user_id,
            user_name=log_data.user_name,
            user_role=log_data.user_role,
            tenant_id=log_data.tenant_id,
            description=log_data.description,
            created_at=datetime.fromisoformat(timestamp),
            ip_address=log_data.ip_address,
        )

    def list_audit_logs(
        self, skip: int = 0, limit: int = 100, start_date: Optional[str] = None, end_date: Optional[str] = None
    ) -> List[AuditLog]:
        """Fetch audit logs (newest first) with optional date filtering."""
        
        table = get_dynamodb_table()
        
        if start_date and not end_date:
            end_date = datetime.now(timezone.utc).isoformat()
        if end_date and not start_date:
            start_date = "2000-01-01"

        if start_date and end_date:
            end_sk = f"LOG#{end_date}"
            if "T" not in end_date:
                end_sk = f"LOG#{end_date}T23:59:59.999Z"
                
            condition = "PK = :pk AND SK BETWEEN :start_sk AND :end_sk"
            attr_values = {
                ":pk": "AUDIT#SYSTEM",
                ":start_sk": f"LOG#{start_date}",
                ":end_sk": end_sk,
            }
        else:
            condition = "PK = :pk AND begins_with(SK, :sk)"
            attr_values = {":pk": "AUDIT#SYSTEM", ":sk": "LOG#"}
            
        # We query the primary key directly. This will return up to limit items sorted descendending by SK.
        response = table.query(
            KeyConditionExpression=condition,
            ExpressionAttributeValues=attr_values,
            ScanIndexForward=False,  # Newest first
            Limit=limit + skip
        )
        
        items = response.get("Items", [])
        
        # Paginate
        paginated_items = items[skip:skip + limit]
        
        logs = []
        for item in paginated_items:
            created_at = item.get("created_at")
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)

            logs.append(AuditLog(
                log_id=item["log_id"],
                action=item["action"],
                user_id=item["user_id"],
                user_name=item["user_name"],
                user_role=item["user_role"],
                tenant_id=item.get("tenant_id"),
                description=item["description"],
                created_at=created_at,
                ip_address=item.get("ip_address"),
            ))

        return logs

# Export singleton instance
audit_service_dynamodb = AuditServiceDynamoDB()
