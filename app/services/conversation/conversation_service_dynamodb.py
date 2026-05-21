"""Conversation and message management service using DynamoDB.

This is the DynamoDB implementation with time-series bucketing for messages.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict
from decimal import Decimal


def _sanitize_for_dynamodb(obj):
    """Recursively convert float values to Decimal for DynamoDB compatibility.
    
    DynamoDB does not support Python float — must use Decimal.
    Uses str() conversion to avoid floating-point precision issues.
    """
    if isinstance(obj, float):
        return Decimal(str(obj))
    elif isinstance(obj, dict):
        return {k: _sanitize_for_dynamodb(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_sanitize_for_dynamodb(i) for i in obj]
    return obj

from app.core.dynamodb import (
    get_item,
    put_item,
    update_item,
    query_items,
    get_hour_bucket,
    get_time_buckets_range,
    get_dynamodb_table,
    count_items,
)
from app.models.conversation import Conversation, Message


class ConversationServiceDynamoDB:
    """DynamoDB-based conversation service with optimized access patterns.

    Access Patterns:
    - Get conversation:        PK=CONV#<id>, SK=METADATA
    - List tenant conversations: GSI1PK=TENANT#<tenant_id>
    - Messages (time-series):   PK=CONV#<id>#<hour_bucket>, SK=MSG#<timestamp_ms>#<msg_id>
    """

    def create_conversation(
        self, tenant_id: str, user_identifier: Optional[str] = None
    ) -> Conversation:
        """Creates a new conversation."""
        conversation_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()

        conversation_item = {
            # Primary keys
            "PK": f"CONV#{conversation_id}",
            "SK": "METADATA",
            # GSI1: Tenant conversations (sorted by last_message_at)
            "GSI1PK": f"TENANT#{tenant_id}",
            "GSI1SK": f"CONV#{timestamp}#{conversation_id}",  # Will update on new messages
            # Conversation data
            "conversation_id": conversation_id,
            "tenant_id": tenant_id,
            "user_identifier": user_identifier,
            "started_at": timestamp,
            "last_message_at": timestamp,
            "message_count": 0,
            "is_active": True,
            "lead_captured": False,
            "extracted_lead_data": {},
            "lead_status": "collecting",
        }

        put_item(conversation_item)

        # Update analytics counters
        try:
            from app.services import analytics_service, USE_DYNAMODB
            if USE_DYNAMODB:
                # Used global datetime
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                analytics_service.increment_counter(tenant_id, "total_conversations")
                analytics_service.record_daily_metrics(tenant_id, today, "conversations")
                analytics_service.increment_platform_counter("total_conversations")
        except Exception as e:
            # Don't fail conversation creation if analytics fails
            print(f"Warning: Failed to update analytics: {e}")

        return Conversation(
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            user_identifier=user_identifier,
            started_at=datetime.fromisoformat(timestamp),
            last_message_at=datetime.fromisoformat(timestamp),
            message_count=0,
            is_active=True,
            lead_captured=False,
            extracted_lead_data={},
            lead_status="collecting",
        )

    def get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        """Retrieves a conversation by ID."""
        item = get_item(f"CONV#{conversation_id}", "METADATA")

        if item:
            started_at_raw = item.get("started_at")
            if isinstance(started_at_raw, str):
                started_at = datetime.fromisoformat(started_at_raw)
            else:
                started_at = started_at_raw or datetime.now(timezone.utc)

            last_message_at_raw = item.get("last_message_at")
            if isinstance(last_message_at_raw, str):
                last_message_at = datetime.fromisoformat(last_message_at_raw)
            else:
                last_message_at = last_message_at_raw or datetime.now(timezone.utc)

            return Conversation(
                conversation_id=item["conversation_id"],
                tenant_id=item["tenant_id"],
                user_identifier=item.get("user_identifier"),
                started_at=started_at,
                last_message_at=last_message_at,
                message_count=item.get("message_count", 0),
                is_active=item.get("is_active", True),
                lead_captured=item.get("lead_captured", False),
                extracted_lead_data=item.get("extracted_lead_data", {}),
                lead_status=item.get("lead_status", "collecting"),
                # State machine fields
                current_state=item.get("current_state", "greeting"),
                current_step_index=item.get("current_step_index", 0),
                step_ignore_count=item.get("step_ignore_count", 0),
                name_ask_count=item.get("name_ask_count", 0),
                mandatory_collected=item.get("mandatory_collected", False),
                # S3 Max agentic state
                s3_max_state=item.get("s3_max_state"),
                # S3 Mapping state
                s3_mapping_state=item.get("s3_mapping_state"),
            )
        return None

    def add_message(
        self, conversation_id: str, role: str, content: str, sources: List[str] = []
    ) -> Message:
        """Adds a message to a conversation using time-series bucketing.

        Time-Series Pattern:
        - PK: CONV#<conv_id>#<hour_bucket> (e.g., CONV#123#2025-12-23T14)
        - SK: MSG#<timestamp_ms>#<msg_id> (e.g., MSG#1766462400000#msg456)

        This distributes writes across multiple partitions (one per hour)
        and allows efficient querying of conversation messages.
        """
        message_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).timestamp()
        hour_bucket = get_hour_bucket(timestamp)

        message_item = {
            # Time-series keys
            "PK": f"CONV#{conversation_id}#{hour_bucket}",
            "SK": f"MSG#{int(timestamp * 1000)}#{message_id}",
            # Message data
            "message_id": message_id,
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "sources": sources,
            "timestamp": Decimal(str(timestamp)),
            "hour_bucket": hour_bucket,
        }

        put_item(message_item)

        # Update conversation metadata
        table = get_dynamodb_table()
        current_time = datetime.now(timezone.utc).isoformat()

        # Get tenant_id from conversation for GSI1SK update
        conv_item = get_item(f"CONV#{conversation_id}", "METADATA")
        tenant_id = conv_item.get("tenant_id") if conv_item else "UNKNOWN"

        table.update_item(
            Key={"PK": f"CONV#{conversation_id}", "SK": "METADATA"},
            UpdateExpression="SET last_message_at = :ts, message_count = if_not_exists(message_count, :zero) + :one, #gsi1sk = :gsi1sk",
            ExpressionAttributeNames={"#gsi1sk": "GSI1SK"},
            ExpressionAttributeValues={
                ":ts": current_time,
                ":zero": 0,
                ":one": 1,
                ":gsi1sk": f"CONV#{current_time}#{conversation_id}",  # Update sort order for tenant conversations
            },
        )

        return Message(
            message_id=message_id,
            conversation_id=conversation_id,
            role=role,
            content=content,
            sources=sources,
            timestamp=datetime.fromtimestamp(timestamp),
        )

    def get_messages(
        self, conversation_id: str, skip: int = 0, limit: int = 50
    ) -> List[Message]:
        """Retrieves messages for a conversation with pagination.

        Queries across hour buckets to get all messages.
        For most conversations, this will be 1-3 buckets.
        """
        # Get conversation key timestamps first
        conversation = self.get_conversation(conversation_id)
        if not conversation:
            return []

        # Determine time range from conversation metadata
        # Add slight buffer to end time to handle edge cases
        start_time = conversation.started_at
        end_time = conversation.last_message_at + timedelta(hours=1)
        
        # Guard against future/invalid timestamps if necessary, 
        # though logic ensures last_message_at >= started_at usually
        if end_time < start_time:
            end_time = datetime.now(timezone.utc)

        buckets = get_time_buckets_range(start_time.timestamp(), end_time.timestamp())

        all_messages = []
        for bucket in buckets:
            items = query_items(
                pk=f"CONV#{conversation_id}#{bucket}",
                limit=1000,  # Per-bucket limit (generous)
            )
            all_messages.extend(items)

        # Sort by timestamp (ascending - oldest first)
        all_messages.sort(key=lambda m: float(m.get("timestamp", 0)))

        # Apply pagination
        paginated = all_messages[skip : skip + limit]

        # Convert to Message model
        messages = []
        for msg in paginated:
            timestamp = float(msg.get("timestamp", 0))
            messages.append(
                Message(
                    message_id=msg["message_id"],
                    conversation_id=msg["conversation_id"],
                    role=msg["role"],
                    content=msg["content"],
                    sources=msg.get("sources", []),
                    timestamp=datetime.fromtimestamp(timestamp),
                )
            )

        return messages

    def get_tenant_conversation_count(self, tenant_id: str) -> int:
        """Returns the total number of conversations for a tenant using an optimized count query."""
        return count_items(
            pk=f"TENANT#{tenant_id}",
            index_name="GSI1",
        )

    def get_tenant_conversations(
        self, tenant_id: str, skip: int = 0, limit: int = 100
    ) -> List[Conversation]:
        """Lists conversations for a tenant.

        Uses GSI1 which is automatically sorted by last_message_at (DESC)
        thanks to the GSI1SK pattern: CONV#<timestamp>#<conv_id>
        """
        items = query_items(
            pk=f"TENANT#{tenant_id}",
            index_name="GSI1",
            limit=limit + skip,
            scan_forward=False,  # DESC order (newest first)
        )

        # Apply pagination and convert to Conversation model
        conversations = []
        for conv in items[skip : skip + limit]:
            started_at_raw = conv.get("started_at")
            if isinstance(started_at_raw, str):
                started_at = datetime.fromisoformat(started_at_raw)
            else:
                started_at = started_at_raw or datetime.now(timezone.utc)

            last_message_at_raw = conv.get("last_message_at")
            if isinstance(last_message_at_raw, str):
                last_message_at = datetime.fromisoformat(last_message_at_raw)
            else:
                last_message_at = last_message_at_raw or datetime.now(timezone.utc)

            conversations.append(
                Conversation(
                    conversation_id=conv["conversation_id"],
                    tenant_id=conv["tenant_id"],
                    user_identifier=conv.get("user_identifier"),
                    started_at=started_at,
                    last_message_at=last_message_at,
                    message_count=conv.get("message_count", 0),
                    is_active=conv.get("is_active", True),
                    lead_captured=conv.get("lead_captured", False),
                    extracted_lead_data=conv.get("extracted_lead_data", {}),
                    lead_status=conv.get("lead_status", "collecting"),
                )
            )

        return conversations

    def mark_lead_captured(self, conversation_id: str, name: str = "", phone: str = ""):
        """Marks a conversation as having a captured lead."""
        data: Dict[str, str] = {"lead_captured": True, "lead_status": "completed"}
        if name:
            data["extracted_lead_name"] = name
        if phone:
            data["extracted_lead_phone"] = phone
        update_item(f"CONV#{conversation_id}", "METADATA", data)

    def update_extracted_data(self, conversation_id: str, data: Dict[str, str]):
        """Updates extracted lead data."""
        update_item(
            f"CONV#{conversation_id}",
            "METADATA",
            {"extracted_lead_data": _sanitize_for_dynamodb(data), "lead_status": "collecting"},
        )

    def get_context_history(
        self, conversation_id: str, max_messages: int = 10, truncate_length: int = 500
    ) -> List[Dict[str, str]]:
        """
        Get optimized conversation history for LLM context.
        Retuns list of {"role": "...", "content": "..."} dicts.
        """
        # Get recent messages (fetch last 24h, sufficient for active context)
        # We fetch up to 50 to ensure we have enough to slice the last N
        messages = self.get_messages(conversation_id, limit=50)
        
        # Take last N (most recent)
        recent_messages = messages[-max_messages:] if len(messages) > max_messages else messages
        
        optimized = []
        for msg in recent_messages:
            content = msg.content or ""
            if len(content) > truncate_length:
                content = content[:truncate_length] + "..."
            optimized.append({"role": msg.role, "content": content})
            
        return optimized

    def update_conversation_state(
        self,
        conversation_id: str,
        current_state: str,
        step_index: int,
        ignore_count: int = 0,
        name_ask_count: int = 0
    ):
        """Updates state machine fields for a conversation."""
        update_item(
            f"CONV#{conversation_id}",
            "METADATA",
            {
                "current_state": current_state,
                "current_step_index": step_index,
                "step_ignore_count": ignore_count,
                "name_ask_count": name_ask_count,
            },
        )

    def update_s3_max_state(self, conversation_id: str, s3_max_state: dict):
        """Persists S3 Max agentic state (scorecard, question history, etc.)."""
        update_item(
            f"CONV#{conversation_id}",
            "METADATA",
            {"s3_max_state": _sanitize_for_dynamodb(s3_max_state)},
        )

    def update_s3_mapping_state(self, conversation_id: str, s3_mapping_state: dict):
        """Persists S3 Mapping state (phase, answers, signals, branching progress)."""
        update_item(
            f"CONV#{conversation_id}",
            "METADATA",
            {"s3_mapping_state": _sanitize_for_dynamodb(s3_mapping_state)},
        )


# Export singleton instance
conversation_service_dynamodb = ConversationServiceDynamoDB()
