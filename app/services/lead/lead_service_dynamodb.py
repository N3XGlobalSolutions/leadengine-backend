"""Lead management service using DynamoDB.

This is the DynamoDB implementation of LeadService using single-table design.
"""

import uuid
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

from app.core.dynamodb import get_item, put_item, update_item, query_items
from app.models.lead import Lead, LeadCreate, LeadStatus, LeadListResponse
from decimal import Decimal


def _sanitize_for_dynamodb(obj):
    """Recursively convert float values to Decimal for DynamoDB compatibility."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    elif isinstance(obj, dict):
        return {k: _sanitize_for_dynamodb(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_sanitize_for_dynamodb(i) for i in obj]
    return obj


class LeadServiceDynamoDB:
    """DynamoDB-based lead service with optimized access patterns.

    Access Patterns:
    - Get lead by ID:         PK=LEAD#<id>, SK=PROFILE
    - Get conversation lead:  GSI1PK=CONV#<conv_id>
    - List tenant leads (all):     GSI2PK=TENANT#<tenant_id>
    - List tenant leads (status):  GSI2PK=TENANT#<tenant_id>#<status>
    """

    def create_lead(self, lead_data: LeadCreate) -> Lead:
        """Creates a new lead."""
        lead_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()

        # Use provided status or default to NEW
        status = lead_data.status if lead_data.status else LeadStatus.NEW

        lead_item = {
            # Primary keys
            "PK": f"LEAD#{lead_id}",
            "SK": "PROFILE",
            # GSI1: Conversation → Lead lookup
            "GSI1PK": f"CONV#{lead_data.conversation_id}",
            "GSI1SK": f"LEAD#{lead_id}",
            # GSI2: Tenant leads with status filtering
            "GSI2PK": f"TENANT#{lead_data.tenant_id}#{status.value}",
            "GSI2SK": f"LEAD#{timestamp}",
            # Lead data
            "lead_id": lead_id,
            "tenant_id": lead_data.tenant_id,
            "conversation_id": lead_data.conversation_id,
            "name": lead_data.name,
            "phone": lead_data.phone,
            "message": lead_data.message,
            "status": status.value,
            "created_at": timestamp,
            "metadata": _sanitize_for_dynamodb(lead_data.metadata or {}),
        }

        put_item(lead_item)

        # Update analytics counters
        try:
            from app.services import analytics_service, USE_DYNAMODB
            if USE_DYNAMODB:
                analytics_service.increment_counter(lead_data.tenant_id, "total_leads")
                analytics_service.increment_lead_status(lead_data.tenant_id, status.value)
                # Record daily metric
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                analytics_service.record_daily_metrics(lead_data.tenant_id, today, "leads")
                analytics_service.increment_platform_counter("total_leads")
        except Exception as e:
            # Don't fail lead creation if analytics fails
            print(f"Warning: Failed to update analytics: {e}")

        return Lead(
            lead_id=lead_id,
            tenant_id=lead_data.tenant_id,
            conversation_id=lead_data.conversation_id,
            name=lead_data.name,
            phone=lead_data.phone,
            message=lead_data.message,
            status=status,
            created_at=datetime.fromisoformat(timestamp),
            metadata=lead_data.metadata,
        )

    def get_lead(self, lead_id: str) -> Optional[Lead]:
        """Retrieves a lead by ID."""
        item = get_item(f"LEAD#{lead_id}", "PROFILE")

        if item:
            created_at_raw = item.get("created_at")
            if isinstance(created_at_raw, str):
                created_at = datetime.fromisoformat(created_at_raw)
            else:
                created_at = created_at_raw or datetime.now(timezone.utc)

            # Convert status string to enum
            status_value = item.get("status", "new")
            status = LeadStatus(status_value)

            return Lead(
                lead_id=item["lead_id"],
                tenant_id=item["tenant_id"],
                conversation_id=item["conversation_id"],
                name=item.get("name"),
                phone=item.get("phone"),
                message=item.get("message"),
                status=status,
                created_at=created_at,
                metadata=item.get("metadata", {}),
            )
        return None

    def list_tenant_leads(
        self,
        tenant_id: str,
        status: Optional[LeadStatus] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Lead]:
        """Lists all leads for a tenant, optionally filtered by status.

        Uses GSI2 with composite key for efficient filtering:
        - All leads: GSI2PK=TENANT#<tenant_id>, begins_with(GSI2SK, 'LEAD#')
        - By status: GSI2PK=TENANT#<tenant_id>#<status>

        Results are automatically sorted by created_at (DESC).
        """
        if status:
            # Query specific status
            pk = f"TENANT#{tenant_id}#{status.value}"
        else:
            # Query all statuses and combine.
            # Each status lives in its own GSI2 partition, so we must query each
            # independently. Use a large internal cap (1000) so we get ALL items for
            # every status bucket before slicing — otherwise the final paginate step
            # would silently discard leads from under-represented status buckets.
            INTERNAL_LIMIT = 1000
            all_leads = []
            for lead_status in LeadStatus:
                items = query_items(
                    pk=f"TENANT#{tenant_id}#{lead_status.value}",
                    index_name="GSI2",
                    limit=INTERNAL_LIMIT,
                    scan_forward=False,  # DESC order (newest first)
                )
                all_leads.extend(items)

            # Sort combined results by created_at DESC
            all_leads.sort(key=lambda x: x.get("created_at", ""), reverse=True)

            # Apply pagination
            paginated_items = all_leads[skip : skip + limit]

            # Convert to Lead models
            leads = []
            for item in paginated_items:
                created_at = item.get("created_at")
                if isinstance(created_at, str):
                    created_at = datetime.fromisoformat(created_at)

                status_value = item.get("status", "new")
                item_status = LeadStatus(status_value)

                leads.append(
                    Lead(
                        lead_id=item["lead_id"],
                        tenant_id=item["tenant_id"],
                        conversation_id=item["conversation_id"],
                        name=item.get("name"),
                        phone=item.get("phone"),
                        message=item.get("message"),
                        status=item_status,
                        created_at=created_at,
                        metadata=item.get("metadata", {}),
                    )
                )

            return leads


        # Query single status
        items = query_items(
            pk=pk,
            index_name="GSI2",
            limit=limit + skip,
            scan_forward=False,  # DESC order (newest first)
        )

        # Apply pagination and convert to Lead model
        leads = []
        for item in items[skip : skip + limit]:
            created_at_raw = item.get("created_at")
            if isinstance(created_at_raw, str):
                created_at = datetime.fromisoformat(created_at_raw)
            else:
                created_at = created_at_raw or datetime.now(timezone.utc)

            status_value = item.get("status", "new")
            item_status = LeadStatus(status_value)

            leads.append(
                Lead(
                    lead_id=item["lead_id"],
                    tenant_id=item["tenant_id"],
                    conversation_id=item["conversation_id"],
                    name=item.get("name"),
                    phone=item.get("phone"),
                    message=item.get("message"),
                    status=item_status,
                    created_at=created_at,
                    metadata=item.get("metadata", {}),
                )
            )

        return leads

    def export_leads(
            self,
            tenant_id: str,
            status: Optional[LeadStatus] = None,
            skip: int = 0,
            limit: int = 100,
    ) -> List[LeadListResponse]:

        all_leads = []

        if status:

            items = query_items(
                pk=f"TENANT#{tenant_id}#{status.value}",
                index_name="GSI2",
                limit=limit,
                scan_forward=False,
            )

            all_leads.extend(items)

        else:

            INTERNAL_LIMIT = 1000

            for lead_status in LeadStatus:
                items = query_items(
                    pk=f"TENANT#{tenant_id}#{lead_status.value}",
                    index_name="GSI2",
                    limit=INTERNAL_LIMIT,
                    scan_forward=False,
                )

                all_leads.extend(items)

        # Sort all leads
        all_leads.sort(
            key=lambda x: x.get("created_at", ""),
            reverse=True
        )

        # Pagination
        paginated_items = all_leads[skip: skip + limit]

        # Response list
        leads: List[LeadListResponse] = []

        for item in paginated_items:

            created_at = item.get("created_at")

            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)

            status_value = item.get("status", "new")

            leads.append(
                LeadListResponse(
                    name=item.get("name"),
                    phone=item.get("phone"),
                    status=status_value,
                    created_at=created_at,
                )
            )

        return leads


    def update_lead_details(self, lead_id: str, updates: Dict[str, Any]) -> Optional[Lead]:
        """Updates lead details (name, phone, metadata, etc.)."""
        # Get current lead to ensure it exists
        current_lead = self.get_lead(lead_id)
        if not current_lead:
            return None

        # Helper to perform update
        from app.core.dynamodb import update_item

        # Filter for allowed fields to prevent accidental overwrites of keys/status
        allowed_fields = ["name", "phone", "message", "metadata"]
        valid_updates = {
            k: v for k, v in updates.items() 
            if k in allowed_fields and v is not None
        }

        if not valid_updates:
            return current_lead

        update_item(f"LEAD#{lead_id}", "PROFILE", valid_updates)

        return self.get_lead(lead_id)

    def update_lead_status(self, lead_id: str, status: LeadStatus) -> Optional[Lead]:
        """Updates a lead's status.

        NOTE: This also needs to update GSI2PK to maintain the status filter.
        We need to get the current lead first to know the tenant_id.
        """
        # Get current lead to know tenant_id
        current_lead = self.get_lead(lead_id)
        if not current_lead:
            return None

        # Update status and GSI2PK
        from app.core.dynamodb import get_dynamodb_table

        table = get_dynamodb_table()

        table.update_item(
            Key={"PK": f"LEAD#{lead_id}", "SK": "PROFILE"},
            UpdateExpression="SET #status = :status, #gsi2pk = :gsi2pk",
            ExpressionAttributeNames={"#status": "status", "#gsi2pk": "GSI2PK"},
            ExpressionAttributeValues={
                ":status": status.value if isinstance(status, LeadStatus) else status,
                ":gsi2pk": f"TENANT#{current_lead.tenant_id}#{status.value if isinstance(status, LeadStatus) else status}",
            },
        )

        # Update analytics counters for status change
        try:
            from app.services import analytics_service, USE_DYNAMODB
            if USE_DYNAMODB:
                old_status = current_lead.status.value
                new_status = status.value if isinstance(status, LeadStatus) else status
                # Decrement old status, increment new status
                analytics_service.increment_lead_status(current_lead.tenant_id, old_status, -1)
                analytics_service.increment_lead_status(current_lead.tenant_id, new_status, 1)
        except Exception as e:
            print(f"Warning: Failed to update analytics: {e}")

        return self.get_lead(lead_id)

    def get_conversation_lead(self, conversation_id: str) -> Optional[Lead]:
        """Gets the lead associated with a conversation.

        Uses GSI1 for efficient lookup.
        """
        items = query_items(pk=f"CONV#{conversation_id}", index_name="GSI1", limit=1)

        if items:
            item = items[0]
            created_at = item.get("created_at")
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)

            status_value = item.get("status", "new")
            status = LeadStatus(status_value)

            return Lead(
                lead_id=item["lead_id"],
                tenant_id=item["tenant_id"],
                conversation_id=item["conversation_id"],
                name=item.get("name"),
                phone=item.get("phone"),
                message=item.get("message"),
                status=status,
                created_at=created_at,
                metadata=item.get("metadata", {}),
            )
        return None


# Export singleton instance
lead_service_dynamodb = LeadServiceDynamoDB()
