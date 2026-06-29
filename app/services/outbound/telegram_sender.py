"""Telegram Bot API sender for outbound campaigns.

This module handles sending messages via Telegram Bot API.
Later, this can be swapped for WhatsApp Business API.
"""

import httpx
from typing import Optional, Tuple

from app.core.config import get_settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)

settings = get_settings()

# Telegram Bot API base URL
TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramSender:
    """Sends messages via Telegram Bot API.

    Configuration:
    - TELEGRAM_BOT_TOKEN: Bot token from @BotFather
    - Messages are sent to phone numbers as chat_id (for testing,
      you'll use Telegram user chat IDs instead).

    For production WhatsApp migration: replace this class with
    a WhatsAppSender that implements the same interface.
    """

    def __init__(self):
        self.bot_token = getattr(settings, "TELEGRAM_BOT_TOKEN", None)

    def _get_api_url(self, method: str) -> str:
        return f"{TELEGRAM_API_BASE}/bot{self.bot_token}/{method}"

    async def send_message(
        self,
        chat_id: str,
        message: str,
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """Send a message via Telegram Bot API.

        Args:
            chat_id: Telegram chat ID (or phone-based lookup).
            message: The message text to send.

        Returns:
            (success, provider_message_id, error_message)
        """
        if not self.bot_token:
            logger.warning("TELEGRAM_BOT_TOKEN not configured — simulating send")
            # Simulate success for development/testing
            return True, f"sim_{chat_id}", None

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    self._get_api_url("sendMessage"),
                    json={
                        "chat_id": chat_id,
                        "text": message,
                        "parse_mode": "HTML",
                    },
                )

                data = response.json()

                if data.get("ok"):
                    msg_id = str(data["result"]["message_id"])
                    logger.info(f"Telegram message sent to {chat_id}: {msg_id}")
                    return True, msg_id, None
                else:
                    error = data.get("description", "Unknown Telegram API error")
                    logger.error(f"Telegram send failed to {chat_id}: {error}")
                    return False, None, error

        except httpx.TimeoutException:
            logger.error(f"Telegram send timeout for {chat_id}")
            return False, None, "Request timeout"
        except Exception as e:
            logger.error(f"Telegram send error for {chat_id}: {e}")
            return False, None, str(e)

    def render_template(self, template: str, lead_data: dict) -> str:
        """Render a message template with lead variables.

        Replaces {{name}}, {{phone}} etc. with actual values.
        """
        rendered = template
        for key, value in lead_data.items():
            placeholder = "{{" + key + "}}"
            rendered = rendered.replace(placeholder, str(value or ""))
        return rendered


# Singleton
telegram_sender = TelegramSender()
