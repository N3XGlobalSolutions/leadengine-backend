"""Chat Products — product-intent detection and product cards for website chat replies.

When the tenant has chat products (TenantConfig.chat_products, max 3) and the visitor's
message shows product intent, the chat reply carries those products as cards (in admin
order) and the answer gets a one-line intro. Separate from the outbound/WhatsApp
product catalog (OutboundProduct).

Everything here is failure-safe: malformed products are logged and skipped, and
attach_chat_products() never raises — the chat must not break because of products.
"""

import logging
import re
from typing import Any, Iterable, List, Optional

from app.models.conversation import ChatProductCard
from app.models.tenant import CHAT_PRODUCTS_MAX, is_valid_chat_product_image_id

# Plain stdlib logger (app.core.logging_config.get_logger is a thin wrapper around it);
# avoids loading app settings so this module stays importable in unit tests.
logger = logging.getLogger(__name__)

PRODUCTS_INTRO_MESSAGE_KEY = "chat_products_intro"
PRODUCTS_INTRO_FALLBACK = "Here's what we offer — tap one to know more."

# Whole-word / whole-phrase, case-insensitive. Deliberately strong signals only: broad words such as
# "options", "plans", "services" or "range" also appear in ordinary qualification answers
# ("my price range is 80k") and would pop the product cards up mid-flow.
PRODUCT_INTENT_TERMS = (
    "product",
    "products",
    "catalog",
    "catalogue",
    "models",
    "builds",
    "packages",
    "offerings",
    "collection",
    "what do you sell",
    "what do you offer",
    "show me",
    "recommend",
    "suggest",
    "price list",
    "pricing",
)


def _phrase_pattern(phrase: str) -> Optional[str]:
    """Regex for a whole word/phrase: any whitespace between words, no word chars on either side."""
    words = phrase.split()
    if not words:
        return None
    return r"(?<!\w)" + r"\s+".join(re.escape(word) for word in words) + r"(?!\w)"


_PRODUCT_INTENT_RE = re.compile(
    "|".join(_phrase_pattern(term) for term in PRODUCT_INTENT_TERMS),
    re.IGNORECASE,
)


def _product_field(product: Any, name: str) -> Any:
    """Read a field from a ChatProduct model or a plain dict."""
    if isinstance(product, dict):
        return product.get(name)
    return getattr(product, name, None)


# Asking to see pictures: a request word followed closely by an image word ("give me image",
# "show some photos", "can I see pictures"). An image word on its own ("I do image editing") is
# an ordinary answer, not a product request.
_MEDIA_REQUEST_RE = re.compile(
    r"(?<!\w)(?:show|send|give|share|see|view|any|some)(?!\w)[\w\s,']{0,24}?"
    r"(?<!\w)(?:images?|photos?|pictures?|pics?)(?!\w)",
    re.IGNORECASE,
)

# Misspellings of the core product words ("produt", "prodcut", "prodects") — one edit away.
_FUZZY_TARGETS = ("product", "products", "catalog", "catalogue")
# Real words one edit away from a target that must not count as a product request
_FUZZY_EXCLUDE = {"produce", "produced", "producer", "produces", "protect", "protects"}
_WORD_RE = re.compile(r"[a-z]+")


