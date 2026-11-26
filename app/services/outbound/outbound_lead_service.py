"""Outbound lead service — file parsing, phone normalization, deduplication."""

import csv
import io
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import phonenumbers
from botocore.exceptions import ClientError

from app.core.dynamodb import (
    get_dynamodb_table,
    put_item,
    update_item,
    delete_item,
    batch_write,
)
from app.models.outbound.outbound_lead import (
    OutboundLead,
    OutboundLeadCreate,
    OutboundLeadUpdate,
    OutboundLeadStatus,
    MessageStatus,
    UploadSummary,
)
from app.core.logging_config import get_logger

logger = get_logger(__name__)

# --- Column detection patterns ---
NAME_PATTERNS = [
    "name", "full_name", "fullname", "first_name", "firstname",
    "contact_name", "contactname", "lead_name", "leadname",
    "customer_name", "customername", "person", "customer",
]
PHONE_PATTERNS = [
    "phone", "phone_number", "phonenumber", "mobile", "mobile_number",
    "mobilenumber", "contact", "contact_number", "contactnumber",
    "number", "whatsapp", "whatsapp_number", "cell", "cellphone",
    "telephone", "tel",
]

DEFAULT_COUNTRY_CODE = "IN"


class OutboundLeadService:
    """Handles lead file parsing, validation, normalization, and DynamoDB CRUD."""

    # ── Column Detection ──────────────────────────────────────────────

    def _detect_column(self, headers: List[str], patterns: List[str]) -> Optional[str]:
        """Detect which header matches a set of known patterns."""
        normalized = {h: h.strip().lower().replace(" ", "_").replace("-", "_") for h in headers}
        for header, norm in normalized.items():
            if norm in patterns:
                return header
        # Partial match fallback
        for header, norm in normalized.items():
            for pattern in patterns:
                if pattern in norm or norm in pattern:
                    return header
        return None

    # ── Phone Normalization ───────────────────────────────────────────

    def normalize_phone(self, raw: str, default_region: str = DEFAULT_COUNTRY_CODE) -> Tuple[Optional[str], Optional[str]]:
        """Normalize a phone number to E.164 format.

        Returns (normalized_phone, error_reason).
        On success: ("+919876543210", None)
        On failure: (None, "Invalid phone number")
        """
        if not raw or not raw.strip():
            return None, "Empty phone number"

        cleaned = re.sub(r"[\s\-\(\)\.]", "", raw.strip())

        try:
            parsed = phonenumbers.parse(cleaned, default_region)
            if phonenumbers.is_valid_number(parsed):
                formatted = phonenumbers.format_number(
                    parsed, phonenumbers.PhoneNumberFormat.E164
                )
                return formatted, None
            else:
                return None, f"Invalid phone number: {raw}"
        except phonenumbers.NumberParseException:
            return None, f"Cannot parse phone number: {raw}"

    # ── File Parsing ──────────────────────────────────────────────────

    def parse_csv(self, content: str) -> Tuple[List[str], List[List[str]]]:
        """Parse CSV content into headers and rows."""
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        if not rows:
            return [], []
        return rows[0], rows[1:]

    def parse_txt(self, content: str) -> Tuple[List[str], List[List[str]]]:
        """Parse TXT content (tab or comma separated)."""
        lines = content.strip().split("\n")
        if not lines:
            return [], []

        # Detect delimiter
        first_line = lines[0]
        if "\t" in first_line:
            delimiter = "\t"
        elif "," in first_line:
            delimiter = ","
        else:
            # Single column — assume phone numbers only
            return ["phone"], [[line.strip()] for line in lines if line.strip()]

        reader = csv.reader(io.StringIO(content), delimiter=delimiter)
        rows = list(reader)
        if not rows:
            return [], []
        return rows[0], rows[1:]

    def parse_xlsx(self, file_bytes: bytes) -> Tuple[List[str], List[List[str]]]:
        """Parse XLSX content into headers and rows."""
        try:
            import openpyxl
        except ImportError:
            raise ImportError("openpyxl is required for XLSX support. Install with: pip install openpyxl")

        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True)
        ws = wb.active

        rows = []
        for row in ws.iter_rows(values_only=True):
            rows.append([str(cell) if cell is not None else "" for cell in row])

        wb.close()

        if not rows:
            return [], []
        return rows[0], rows[1:]

    # ── Full Upload Processing Pipeline ───────────────────────────────

    def process_upload(
        self,
        campaign_id: str,
        tenant_id: str,
        filename: str,
        file_content: bytes,
    ) -> Tuple[UploadSummary, List[Dict], List[Dict]]:
        """Process an uploaded lead file end-to-end.

        Returns: (summary, invalid_leads, duplicate_leads)
        """
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

        if ext == "csv":
            headers, rows = self.parse_csv(file_content.decode("utf-8", errors="replace"))
        elif ext == "txt":
            headers, rows = self.parse_txt(file_content.decode("utf-8", errors="replace"))
        elif ext in ("xlsx", "xls"):
            headers, rows = self.parse_xlsx(file_content)
        else:
            raise ValueError(f"Unsupported file type: .{ext}. Supported: CSV, XLSX, TXT")

        if not headers or not rows:
            raise ValueError("File is empty or has no data rows")

        # Detect columns
        name_col = self._detect_column(headers, NAME_PATTERNS)
        phone_col = self._detect_column(headers, PHONE_PATTERNS)

        if not phone_col:
            raise ValueError(
                f"Could not detect phone number column. Headers found: {headers}. "
                f"Expected one of: {', '.join(PHONE_PATTERNS[:5])}"
            )

        name_idx = headers.index(name_col) if name_col else None
        phone_idx = headers.index(phone_col)

        # Process rows
        valid_leads: List[OutboundLeadCreate] = []
        invalid_leads: List[Dict] = []
        duplicate_leads: List[Dict] = []
        seen_phones = set()

        for row in rows:
            if not row or len(row) <= phone_idx:
                continue

            raw_phone = row[phone_idx].strip()
            name = row[name_idx].strip() if name_idx is not None and len(row) > name_idx else None

            if not raw_phone:
                continue

            # Normalize phone
            normalized, error = self.normalize_phone(raw_phone)

            if error:
                invalid_leads.append({
                    "name": name or "",
                    "phone": raw_phone,
                    "reason": error,
                })
                continue

            # Check duplicates
            if normalized in seen_phones:
                duplicate_leads.append({
                    "name": name or "",
                    "phone": raw_phone,
                    "normalized_phone": normalized,
                    "reason": "Duplicate phone number",
                })
                continue

            seen_phones.add(normalized)
            valid_leads.append(OutboundLeadCreate(
                name=name,
                phone=normalized,
                raw_phone=raw_phone,
                status=OutboundLeadStatus.VALID,
            ))

        # Store valid leads in DynamoDB
        self._batch_store_leads(campaign_id, tenant_id, valid_leads)

        summary = UploadSummary(
            total_uploaded=len(rows),
            valid=len(valid_leads),
            invalid=len(invalid_leads),
            duplicates=len(duplicate_leads),
            detected_name_column=name_col,
            detected_phone_column=phone_col,
            original_filename=filename,
        )

        logger.info(
            f"Upload processed for campaign {campaign_id}: "
            f"{summary.valid} valid, {summary.invalid} invalid, {summary.duplicates} duplicates"
        )

        return summary, invalid_leads, duplicate_leads

    # ── DynamoDB Lead Storage ─────────────────────────────────────────

    def _batch_store_leads(
        self, campaign_id: str, tenant_id: str, leads: List[OutboundLeadCreate]
    ) -> None:
        """Batch write valid leads to DynamoDB."""
        now = datetime.now(timezone.utc).isoformat()
        items = []

        for lead in leads:
            lead_id = str(uuid.uuid4())
            items.append({
                "PK": f"OB_CAMPAIGN#{campaign_id}",
                "SK": f"LEAD#{lead_id}",
                "GSI1PK": f"OB_PHONE#{lead.phone}",
                "GSI1SK": f"CAMPAIGN#{campaign_id}",
                "GSI2PK": f"OB_LEAD#{campaign_id}#{MessageStatus.PENDING.value}",
                "GSI2SK": f"LEAD#{now}",
                "lead_id": lead_id,
                "campaign_id": campaign_id,
                "tenant_id": tenant_id,
                "name": lead.name or "",
                "phone": lead.phone,
                "raw_phone": lead.raw_phone or lead.phone,
                "status": lead.status.value,
                "message_status": MessageStatus.PENDING.value,
                "retry_count": 0,
                "created_at": now,
            })

        # Batch write in chunks of 25 (DynamoDB limit)
        for i in range(0, len(items), 25):
            batch_write(items[i : i + 25])

    def list_leads(
        self,
        campaign_id: str,
        status: Optional[str] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[OutboundLead]:
        """List leads for a campaign with optional filtering."""
        table = get_dynamodb_table()

        response = table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={
                ":pk": f"OB_CAMPAIGN#{campaign_id}",
                ":sk": "LEAD#",
            },
            ScanIndexForward=True,
        )

        items = response.get("Items", [])

        # Handle pagination for large datasets
        while "LastEvaluatedKey" in response:
            response = table.query(
                KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
                ExpressionAttributeValues={
                    ":pk": f"OB_CAMPAIGN#{campaign_id}",
                    ":sk": "LEAD#",
                },
                ScanIndexForward=True,
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            items.extend(response.get("Items", []))

        # Apply filters
        if status:
            items = [i for i in items if i.get("message_status") == status]

        if search:
            search_lower = search.lower()
            items = [
                i for i in items
                if search_lower in (i.get("name", "") or "").lower()
                or search_lower in (i.get("phone", "") or "").lower()
            ]

        # Paginate
        paginated = items[skip : skip + limit]

        return [self._item_to_lead(item) for item in paginated]

    def count_leads(self, campaign_id: str) -> int:
        """Count total leads for a campaign."""
        table = get_dynamodb_table()

        total = 0
        params = {
            "Select": "COUNT",
            "KeyConditionExpression": "PK = :pk AND begins_with(SK, :sk)",
            "ExpressionAttributeValues": {
                ":pk": f"OB_CAMPAIGN#{campaign_id}",
                ":sk": "LEAD#",
            },
        }

        while True:
            response = table.query(**params)
            total += response.get("Count", 0)
            if "LastEvaluatedKey" in response:
                params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
            else:
                break

        return total

    def get_lead(self, campaign_id: str, lead_id: str) -> Optional[OutboundLead]:
        """Get a single lead."""
        table = get_dynamodb_table()
        response = table.get_item(
            Key={"PK": f"OB_CAMPAIGN#{campaign_id}", "SK": f"LEAD#{lead_id}"}
        )
        item = response.get("Item")
        if item:
            return self._item_to_lead(item)
        return None

    def update_lead(
        self, campaign_id: str, lead_id: str, data: OutboundLeadUpdate
    ) -> Optional[OutboundLead]:
        """Update a lead's name or phone."""
        lead = self.get_lead(campaign_id, lead_id)
        if not lead:
            return None

        updates: Dict[str, Any] = {}
        if data.name is not None:
            updates["name"] = data.name
        if data.phone is not None:
            normalized, error = self.normalize_phone(data.phone)
            if error:
                raise ValueError(error)
            updates["phone"] = normalized
            updates["raw_phone"] = data.phone
            updates["GSI1PK"] = f"OB_PHONE#{normalized}"
            updates["GSI1SK"] = f"CAMPAIGN#{campaign_id}"

        if updates:
            table = get_dynamodb_table()
            expr_parts = []
            attr_names = {}
            attr_values = {}
            for i, (key, val) in enumerate(updates.items()):
                attr_names[f"#a{i}"] = key
                attr_values[f":v{i}"] = val
                expr_parts.append(f"#a{i} = :v{i}")

            table.update_item(
                Key={"PK": f"OB_CAMPAIGN#{campaign_id}", "SK": f"LEAD#{lead_id}"},
                UpdateExpression="SET " + ", ".join(expr_parts),
                ExpressionAttributeNames=attr_names,
                ExpressionAttributeValues=attr_values,
            )

        return self.get_lead(campaign_id, lead_id)

    def delete_lead(self, campaign_id: str, lead_id: str) -> bool:
        """Delete a single lead."""
        table = get_dynamodb_table()
        table.delete_item(
            Key={"PK": f"OB_CAMPAIGN#{campaign_id}", "SK": f"LEAD#{lead_id}"}
        )
        return True

    def bulk_delete_leads(self, campaign_id: str, lead_ids: List[str]) -> int:
        """Bulk delete leads from a campaign."""
        table = get_dynamodb_table()
        deleted = 0
        with table.batch_writer() as batch:
            for lead_id in lead_ids:
                batch.delete_item(
                    Key={"PK": f"OB_CAMPAIGN#{campaign_id}", "SK": f"LEAD#{lead_id}"}
                )
                deleted += 1
        return deleted

    def claim_lead_for_sending(self, campaign_id: str, lead_id: str) -> bool:
        """Atomically move a lead PENDING -> SENDING. False if another run already took it.

        Guarantees a lead is messaged at most once even if two executors overlap
        (e.g. pause/resume while a batch is in flight, or a redelivered SQS message).
        """
        table = get_dynamodb_table()
        try:
            table.update_item(
                Key={"PK": f"OB_CAMPAIGN#{campaign_id}", "SK": f"LEAD#{lead_id}"},
                UpdateExpression="SET #ms = :sending",
                ConditionExpression="#ms = :pending",
                ExpressionAttributeNames={"#ms": "message_status"},
                ExpressionAttributeValues={
                    ":sending": MessageStatus.SENDING.value,
                    ":pending": MessageStatus.PENDING.value,
                },
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def mark_replied(self, campaign_id: str, lead_id: str) -> bool:
        """Record the lead's first reply. True only the first time (so reply counters count leads once)."""
        table = get_dynamodb_table()
        try:
            table.update_item(
                Key={"PK": f"OB_CAMPAIGN#{campaign_id}", "SK": f"LEAD#{lead_id}"},
                UpdateExpression="SET replied_at = :now",
                ConditionExpression="attribute_exists(PK) AND attribute_not_exists(replied_at)",
                ExpressionAttributeValues={":now": datetime.now(timezone.utc).isoformat()},
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def update_lead_message_status(
        self, campaign_id: str, lead_id: str, status: MessageStatus, error_reason: str = None
    ) -> None:
        """Update the message delivery status on a lead."""
        table = get_dynamodb_table()
        updates = {"message_status": status.value}
        now = datetime.now(timezone.utc).isoformat()
        if status == MessageStatus.SENT:
            updates["sent_at"] = now
        elif status == MessageStatus.DELIVERED:
            updates["delivered_at"] = now
        elif status == MessageStatus.READ:
            updates["read_at"] = now
        if error_reason:
            updates["error_reason"] = error_reason

        expr_parts = []
        attr_names = {}
        attr_values = {}
        for i, (key, val) in enumerate(updates.items()):
            attr_names[f"#a{i}"] = key
            attr_values[f":v{i}"] = val
            expr_parts.append(f"#a{i} = :v{i}")

        table.update_item(
            Key={"PK": f"OB_CAMPAIGN#{campaign_id}", "SK": f"LEAD#{lead_id}"},
            UpdateExpression="SET " + ", ".join(expr_parts),
            ExpressionAttributeNames=attr_names,
            ExpressionAttributeValues=attr_values,
        )

    def get_pending_leads(self, campaign_id: str, limit: int = 100) -> List[OutboundLead]:
        """Get leads with PENDING message status for campaign execution."""
        table = get_dynamodb_table()

        response = table.query(
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            FilterExpression="#ms = :pending",
            ExpressionAttributeNames={"#ms": "message_status"},
            ExpressionAttributeValues={
                ":pk": f"OB_CAMPAIGN#{campaign_id}",
                ":sk": "LEAD#",
                ":pending": MessageStatus.PENDING.value,
            },
            Limit=limit,
        )

        return [self._item_to_lead(item) for item in response.get("Items", [])]

    def generate_invalid_csv(self, invalid_leads: List[Dict]) -> str:
        """Generate CSV content for invalid leads."""
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["Name", "Phone", "Reason"])
        for lead in invalid_leads:
            writer.writerow([lead.get("name", ""), lead.get("phone", ""), lead.get("reason", "")])
        output.seek(0)
        return output.getvalue()

    def generate_duplicate_csv(self, duplicate_leads: List[Dict]) -> str:
        """Generate CSV content for duplicate leads."""
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["Name", "Phone", "Normalized Phone", "Reason"])
        for lead in duplicate_leads:
            writer.writerow([
                lead.get("name", ""),
                lead.get("phone", ""),
                lead.get("normalized_phone", ""),
                lead.get("reason", ""),
            ])
        output.seek(0)
        return output.getvalue()

    def _item_to_lead(self, item: Dict[str, Any]) -> OutboundLead:
        """Convert DynamoDB item to OutboundLead model."""
        created_at = item.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)

        sent_at = item.get("sent_at")
        if isinstance(sent_at, str):
            sent_at = datetime.fromisoformat(sent_at)

        def _dt(key):
            val = item.get(key)
            return datetime.fromisoformat(val) if isinstance(val, str) else val

        return OutboundLead(
            delivered_at=_dt("delivered_at"),
            read_at=_dt("read_at"),
            replied_at=_dt("replied_at"),
            lead_id=item["lead_id"],
            campaign_id=item["campaign_id"],
            tenant_id=item["tenant_id"],
            name=item.get("name"),
            phone=item["phone"],
            raw_phone=item.get("raw_phone"),
            status=OutboundLeadStatus(item.get("status", "VALID")),
            message_status=MessageStatus(item.get("message_status", "PENDING")),
            error_reason=item.get("error_reason"),
            retry_count=int(item.get("retry_count", 0)),
            created_at=created_at,
            sent_at=sent_at,
        )


# Singleton
outbound_lead_service = OutboundLeadService()
