"""WhatsApp Cloud API sender for outbound campaigns.

This module handles sending messages via the WhatsApp Business Cloud API.
"""

import httpx
import asyncio
import phonenumbers
from typing import Optional, Tuple, Dict, Any

from app.utils.encryption import decrypt_secret
from app.core.logging_config import get_logger

logger = get_logger(__name__)

# Shared HTTP client reused across all sends. A single connection pool avoids
# the cost of establishing a new TLS connection on every message and bounds the
# total number of concurrent outbound connections.
_http_client = httpx.AsyncClient(
    timeout=30,
    limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
)


class WhatsAppSender:
    """Sends messages via WhatsApp Cloud API."""

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

    async def send_message(
        self,
        to_phone: str,
        message: str,
        tenant_config,
    ) -> Dict[str, Any]:
        """Send a message using the WhatsApp Cloud API.

        If configuration is missing or WhatsApp is disabled, runs in Simulation Mode.
        """
        # Validate configuration
        enabled = getattr(tenant_config, "whatsapp_enabled", False)
        access_token_encrypted = getattr(tenant_config, "whatsapp_access_token", None)
        phone_number_id = getattr(tenant_config, "whatsapp_phone_number_id", None)

        if not enabled or not access_token_encrypted or not phone_number_id:
            logger.info("[WhatsApp] Simulation mode active")
            return {
                "status": "simulated",
                "provider": "whatsapp"
            }

        # Decrypt token
        try:
            access_token = decrypt_secret(access_token_encrypted)
        except Exception as e:
            logger.error(f"Failed to decrypt WhatsApp access token: {e}")
            return {
                "status": "failed",
                "provider": "whatsapp",
                "error": f"Decryption failed: {str(e)}"
            }

        url = f"https://graph.facebook.com/v23.0/{phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "text",
            "text": {
                "body": message
            }
        }

        # Try sending with retries for transient failures (exponential backoff)
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                # Reuse the shared module-level client (connection pooling).
                response = await _http_client.post(url, headers=headers, json=payload)

                if response.status_code == 200:
                    data = response.json()
                    messages = data.get("messages", [])
                    msg_id = messages[0].get("id") if messages else None
                    logger.info(f"WhatsApp message successfully sent to {to_phone} (ID: {msg_id})")
                    return {
                        "status": "sent",
                        "provider": "whatsapp",
                        "message_id": msg_id
                    }

                # 5xx status codes are transient and can be retried
                if response.status_code >= 500:
                    logger.warning(
                        f"Transient WhatsApp API error (status {response.status_code}) on attempt {attempt}/{max_retries}. Retrying..."
                    )
                    if attempt < max_retries:
                        await asyncio.sleep(2 ** attempt)
                        continue

                # Non-transient error or retries exhausted
                error_data = response.text
                # Never log the request headers/authorization token, only status and body
                logger.error(f"WhatsApp API error response (status {response.status_code}): {error_data}")
                return {
                    "status": "failed",
                    "provider": "whatsapp",
                    "error": f"WhatsApp API error: {response.status_code} - {error_data}"
                }

            except httpx.TimeoutException as e:
                logger.warning(f"WhatsApp API timeout on attempt {attempt}/{max_retries}: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return {
                    "status": "failed",
                    "provider": "whatsapp",
                    "error": "Request timeout"
                }
            except Exception as e:
                logger.error(f"WhatsApp send exception on attempt {attempt}/{max_retries}: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return {
                    "status": "failed",
                    "provider": "whatsapp",
                    "error": str(e)
                }

        return {
            "status": "failed",
            "provider": "whatsapp",
            "error": "All retry attempts failed"
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


# Singleton instance
whatsapp_sender = WhatsAppSender()
