"""Repository and database service for Outbound AI entities using DynamoDB."""

import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
import uuid
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key

from app.core.dynamodb import get_dynamodb_table, get_dynamodb_client
from app.models.outbound.outbound_entities import (
    ConversationStage,
    OutboundConversation,
    OutboundAIMessage,
    OutboundMemory,
    OutboundProduct,
    OutboundAgentSettings,
    OutboundCalendarSettings,
    OutboundCalendarMeeting,
    OutboundConversationAnalytics,
)


def _to_decimal(obj: Any) -> Any:
    """Recursively convert float to Decimal for DynamoDB compliance."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    elif isinstance(obj, dict):
        return {k: _to_decimal(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_to_decimal(i) for i in obj]
    return obj


def _from_decimal(obj: Any) -> Any:
    """Recursively convert Decimal to standard types (float or int)."""
    if isinstance(obj, Decimal):
        # Convert to int if float part is empty, else float
        if obj % 1 == 0:
            return int(obj)
        return float(obj)
    elif isinstance(obj, dict):
        return {k: _from_decimal(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_from_decimal(i) for i in obj]
    return obj


class OutboundDBService:
    """DynamoDB repository layer for Enterprise Outbound AI Agent."""

    # ── CONVERSATION LOCKS (Distributed Lock) ──────────────────────────

    def acquire_lock(self, conversation_id: str, ttl_seconds: int = 120) -> Optional[str]:
        """Acquire a fenced lock on a conversation using a conditional write.

        Returns a unique owner token on success (truthy), or None if the lock is
        held by someone else. The token must be passed back to ``release_lock``
        so a worker whose lock has already expired (and been re-acquired by
        another worker) cannot delete the new owner's lock. TTL defaults to 120s
        to comfortably exceed worst-case pipeline latency (multi-step LLM).
        """
        table = get_dynamodb_table()
        now = int(time.time())
        expires_at = now + ttl_seconds
        owner = str(uuid.uuid4())
        pk = f"LOCK#CONV#{conversation_id}"
        sk = "LOCK"

        try:
            table.put_item(
                Item={
                    "PK": pk,
                    "SK": sk,
                    "owner": owner,
                    "expires_at": expires_at,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                ConditionExpression="attribute_not_exists(PK) OR expires_at < :now",
                ExpressionAttributeValues={":now": now},
            )
            return owner
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return None
            raise e

    def release_lock(self, conversation_id: str, owner: Optional[str] = None) -> bool:
        """Release the lock only if we still own it (fencing).

        Passing the ``owner`` token returned by ``acquire_lock`` ensures we do
        not delete a lock that has since expired and been taken over by another
        worker. If no token is provided we fall back to an unconditional delete
        for backwards compatibility.
        """
        table = get_dynamodb_table()
        pk = f"LOCK#CONV#{conversation_id}"
        sk = "LOCK"
        try:
            if owner is not None:
                table.delete_item(
                    Key={"PK": pk, "SK": sk},
                    ConditionExpression="owner = :owner",
                    ExpressionAttributeValues={":owner": owner},
                )
            else:
                table.delete_item(Key={"PK": pk, "SK": sk})
            return True
        except ClientError as e:
            # ConditionalCheckFailed means someone else owns it now — that's fine.
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            return False

    # ── WEBHOOK DEDUPLICATION ─────────────────────────────────────────

    def check_and_set_dedup(self, message_id: str, ttl_seconds: int = 86400) -> bool:
        """Verify message idempotency and log message_id if new."""
        table = get_dynamodb_table()
        now = int(time.time())
        expires_at = now + ttl_seconds
        pk = f"OB_MSG_DEDUP#{message_id}"
        sk = "DEDUP"

        try:
            table.put_item(
                Item={
                    "PK": pk,
                    "SK": sk,
                    "expires_at": expires_at,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                ConditionExpression="attribute_not_exists(PK)",
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise e

    # ── AGENT & CALENDAR SETTINGS ─────────────────────────────────────

    def get_agent_settings(self, tenant_id: str) -> OutboundAgentSettings:
        """Fetch agent settings for tenant, returning defaults if not found."""
        table = get_dynamodb_table()
        pk = f"OB_SETTING#{tenant_id}"
        sk = "AGENT"
        try:
            res = table.get_item(Key={"PK": pk, "SK": sk})
            item = res.get("Item")
            if not item:
                return OutboundAgentSettings(tenant_id=tenant_id)
            
            # Map item fields
            return OutboundAgentSettings(
                tenant_id=tenant_id,
                ai_name=item.get("ai_name", "Sales Assistant"),
                business_summary=item.get("business_summary", ""),
                sales_tone=item.get("sales_tone", "Professional and helpful"),
                conversation_style=item.get("conversation_style", "Informative and natural"),
                target_audience=item.get("target_audience", "General business clients"),
                primary_language=item.get("primary_language", "en"),
                max_response_length=int(item.get("max_response_length", 500)),
                enable_scheduling=bool(item.get("enable_scheduling", True)),
                enable_product_search=bool(item.get("enable_product_search", True)),
                enable_knowledge_search=bool(item.get("enable_knowledge_search", True)),
            )
        except Exception:
            return OutboundAgentSettings(tenant_id=tenant_id)

    def save_agent_settings(self, settings: OutboundAgentSettings) -> OutboundAgentSettings:
        """Save tenant outbound agent settings."""
        table = get_dynamodb_table()
        pk = f"OB_SETTING#{settings.tenant_id}"
        sk = "AGENT"
        
        item = {
            "PK": pk,
            "SK": sk,
            "tenant_id": settings.tenant_id,
            "ai_name": settings.ai_name,
            "business_summary": settings.business_summary,
            "sales_tone": settings.sales_tone,
            "conversation_style": settings.conversation_style,
            "target_audience": settings.target_audience,
            "primary_language": settings.primary_language,
            "max_response_length": settings.max_response_length,
            "enable_scheduling": settings.enable_scheduling,
            "enable_product_search": settings.enable_product_search,
            "enable_knowledge_search": settings.enable_knowledge_search,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        table.put_item(Item=item)
        return settings

    def get_calendar_settings(self, tenant_id: str) -> OutboundCalendarSettings:
        """Fetch calendar credentials and integration settings."""
        table = get_dynamodb_table()
        pk = f"OB_SETTING#{tenant_id}"
        sk = "CALENDAR"
        try:
            res = table.get_item(Key={"PK": pk, "SK": sk})
            item = res.get("Item")
            if not item:
                return OutboundCalendarSettings(tenant_id=tenant_id)
            
            return OutboundCalendarSettings(
                tenant_id=tenant_id,
                google_client_id=item.get("google_client_id"),
                google_client_secret=item.get("google_client_secret"),
                google_refresh_token=item.get("google_refresh_token"),
                calendar_id=item.get("calendar_id", "primary"),
                timezone=item.get("timezone", "UTC"),
                working_days=item.get("working_days", ["monday", "tuesday", "wednesday", "thursday", "friday"]),
                working_hours_start=item.get("working_hours_start", "09:00"),
                working_hours_end=item.get("working_hours_end", "17:00"),
                meeting_duration=int(item.get("meeting_duration", 30)),
                buffer=int(item.get("buffer", 15)),
            )
        except Exception:
            return OutboundCalendarSettings(tenant_id=tenant_id)

    def save_calendar_settings(self, settings: OutboundCalendarSettings) -> OutboundCalendarSettings:
        """Save tenant calendar settings."""
        table = get_dynamodb_table()
        pk = f"OB_SETTING#{settings.tenant_id}"
        sk = "CALENDAR"
        
        item = {
            "PK": pk,
            "SK": sk,
            "tenant_id": settings.tenant_id,
            "google_client_id": settings.google_client_id,
            "google_client_secret": settings.google_client_secret,
            "google_refresh_token": settings.google_refresh_token,
            "calendar_id": settings.calendar_id,
            "timezone": settings.timezone,
            "working_days": settings.working_days,
            "working_hours_start": settings.working_hours_start,
            "working_hours_end": settings.working_hours_end,
            "meeting_duration": settings.meeting_duration,
            "buffer": settings.buffer,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        table.put_item(Item=item)
        return settings

    # ── PRODUCTS CRUD ─────────────────────────────────────────────────

    def get_product(self, product_id: str) -> Optional[OutboundProduct]:
        """Retrieve a specific product from database."""
        table = get_dynamodb_table()
        pk = f"OB_PRODUCT#{product_id}"
        sk = "METADATA"
        
        res = table.get_item(Key={"PK": pk, "SK": sk})
        item = res.get("Item")
        if not item:
            return None
            
        return OutboundProduct(
            product_id=item["product_id"],
            tenant_id=item["tenant_id"],
            name=item["name"],
            description=item["description"],
            short_description=item["short_description"],
            price=Decimal(str(item["price"])),
            currency=item.get("currency", "USD"),
            images=item.get("images", []),
            gallery=item.get("gallery", []),
            videos=item.get("videos", []),
            features=item.get("features", []),
            benefits=item.get("benefits", []),
            specifications=item.get("specifications", {}),
            category=item["category"],
            tags=item.get("tags", []),
            brochure_pdf=item.get("brochure_pdf"),
            status=item.get("status", "ACTIVE"),
            search_keywords=item.get("search_keywords", []),
        )

    def save_product(self, product: OutboundProduct) -> OutboundProduct:
        """Save or overwrite product record."""
        table = get_dynamodb_table()
        pk = f"OB_PRODUCT#{product.product_id}"
        sk = "METADATA"
        
        item = {
            "PK": pk,
            "SK": sk,
            "GSI1PK": f"TENANT#{product.tenant_id}#PRODUCTS",
            "GSI1SK": f"PROD#{product.product_id}",
            "product_id": product.product_id,
            "tenant_id": product.tenant_id,
            "name": product.name,
            "description": product.description,
            "short_description": product.short_description,
            "price": _to_decimal(product.price),
            "currency": product.currency,
            "images": product.images,
            "gallery": product.gallery,
            "videos": product.videos,
            "features": product.features,
            "benefits": product.benefits,
            "specifications": product.specifications,
            "category": product.category,
            "tags": product.tags,
            "brochure_pdf": product.brochure_pdf,
            "status": product.status,
            "search_keywords": product.search_keywords,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        table.put_item(Item=item)
        return product

    def delete_product(self, product_id: str) -> bool:
        """Delete product by ID."""
        table = get_dynamodb_table()
        pk = f"OB_PRODUCT#{product_id}"
        sk = "METADATA"
        try:
            table.delete_item(Key={"PK": pk, "SK": sk})
            return True
        except Exception:
            return False

    def list_tenant_products(self, tenant_id: str) -> List[OutboundProduct]:
        """List all active products for tenant using GSI1."""
        table = get_dynamodb_table()
        res = table.query(
            IndexName="GSI1",
            KeyConditionExpression="GSI1PK = :pk AND begins_with(GSI1SK, :sk)",
            ExpressionAttributeValues={
                ":pk": f"TENANT#{tenant_id}#PRODUCTS",
                ":sk": "PROD#",
            }
        )
        products = []
        for item in res.get("Items", []):
            products.append(
                OutboundProduct(
                    product_id=item["product_id"],
                    tenant_id=item["tenant_id"],
                    name=item["name"],
                    description=item["description"],
                    short_description=item["short_description"],
                    price=Decimal(str(item["price"])),
                    currency=item.get("currency", "USD"),
                    images=item.get("images", []),
                    gallery=item.get("gallery", []),
                    videos=item.get("videos", []),
                    features=item.get("features", []),
                    benefits=item.get("benefits", []),
                    specifications=item.get("specifications", {}),
                    category=item["category"],
                    tags=item.get("tags", []),
                    brochure_pdf=item.get("brochure_pdf"),
                    status=item.get("status", "ACTIVE"),
                    search_keywords=item.get("search_keywords", []),
                )
            )
        return products

    # ── CONVERSATIONS AND MESSAGES ────────────────────────────────────

    def get_conversation(self, conversation_id: str) -> Optional[OutboundConversation]:
        """Fetch conversation by ID."""
        table = get_dynamodb_table()
        pk = f"OB_CONV#{conversation_id}"
        sk = "METADATA"
        
        res = table.get_item(Key={"PK": pk, "SK": sk})
        item = res.get("Item")
        if not item:
            return None
            
        return OutboundConversation(
            conversation_id=item["conversation_id"],
            tenant_id=item["tenant_id"],
            lead_id=item["lead_id"],
            campaign_id=item["campaign_id"],
            started_at=datetime.fromisoformat(item["started_at"]),
            last_message_at=datetime.fromisoformat(item["last_message_at"]),
            stage=ConversationStage(item.get("stage", "NEW")),
            status=item.get("status", "ACTIVE"),
            message_count=int(item.get("message_count", 0)),
            response_time_sum=float(item.get("response_time_sum", 0.0)),
            response_count=int(item.get("response_count", 0)),
            token_usage_sum=int(item.get("token_usage_sum", 0)),
        )

    def save_conversation(self, conversation: OutboundConversation) -> OutboundConversation:
        """Save conversation metadata and update list index GSI1."""
        table = get_dynamodb_table()
        pk = f"OB_CONV#{conversation.conversation_id}"
        sk = "METADATA"
        
        item = {
            "PK": pk,
            "SK": sk,
            "GSI1PK": f"TENANT#{conversation.tenant_id}#OUTBOUND_CONVS",
            "GSI1SK": f"CONV#{conversation.last_message_at.isoformat()}#{conversation.conversation_id}",
            "conversation_id": conversation.conversation_id,
            "tenant_id": conversation.tenant_id,
            "lead_id": conversation.lead_id,
            "campaign_id": conversation.campaign_id,
            "started_at": conversation.started_at.isoformat(),
            "last_message_at": conversation.last_message_at.isoformat(),
            "stage": conversation.stage.value,
            "status": conversation.status,
            "message_count": conversation.message_count,
            "response_time_sum": _to_decimal(conversation.response_time_sum),
            "response_count": conversation.response_count,
            "token_usage_sum": conversation.token_usage_sum,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        table.put_item(Item=item)
        return conversation

    def list_tenant_conversations(self, tenant_id: str) -> List[OutboundConversation]:
        """List conversations for a tenant ordered by last_message_at desc."""
        table = get_dynamodb_table()
        res = table.query(
            IndexName="GSI1",
            KeyConditionExpression="GSI1PK = :pk",
            ExpressionAttributeValues={
                ":pk": f"TENANT#{tenant_id}#OUTBOUND_CONVS",
            },
            ScanIndexForward=False  # Descending order
        )
        
        conversations = []
        for item in res.get("Items", []):
            conversations.append(
                OutboundConversation(
                    conversation_id=item["conversation_id"],
                    tenant_id=item["tenant_id"],
                    lead_id=item["lead_id"],
                    campaign_id=item["campaign_id"],
                    started_at=datetime.fromisoformat(item["started_at"]),
                    last_message_at=datetime.fromisoformat(item["last_message_at"]),
                    stage=ConversationStage(item.get("stage", "NEW")),
                    status=item.get("status", "ACTIVE"),
                    message_count=int(item.get("message_count", 0)),
                    response_time_sum=float(item.get("response_time_sum", 0.0)),
                    response_count=int(item.get("response_count", 0)),
                    token_usage_sum=int(item.get("token_usage_sum", 0)),
                )
            )
        return conversations

    def add_message(self, message: OutboundAIMessage) -> OutboundAIMessage:
        """Add chat message into conversation thread."""
        table = get_dynamodb_table()
        pk = f"OB_CONV#{message.conversation_id}"
        timestamp_ms = int(message.timestamp.timestamp() * 1000)
        sk = f"MSG#{timestamp_ms}#{message.message_id}"
        
        item = {
            "PK": pk,
            "SK": sk,
            "message_id": message.message_id,
            "conversation_id": message.conversation_id,
            "role": message.role,
            "content": message.content,
            "timestamp": message.timestamp.isoformat(),
            "tool_calls": _to_decimal(message.tool_calls) if message.tool_calls else None,
            "prompt_version": message.prompt_version,
            "metadata": _to_decimal(message.metadata),
        }
        table.put_item(Item=item)
        return message

    def get_conversation_messages(self, conversation_id: str, limit: int = 50) -> List[OutboundAIMessage]:
        """Fetch chat history for conversation."""
        table = get_dynamodb_table()
        res = table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={
                ":pk": f"OB_CONV#{conversation_id}",
                ":sk": "MSG#",
            },
            Limit=limit,
            ScanIndexForward=True  # Chronological order
        )
        
        messages = []
        for item in res.get("Items", []):
            messages.append(
                OutboundAIMessage(
                    message_id=item["message_id"],
                    conversation_id=item["conversation_id"],
                    role=item["role"],
                    content=item["content"],
                    timestamp=datetime.fromisoformat(item["timestamp"]),
                    tool_calls=_from_decimal(item.get("tool_calls")),
                    prompt_version=item.get("prompt_version"),
                    metadata=_from_decimal(item.get("metadata", {})),
                )
            )
        return messages

    # ── CONVERSATION MEMORY ───────────────────────────────────────────

    def get_memory(self, conversation_id: str) -> OutboundMemory:
        """Get structured memory profile for conversation, or new object."""
        table = get_dynamodb_table()
        pk = f"OB_CONV#{conversation_id}"
        sk = "MEMORY"
        
        try:
            res = table.get_item(Key={"PK": pk, "SK": sk})
            item = res.get("Item")
            if not item:
                return OutboundMemory(conversation_id=conversation_id)
                
            return OutboundMemory(
                conversation_id=conversation_id,
                conversation_summary=item.get("conversation_summary", ""),
                interested_products=item.get("interested_products", []),
                budget=item.get("budget"),
                preferred_language=item.get("preferred_language", "en"),
                meeting_status=item.get("meeting_status", "PENDING"),
                objections=item.get("objections", []),
                preferences=item.get("preferences", {}),
                customer_intent=item.get("customer_intent", ""),
                last_action=item.get("last_action", ""),
                conversation_stage=ConversationStage(item.get("conversation_stage", "NEW")),
            )
        except Exception:
            return OutboundMemory(conversation_id=conversation_id)

    def save_memory(self, memory: OutboundMemory) -> OutboundMemory:
        """Save updated structured memory."""
        table = get_dynamodb_table()
        pk = f"OB_CONV#{memory.conversation_id}"
        sk = "MEMORY"
        
        item = {
            "PK": pk,
            "SK": sk,
            "conversation_id": memory.conversation_id,
            "conversation_summary": memory.conversation_summary,
            "interested_products": memory.interested_products,
            "budget": memory.budget,
            "preferred_language": memory.preferred_language,
            "meeting_status": memory.meeting_status,
            "objections": memory.objections,
            "preferences": _to_decimal(memory.preferences),
            "customer_intent": memory.customer_intent,
            "last_action": memory.last_action,
            "conversation_stage": memory.conversation_stage.value,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        table.put_item(Item=item)
        return memory

    # ── CALENDAR MEETINGS ─────────────────────────────────────────────

    def get_meeting(self, meeting_id: str) -> Optional[OutboundCalendarMeeting]:
        """Fetch internal scheduled meeting details."""
        table = get_dynamodb_table()
        pk = f"OB_MEETING#{meeting_id}"
        sk = "METADATA"
        
        res = table.get_item(Key={"PK": pk, "SK": sk})
        item = res.get("Item")
        if not item:
            return None
            
        return OutboundCalendarMeeting(
            meeting_id=item["meeting_id"],
            tenant_id=item["tenant_id"],
            lead_id=item["lead_id"],
            conversation_id=item["conversation_id"],
            event_id=item["event_id"],
            summary=item["summary"],
            description=item["description"],
            start_time=datetime.fromisoformat(item["start_time"]),
            end_time=datetime.fromisoformat(item["end_time"]),
            timezone=item.get("timezone", "UTC"),
            attendee_name=item["attendee_name"],
            attendee_email=item["attendee_email"],
            attendee_phone=item["attendee_phone"],
            meet_link=item.get("meet_link"),
            status=item.get("status", "BOOKED"),
        )

    def save_meeting(self, meeting: OutboundCalendarMeeting) -> OutboundCalendarMeeting:
        """Save structured scheduled meeting record and update lists."""
        table = get_dynamodb_table()
        pk = f"OB_MEETING#{meeting.meeting_id}"
        sk = "METADATA"
        
        item = {
            "PK": pk,
            "SK": sk,
            "GSI1PK": f"TENANT#{meeting.tenant_id}#MEETINGS",
            "GSI1SK": f"MEETING#{meeting.start_time.isoformat()}#{meeting.meeting_id}",
            "meeting_id": meeting.meeting_id,
            "tenant_id": meeting.tenant_id,
            "lead_id": meeting.lead_id,
            "conversation_id": meeting.conversation_id,
            "event_id": meeting.event_id,
            "summary": meeting.summary,
            "description": meeting.description,
            "start_time": meeting.start_time.isoformat(),
            "end_time": meeting.end_time.isoformat(),
            "timezone": meeting.timezone,
            "attendee_name": meeting.attendee_name,
            "attendee_email": meeting.attendee_email,
            "attendee_phone": meeting.attendee_phone,
            "meet_link": meeting.meet_link,
            "status": meeting.status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        table.put_item(Item=item)
        return meeting

    def list_tenant_meetings(self, tenant_id: str) -> List[OutboundCalendarMeeting]:
        """List scheduled meetings for a tenant."""
        table = get_dynamodb_table()
        res = table.query(
            IndexName="GSI1",
            KeyConditionExpression="GSI1PK = :pk AND begins_with(GSI1SK, :sk)",
            ExpressionAttributeValues={
                ":pk": f"TENANT#{tenant_id}#MEETINGS",
                ":sk": "MEETING#",
            },
            ScanIndexForward=False
        )
        
        meetings = []
        for item in res.get("Items", []):
            meetings.append(
                OutboundCalendarMeeting(
                    meeting_id=item["meeting_id"],
                    tenant_id=item["tenant_id"],
                    lead_id=item["lead_id"],
                    conversation_id=item["conversation_id"],
                    event_id=item["event_id"],
                    summary=item["summary"],
                    description=item["description"],
                    start_time=datetime.fromisoformat(item["start_time"]),
                    end_time=datetime.fromisoformat(item["end_time"]),
                    timezone=item.get("timezone", "UTC"),
                    attendee_name=item["attendee_name"],
                    attendee_email=item["attendee_email"],
                    attendee_phone=item["attendee_phone"],
                    meet_link=item.get("meet_link"),
                    status=item.get("status", "BOOKED"),
                )
            )
        return meetings

    # ── PHONE TO CONVERSATION MAPPING ─────────────────────────────────

    def get_conversation_by_phone(self, tenant_id: str, phone: str) -> Optional[OutboundConversation]:
        """Look up active conversation by tenant_id and customer phone number."""
        table = get_dynamodb_table()
        pk = f"OB_PHONE_CONV#{tenant_id}#{phone}"
        sk = "METADATA"
        
        res = table.get_item(Key={"PK": pk, "SK": sk})
        item = res.get("Item")
        if not item:
            return None
            
        conv_id = item.get("conversation_id")
        if not conv_id:
            return None
            
        return self.get_conversation(conv_id)

    def link_phone_to_conversation(self, tenant_id: str, phone: str, conversation_id: str) -> None:
        """Create mapping linking customer phone to conversation ID."""
        table = get_dynamodb_table()
        pk = f"OB_PHONE_CONV#{tenant_id}#{phone}"
        sk = "METADATA"
        
        table.put_item(
            Item={
                "PK": pk,
                "SK": sk,
                "tenant_id": tenant_id,
                "phone": phone,
                "conversation_id": conversation_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    # ── LEAD TO CONVERSATION MAPPING ──────────────────────────────────

    def get_conversation_by_lead(self, lead_id: str) -> Optional[OutboundConversation]:
        """Look up active conversation by lead_id."""
        table = get_dynamodb_table()
        pk = f"OB_LEAD_CONV#{lead_id}"
        sk = "METADATA"
        
        res = table.get_item(Key={"PK": pk, "SK": sk})
        item = res.get("Item")
        if not item:
            return None
            
        conv_id = item.get("conversation_id")
        if not conv_id:
            return None
            
        return self.get_conversation(conv_id)

    def link_lead_to_conversation(self, lead_id: str, conversation_id: str) -> None:
        """Create mapping linking lead to conversation ID."""
        table = get_dynamodb_table()
        pk = f"OB_LEAD_CONV#{lead_id}"
        sk = "METADATA"
        
        table.put_item(
            Item={
                "PK": pk,
                "SK": sk,
                "lead_id": lead_id,
                "conversation_id": conversation_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    # ── ANALYTICS ─────────────────────────────────────────────────────

    def get_analytics(self, tenant_id: str) -> OutboundConversationAnalytics:
        """Retrieve aggregated outbound performance metrics."""
        table = get_dynamodb_table()
        pk = f"OB_ANALYTICS#{tenant_id}"
        sk = "METADATA"
        
        try:
            res = table.get_item(Key={"PK": pk, "SK": sk})
            item = res.get("Item")
            if not item:
                return OutboundConversationAnalytics(tenant_id=tenant_id)
                
            return OutboundConversationAnalytics(
                tenant_id=tenant_id,
                total_conversations=int(item.get("total_conversations", 0)),
                meetings_booked=int(item.get("meetings_booked", 0)),
                products_shared=int(item.get("products_shared", 0)),
                knowledge_searches=int(item.get("knowledge_searches", 0)),
                tool_usage=_from_decimal(item.get("tool_usage", {})),
                average_response_time=float(item.get("average_response_time", 0.0)),
                average_conversation_length=float(item.get("average_conversation_length", 0.0)),
                token_usage=int(item.get("token_usage", 0)),
                conversion_rate=float(item.get("conversion_rate", 0.0)),
            )
        except Exception:
            return OutboundConversationAnalytics(tenant_id=tenant_id)

    def save_analytics(self, analytics: OutboundConversationAnalytics) -> OutboundConversationAnalytics:
        """Save outbound performance metrics."""
        table = get_dynamodb_table()
        pk = f"OB_ANALYTICS#{analytics.tenant_id}"
        sk = "METADATA"
        
        item = {
            "PK": pk,
            "SK": sk,
            "tenant_id": analytics.tenant_id,
            "total_conversations": analytics.total_conversations,
            "meetings_booked": analytics.meetings_booked,
            "products_shared": analytics.products_shared,
            "knowledge_searches": analytics.knowledge_searches,
            "tool_usage": _to_decimal(analytics.tool_usage),
            "average_response_time": _to_decimal(analytics.average_response_time),
            "average_conversation_length": _to_decimal(analytics.average_conversation_length),
            "token_usage": analytics.token_usage,
            "conversion_rate": _to_decimal(analytics.conversion_rate),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        table.put_item(Item=item)
        return analytics


# Singleton repository instance
outbound_db_service = OutboundDBService()
