"""WhatsApp Cloud API sender for outbound campaigns and AI replies.

Supports text, approved template, image and document messages, plus listing a
WhatsApp Business Account's approved templates.
"""

import asyncio
import os
import re
from typing import Any, Dict, List, Optional

import httpx
import phonenumbers

from app.utils.encryption import decrypt_secret
from app.core.logging_config import get_logger

logger = get_logger(__name__)

GRAPH_API_VERSION = os.environ.get("WHATSAPP_GRAPH_API_VERSION", "v23.0")
GRAPH_BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
MAX_RETRIES = 3

# Human-readable explanations for the Meta error codes campaigns hit most often.
META_ERROR_HINTS = {
    131047: "More than 24 hours since the customer last replied - use an approved template.",
    131026: "Message undeliverable (number not on WhatsApp, or customer can't receive it).",
    131049: "Meta limited marketing messages to this user (per-user marketing limit).",
    131050: "The customer stopped marketing messages from this business.",
    131051: "Unsupported message type.",
    131052: "Media could not be downloaded from the URL.",
    131053: "Media upload failed - check the file type and that the URL is public.",
    132000: "Template parameter count doesn't match the template.",
    132001: "Template doesn't exist in this language or isn't approved.",
    132005: "Template text too long after filling parameters.",
    132007: "Template content violates WhatsApp policy.",
    132012: "Template parameter format mismatch.",
    133010: "Phone number not registered with the Cloud API.",
    190: "Access token expired or invalid - generate a new token in the client's WhatsApp settings.",
    200: "The system user lacks permission on this WhatsApp account.",
    100: "Invalid parameter (check the phone number ID and recipient).",
}


def explain_meta_error(code: Optional[int], fallback: str = "") -> str:
    if code is None:
        return fallback
    hint = META_ERROR_HINTS.get(int(code))
    return f"{hint} (code {code})" if hint else (fallback or f"WhatsApp error code {code}")


