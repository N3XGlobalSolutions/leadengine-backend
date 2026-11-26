"""Safety and formatting validator for outgoing WhatsApp messages."""

import re
from typing import List, Optional
from app.services.ai.outbound.conversation_pipeline import OutboundAISession
from app.services.ai.outbound.agent_state import extract_state
from app.core.logging_config import get_logger

logger = get_logger(__name__)

# Real profanity only. Business words like "scam", "spam" or "fraud" are excluded:
# a legitimate reply such as "this is not spam" must not be replaced.
PROFANITY_BLACKLIST = {
    "fuck", "fucking", "f**k", "shit", "bitch", "asshole", "bastard", "motherfucker"
}

EMPTY_REPLY_FALLBACK = "I'm here to help. Could you please clarify your question?"
PROFANITY_FALLBACK = "Our goal is to assist you with the best solutions. How can we help you move forward?"

# WhatsApp text messages are limited to 4096 characters.
WHATSAPP_MAX_CHARS = 4000
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
ALWAYS_ALLOWED_DOMAINS = ("qbotglobal.com",)


def _truncate_at_boundary(text: str, limit: int) -> str:
    """Cut at the last sentence or line break before `limit`, never mid-word."""
    if len(text) <= limit:
        return text
    window = text[:limit]
    cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "), window.rfind("\n"))
    if cut >= limit * 0.5:
        return window[:cut + 1].rstrip()
    space = window.rfind(" ")
    return (window[:space] if space > 0 else window).rstrip() + "…"


def _allowed_urls(session: OutboundAISession) -> List[str]:
    """URLs the agent may legitimately share: catalog assets, knowledge-base text, booking results."""
    allowed: List[str] = []
    for p in session.selected_products:
        if p.brochure_pdf:
            allowed.append(p.brochure_pdf)
        allowed.extend(p.images)
        allowed.extend(p.gallery)
    for item in session.retrieved_knowledge:
        allowed.extend(URL_RE.findall(item.get("text", "") or ""))
    for output in session.tool_outputs:
        result = output.get("result") or {}
        if isinstance(result, dict) and result.get("meet_link"):
            allowed.append(result["meet_link"])
    settings = session.agent_settings
    if settings and settings.agent_instructions:
        allowed.extend(URL_RE.findall(settings.agent_instructions))
    return [u.rstrip(".,") for u in allowed if u]


class ResponseValidator:
    """Post-processing validation checking safety, length, and format of AI responses."""

    def validate_response(self, session: OutboundAISession, text: Optional[str]) -> str:
        """Validate, sanitize, and format the reply before it is stored and sent."""
        # Never leak the machine-readable state line.
        text, _ = extract_state(text or "")

        # 1. Fallback for empty replies
        if not text or not text.strip():
            logger.warning("[ResponseValidator] Received empty response from model.")
            return EMPTY_REPLY_FALLBACK

        cleaned = text.strip()

        # 2. Clean up unsupported markdown formatting (WhatsApp compat)
        # WhatsApp supports *bold*, _italics_, ~strikethrough~ and ```monospace```,
        # but not headers, [text](url) links or HTML tags.
        cleaned = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", cleaned)
        cleaned = re.sub(r"\[(Source|source|doc|Document)\s*\d*\]", "", cleaned)
        cleaned = re.sub(r"\[\d+\]", "", cleaned)
        cleaned = cleaned.replace("[", "").replace("]", "")
        cleaned = re.sub(r"<[^>]+>", "", cleaned)
        cleaned = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", cleaned)   # markdown headings
        cleaned = re.sub(r"\*\*(.+?)\*\*", r"*\1*", cleaned)      # **bold** -> *bold*

        # 3. Hallucinated URL check: keep only links we can vouch for.
        urls = URL_RE.findall(cleaned)
        if urls:
            allowed = _allowed_urls(session)
            client_domain = (session.client_domain or "").lower().replace("https://", "").replace("http://", "").strip("/")
            for url in urls:
                url_l = url.lower()
                is_allowed = (
                    any(url.rstrip(".,") in a or a in url for a in allowed)
                    or any(d in url_l for d in ALWAYS_ALLOWED_DOMAINS)
                    or (client_domain and client_domain in url_l)
                )
                if not is_allowed:
                    logger.warning(f"[ResponseValidator] Stripping unverified URL: '{url}'")
                    cleaned = cleaned.replace(url, "")

        # 4. Profanity check
        words = re.findall(r"\b[\w*]+\b", cleaned.lower())
        if any(w in PROFANITY_BLACKLIST for w in words):
            logger.warning("[ResponseValidator] Safety alert: profanity detected in reply. Using fallback.")
            return PROFANITY_FALLBACK

        # 5. Length: the prompt asks for max_response_length; only cut replies that
        # run far past it, at a sentence boundary, within WhatsApp's hard limit.
        target = 600
        if session.agent_settings and session.agent_settings.max_response_length:
            target = session.agent_settings.max_response_length
        hard_limit = min(max(int(target * 2), 800), WHATSAPP_MAX_CHARS)
        if len(cleaned) > hard_limit:
            logger.warning(f"[ResponseValidator] Response length ({len(cleaned)}) exceeds {hard_limit}. Truncating.")
            cleaned = _truncate_at_boundary(cleaned, hard_limit)

        # Collapse extra spaces while preserving line breaks for WhatsApp
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        cleaned = re.sub(r" +\n", "\n", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

        return cleaned or EMPTY_REPLY_FALLBACK


# Global validator singleton
response_validator = ResponseValidator()
