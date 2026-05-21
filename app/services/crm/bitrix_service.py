"""Bitrix24 CRM integration service.

Pushes qualified leads to Bitrix24 CRM via the crm.lead.add REST API.
The webhook URL is configured globally via the BITRIX_WEBHOOK_URL environment variable.
All qualified leads from all tenants are automatically pushed.
"""

import os
from typing import Any, Dict, Optional

import httpx

from app.core.logging_config import get_logger

logger = get_logger(__name__)

# Global webhook URL — applies to ALL tenants
BITRIX_WEBHOOK_URL = os.environ.get("BITRIX_WEBHOOK_URL", "")


def push_lead_to_bitrix(
    company_name: str,
    name: Optional[str],
    phone: Optional[str],
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """Push a qualified lead to Bitrix24 CRM.

    Uses the global BITRIX_WEBHOOK_URL environment variable.
    If not configured, silently skips (no error raised).

    Args:
        company_name: Tenant company name for the lead title
        name: Lead's name
        phone: Lead's phone number
        metadata: Lead metadata containing qualification Q&A

    Returns:
        True if successfully created in Bitrix24, False otherwise.
    """
    if not BITRIX_WEBHOOK_URL:
        logger.debug("BITRIX_WEBHOOK_URL not configured — skipping CRM push")
        return False

    try:
        # Build the COMMENTS field with formatted Q&A
        comments = _format_comments(name, phone, metadata)

        # Split name into first/last if possible
        first_name = name or ""
        last_name = ""
        if name and " " in name:
            parts = name.strip().split(" ", 1)
            first_name = parts[0]
            last_name = parts[1]

        # Build Bitrix24 lead fields
        fields: Dict[str, Any] = {
            "TITLE": f"{company_name} Lead - {name or 'Unknown'}",
            "NAME": first_name,
            "LAST_NAME": last_name,
            "COMMENTS": comments,
            "SOURCE_DESCRIPTION": f"QBot Chat Widget - {company_name}",
        }

        # Add phone if available
        if phone:
            fields["PHONE"] = [{"VALUE": phone, "VALUE_TYPE": "MOBILE"}]

        url = BITRIX_WEBHOOK_URL.rstrip("/") + "/crm.lead.add"
        logger.info(f"Pushing lead to Bitrix24 for tenant: {company_name}")

        response = httpx.post(
            url,
            json={"fields": fields},
            headers={"Content-Type": "application/json"},
            timeout=10,
        )

        if response.status_code == 200:
            result = response.json()
            bitrix_lead_id = result.get("result")
            logger.info(f"✅ Lead pushed to Bitrix24. Lead ID: {bitrix_lead_id} | Tenant: {company_name}")
            return True
        else:
            logger.error(f"❌ Bitrix24 API error: {response.status_code} - {response.text}")
            return False

    except httpx.TimeoutException:
        logger.error("❌ Bitrix24 API timeout (10s)")
        return False
    except Exception as e:
        logger.error(f"❌ Bitrix24 push failed: {e}")
        return False


def _format_comments(
    name: Optional[str],
    phone: Optional[str],
    metadata: Optional[Dict[str, Any]],
) -> str:
    """Format lead data into a readable COMMENTS string for Bitrix24.

    Example output:
        Captured Data
        ━━━━━━━━━━━━━
        Name: Sam
        Phone: 7348098691

        What is your occupation?
        Student
    """
    lines = ["Captured Data", "━━━━━━━━━━━━━"]
    lines.append(f"Name: {name or 'Not provided'}")
    lines.append(f"Phone: {phone or 'Not provided'}")

    if metadata:
        for key, value in metadata.items():
            if key in ("name", "phone", "s3_lead_output", "s2_lead_output"):
                continue
            if isinstance(value, dict) and "label" in value and "answer" in value:
                lines.append("")
                lines.append(value["label"])
                lines.append(str(value["answer"]))

    return "\n".join(lines)