class WhatsAppSender:
    """Sends messages via WhatsApp Cloud API (or 360dialog when configured)."""

    def normalize_phone(self, phone_str: str, default_region: str = "IN") -> Optional[str]:
        """Normalize a phone number to E.164 format.

        Examples:
        - 9876543210 -> +919876543210
        - 919876543210 -> +919876543210
        - +919876543210 -> +919876543210
        """
        try:
            # Strip spaces, dashes, and basic cleanup
            cleaned = phone_str.strip()
            if not cleaned:
                return None

            # If it doesn't start with a plus sign, apply preprocessing based on length
            digits_only = "".join(c for c in cleaned if c.isdigit())
            has_plus = cleaned.startswith("+")

            if not has_plus:
                if len(digits_only) == 10:
                    cleaned = "+91" + digits_only
                elif len(digits_only) == 12 and digits_only.startswith("91"):
                    cleaned = "+" + digits_only
                else:
                    cleaned = "+" + digits_only
            else:
                cleaned = "+" + digits_only

            parsed = phonenumbers.parse(cleaned, None)
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)

            # Fallback to parsing with default region
            parsed_fallback = phonenumbers.parse(phone_str, default_region)
            if phonenumbers.is_valid_number(parsed_fallback):
                return phonenumbers.format_number(parsed_fallback, phonenumbers.PhoneNumberFormat.E164)
        except Exception as e:
            logger.warning(f"Error normalizing phone number '{phone_str}': {e}")
        return None

    # ── Credentials ────────────────────────────────────────────────────

    def _endpoint(self, tenant_config) -> Dict[str, Any]:
        """Resolve where and how to send: 360dialog, Meta Cloud API, or simulation."""
        d360_key = os.environ.get("D360_API_KEY") or getattr(tenant_config, "d360_api_key", None)
        d360_url = os.environ.get("D360_API_URL") or getattr(tenant_config, "d360_api_url", None)
        if d360_key:
            base_url = d360_url.rstrip("/") if d360_url else "https://waba-sandbox.360dialog.io/v1"
            url = base_url if base_url.endswith("/messages") else f"{base_url}/messages"
            return {
                "mode": "360dialog",
                "url": url,
                "headers": {"D360-API-KEY": d360_key, "Authorization": f"Bearer {d360_key}", "Content-Type": "application/json"},
            }

        enabled = getattr(tenant_config, "whatsapp_enabled", False)
        token_encrypted = getattr(tenant_config, "whatsapp_access_token", None)
        phone_number_id = getattr(tenant_config, "whatsapp_phone_number_id", None)
        if not enabled or not token_encrypted or not phone_number_id:
            return {"mode": "simulation"}

        try:
            token = decrypt_secret(token_encrypted)
        except Exception as e:
            logger.error(f"Failed to decrypt WhatsApp access token: {e}")
            return {"mode": "error", "error": f"Decryption failed: {str(e)}"}

        return {
            "mode": "meta",
            "url": f"{GRAPH_BASE_URL}/{phone_number_id}/messages",
            "token": token,
            "headers": {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        }

    # ── Core send with retries ─────────────────────────────────────────

    async def _send(self, to_phone: str, body: Dict[str, Any], tenant_config, kind: str) -> Dict[str, Any]:
        endpoint = self._endpoint(tenant_config)
        if endpoint["mode"] == "simulation":
            logger.info(f"[WhatsApp] Simulation mode active ({kind} not sent)")
            return {"status": "simulated", "provider": "whatsapp"}
        if endpoint["mode"] == "error":
            return {"status": "failed", "provider": "whatsapp", "error": endpoint["error"]}

        payload = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to_phone, **body}
        last_error = "All retry attempts failed"
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.post(endpoint["url"], headers=endpoint["headers"], json=payload)

                if response.status_code == 200:
                    data = response.json()
                    messages = data.get("messages", [])
                    msg_id = messages[0].get("id") if messages else None
                    logger.info(f"WhatsApp {kind} successfully sent to {to_phone} (ID: {msg_id})")
                    return {"status": "sent", "provider": "whatsapp", "message_id": msg_id}

                # 5xx and rate limits are transient
                if response.status_code >= 500 or response.status_code == 429:
                    logger.warning(f"Transient WhatsApp API error ({response.status_code}) on attempt {attempt}/{MAX_RETRIES}.")
                    last_error = f"WhatsApp API error: {response.status_code}"
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(2 ** attempt)
                        continue

                # Never log request headers (token); status and body only
                error_code, error_text = self._parse_error(response)
                logger.error(f"WhatsApp API error response (status {response.status_code}): {response.text[:500]}")
                return {
                    "status": "failed",
                    "provider": "whatsapp",
                    "error_code": error_code,
                    "error": explain_meta_error(error_code, error_text or f"WhatsApp API error {response.status_code}"),
                }

            except httpx.TimeoutException as e:
                logger.warning(f"WhatsApp API timeout on attempt {attempt}/{MAX_RETRIES}: {e}")
                last_error = "Request timeout"
            except Exception as e:
                logger.error(f"WhatsApp send exception on attempt {attempt}/{MAX_RETRIES}: {e}")
                last_error = str(e)
            if attempt < MAX_RETRIES:
                await asyncio.sleep(2 ** attempt)

        return {"status": "failed", "provider": "whatsapp", "error": last_error}

    @staticmethod
    def _parse_error(response) -> tuple:
        try:
            err = response.json().get("error", {})
            code = err.get("code")
            details = (err.get("error_data") or {}).get("details") or err.get("message") or ""
            return (int(code) if code is not None else None), details
        except Exception:
            return None, response.text[:200]

    # ── Message types ──────────────────────────────────────────────────

    async def send_message(self, to_phone: str, message: str, tenant_config) -> Dict[str, Any]:
        """Free-form text. Only delivered within 24h of the customer's last message."""
        return await self._send(to_phone, {"type": "text", "text": {"body": message}}, tenant_config, "text")

    async def send_template(
        self,
        to_phone: str,
        template_name: str,
        language: str,
        tenant_config,
        body_params: Optional[List[str]] = None,
        header_params: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Approved template message (works for cold outreach, outside the 24h window)."""
        components = []
        if header_params:
            components.append({"type": "header", "parameters": [{"type": "text", "text": p} for p in header_params]})
        if body_params:
            components.append({"type": "body", "parameters": [{"type": "text", "text": (p or "-")} for p in body_params]})
        template: Dict[str, Any] = {"name": template_name, "language": {"code": language}}
        if components:
            template["components"] = components
        return await self._send(to_phone, {"type": "template", "template": template}, tenant_config, "template")

    async def send_image(self, to_phone: str, link: str, tenant_config, caption: str = "") -> Dict[str, Any]:
        image: Dict[str, Any] = {"link": link}
        if caption:
            image["caption"] = caption[:1000]
        return await self._send(to_phone, {"type": "image", "image": image}, tenant_config, "image")

    async def send_document(self, to_phone: str, link: str, tenant_config,
                            filename: str = "", caption: str = "") -> Dict[str, Any]:
        document: Dict[str, Any] = {"link": link}
        if filename:
            document["filename"] = filename[:240]
        if caption:
            document["caption"] = caption[:1000]
        return await self._send(to_phone, {"type": "document", "document": document}, tenant_config, "document")

    # ── Templates ──────────────────────────────────────────────────────

    async def list_templates(self, tenant_config) -> Dict[str, Any]:
        """Approved templates of the tenant's WhatsApp Business Account."""
        waba_id = getattr(tenant_config, "whatsapp_business_account_id", None)
        if not waba_id:
            return {"templates": [], "error": "Add the WhatsApp Business Account ID in the client's WhatsApp settings."}
        endpoint = self._endpoint(tenant_config)
        if endpoint["mode"] != "meta":
            return {"templates": [], "error": "WhatsApp is not enabled or configured for this client."}

        url = f"{GRAPH_BASE_URL}/{waba_id}/message_templates"
        params = {"fields": "name,status,language,category,components", "limit": 200}
        templates: List[Dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=30) as client:
            while url:
                response = await client.get(url, headers={"Authorization": endpoint["headers"]["Authorization"]}, params=params)
                if response.status_code != 200:
                    code, text = self._parse_error(response)
                    return {"templates": [], "error": explain_meta_error(code, text)}
                data = response.json()
                templates.extend(data.get("data", []))
                url = (data.get("paging") or {}).get("next")
                params = None  # the "next" URL already carries the query

        return {"templates": [self._summarize_template(t) for t in templates if t.get("status") == "APPROVED"]}

    @staticmethod
    def _summarize_template(t: Dict[str, Any]) -> Dict[str, Any]:
        body, header_text, header_format, footer, buttons = "", "", None, "", []
        for c in t.get("components", []):
            ctype = c.get("type")
            if ctype == "BODY":
                body = c.get("text", "")
            elif ctype == "HEADER":
                header_format = c.get("format")
                header_text = c.get("text", "") if header_format == "TEXT" else ""
            elif ctype == "FOOTER":
                footer = c.get("text", "")
            elif ctype == "BUTTONS":
                buttons = [b.get("text", "") for b in c.get("buttons", [])]
        body_vars = len(set(re.findall(r"\{\{(\d+)\}\}", body)))
        header_vars = len(set(re.findall(r"\{\{(\d+)\}\}", header_text)))
        supported = header_format in (None, "TEXT") and not re.search(r"\{\{[a-zA-Z_]", body)
        return {
            "name": t.get("name"),
            "language": t.get("language"),
            "category": t.get("category"),
            "body": body,
            "header_text": header_text,
            "header_format": header_format,
            "footer": footer,
            "buttons": buttons,
            "body_param_count": body_vars,
            "header_param_count": header_vars,
            "supported": supported,
            "unsupported_reason": "" if supported else "Templates with image/video/document headers or named variables aren't supported yet.",
        }

    def render_template(self, template: str, lead_data: dict) -> str:
        """Render a message template with lead variables.

        Replaces {{name}}, {{phone}} etc. with actual values.
        """
        rendered = template
        for key, value in lead_data.items():
            placeholder = "{{" + key + "}}"
            rendered = rendered.replace(placeholder, str(value or ""))
        return rendered

    @staticmethod
    def fill_numbered_template(text: str, params: List[str]) -> str:
        """Fill {{1}}, {{2}}... in a template body (for previews and conversation context)."""
        def repl(m):
            idx = int(m.group(1)) - 1
            return params[idx] if 0 <= idx < len(params) else m.group(0)
        return re.sub(r"\{\{(\d+)\}\}", repl, text or "")


# Singleton instance
whatsapp_sender = WhatsAppSender()