def _edit_distance_at_most_one(a: str, b: str) -> bool:
    """True when a and b differ by at most one insert, delete, substitution or adjacent swap."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        diffs = [i for i in range(la) if a[i] != b[i]]
        if len(diffs) == 1:
            return True
        return len(diffs) == 2 and diffs[1] == diffs[0] + 1 and a[diffs[0]] == b[diffs[1]] and a[diffs[1]] == b[diffs[0]]
    shorter, longer = (a, b) if la < lb else (b, a)
    i = 0
    while i < len(shorter) and shorter[i] == longer[i]:
        i += 1
    return shorter[i:] == longer[i + 1:]


def _has_product_word_typo(message: str) -> bool:
    for word in _WORD_RE.findall(message.lower()):
        if len(word) < 6 or word in _FUZZY_EXCLUDE:
            continue
        if any(_edit_distance_at_most_one(word, target) for target in _FUZZY_TARGETS):
            return True
    return False


def has_product_intent(message: str, products: Optional[Iterable[Any]] = None) -> bool:
    """Does the visitor's message ask about products?

    True on a whole-word match of a product-intent term (product, catalog, pricing,
    "what do you sell", ...), a misspelt product word ("produt"), or a request to see pictures
    ("give me image"). Mentioning a product's title alone does NOT count: tapping a card sends
    "I'm interested in <title>", and re-showing every card in reply would loop.
    `products` is accepted for callers' convenience and future use. Never raises.
    """
    if not isinstance(message, str) or not message.strip():
        return False
    return bool(
        _PRODUCT_INTENT_RE.search(message)
        or _MEDIA_REQUEST_RE.search(message)
        or _has_product_word_typo(message)
    )


def build_product_cards(products: Optional[Iterable[Any]]) -> List[ChatProductCard]:
    """Build chat product cards in admin order (max 3); malformed products are logged and skipped."""
    cards: List[ChatProductCard] = []
    for index, product in enumerate(products or []):
        if len(cards) >= CHAT_PRODUCTS_MAX:
            break
        try:
            product_id = _product_field(product, "product_id")
            title = _product_field(product, "title")
            if not isinstance(product_id, str) or not product_id.strip():
                raise ValueError("missing product_id")
            if not isinstance(title, str) or not title.strip():
                raise ValueError("missing title")

            description = _product_field(product, "description")
            image_id = _product_field(product, "image_id") or None
            if image_id is not None and not is_valid_chat_product_image_id(image_id):
                # Keep the card, drop the unusable image reference
                logger.warning(f"Chat products: ignoring invalid image_id on product {index}")
                image_id = None

            cards.append(
                ChatProductCard(
                    product_id=product_id.strip(),
                    title=title.strip(),
                    description=description.strip() if isinstance(description, str) else "",
                    image_id=image_id,
                )
            )
        except Exception as e:
            logger.warning(f"Chat products: skipping malformed product at index {index}: {e}")
    return cards


def get_products_intro(language: Optional[str]) -> str:
    """One-line intro shown above the product cards, localized with an English fallback."""
    try:
        from app.utils.localization import get_message

        intro = get_message(language or "en", PRODUCTS_INTRO_MESSAGE_KEY)
        if isinstance(intro, str) and intro.strip():
            return intro
    except Exception as e:
        logger.warning(f"Chat products: intro localization failed: {e}")
    return PRODUCTS_INTRO_FALLBACK


def prefix_answer(answer: Optional[str], intro: str, conversational_transition: Optional[str] = None) -> str:
    """Put the intro line at the top of the answer.

    If the answer starts with the S2/S3 conversational transition (which the chat client
    strips by prefix), the intro goes right after it so that stripping keeps working.
    """
    answer = answer or ""
    transition = conversational_transition or ""

    head = ""
    body = answer
    if transition.strip() and answer.startswith(transition):
        head = transition
        body = answer[len(transition):]

    body = body.strip()
    if body.startswith(intro):
        return answer  # already prefixed

    prefixed = f"{intro}\n\n{body}" if body else intro
    return f"{head}\n\n{prefixed}" if head else prefixed


def attach_chat_products(response: Any, products: Optional[Iterable[Any]], message: str, language: Optional[str] = None) -> Any:
    """Fill response.products and prefix the answer when the message shows product intent.

    No-op when there are no (valid) products or no product intent. Never raises —
    on any error the response is returned unchanged.
    """
    try:
        products = list(products or [])
        if not products or not has_product_intent(message, products):
            return response

        cards = build_product_cards(products)
        if not cards:
            return response

        new_answer = prefix_answer(
            getattr(response, "answer", ""),
            get_products_intro(language),
            getattr(response, "conversational_transition", None),
        )
        response.answer = new_answer
        response.products = cards
        logger.info(
            f"Chat products attached: {len(cards)} card(s) for conversation "
            f"{getattr(response, 'conversation_id', None)}"
        )
    except Exception as e:
        logger.warning(f"Chat products skipped: {e}", exc_info=True)
    return response
