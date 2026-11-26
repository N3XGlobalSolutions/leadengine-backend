"""Parse the machine-readable <state> line the sales agent appends to each reply.

The agent ends every reply with one line such as:

    <state>{"collected": {"budget": "5 lakhs"}, "status": "IN_PROGRESS", "handoff": false}</state>

This lets us record lead details and qualification status in the same LLM call
that writes the reply (no extra classification call, so no extra tokens). The line
is always stripped before anything is stored or sent to the customer.
"""

import json
import re
from typing import Any, Dict, Tuple

VALID_STATUSES = {"IN_PROGRESS", "QUALIFIED", "NOT_QUALIFIED", "OPTED_OUT"}
VALID_OBJECTIONS = {"PRICE", "TIMING", "TRUST", "COMPETITOR", "NEED", "AUTHORITY"}

_STATE_BLOCK_RE = re.compile(r"<state>(.*?)</state>", re.IGNORECASE | re.DOTALL)
# An opening tag with no closing tag (reply cut off by max_tokens): drop everything after it.
_DANGLING_STATE_RE = re.compile(r"<state>.*\Z", re.IGNORECASE | re.DOTALL)
_KEY_CLEAN_RE = re.compile(r"[^a-z0-9_]+")

MAX_COLLECTED_FIELDS = 20
MAX_KEY_LEN = 40
MAX_VALUE_LEN = 300


def extract_state(text: str) -> Tuple[str, Dict[str, Any]]:
    """Split model output into (customer-facing reply, parsed state dict).

    Never raises: malformed or missing state yields an empty dict, and any
    <state> markup is removed from the reply either way.
    """
    if not text:
        return "", {}

    state: Dict[str, Any] = {}
    matches = list(_STATE_BLOCK_RE.finditer(text))
    if matches:
        raw = matches[-1].group(1).strip()
        # Tolerate ```json fences inside the tag.
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                state = parsed
        except (ValueError, TypeError):
            state = {}

    reply = _STATE_BLOCK_RE.sub("", text)
    reply = _DANGLING_STATE_RE.sub("", reply)
    return reply.strip(), normalize_state(state)


def normalize_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and bound the parsed state so bad model output can't corrupt memory."""
    if not isinstance(state, dict):
        return {}

    normalized: Dict[str, Any] = {}

    collected = state.get("collected")
    if isinstance(collected, dict):
        clean: Dict[str, str] = {}
        for key, value in list(collected.items())[:MAX_COLLECTED_FIELDS]:
            clean_key = _KEY_CLEAN_RE.sub("_", str(key).strip().lower()).strip("_")[:MAX_KEY_LEN]
            if value is None or isinstance(value, (dict, list)):
                continue
            clean_value = str(value).strip()[:MAX_VALUE_LEN]
            if clean_key and clean_value:
                clean[clean_key] = clean_value
        if clean:
            normalized["collected"] = clean

    status = str(state.get("status") or "").strip().upper()
    if status in VALID_STATUSES:
        normalized["status"] = status

    # Lead score 0-100 as judged by the agent against the business's qualification rules.
    score = state.get("score")
    if score is not None and not isinstance(score, bool):
        try:
            normalized["score"] = max(0, min(100, int(round(float(score)))))
            reason = str(state.get("score_reason") or "").strip()
            if reason:
                normalized["score_reason"] = reason[:200]
        except (TypeError, ValueError):
            pass

    objection = str(state.get("objection") or "").strip().upper()
    if objection in VALID_OBJECTIONS:
        normalized["objection"] = objection

    if state.get("handoff") is True:
        normalized["handoff"] = True

    return normalized
