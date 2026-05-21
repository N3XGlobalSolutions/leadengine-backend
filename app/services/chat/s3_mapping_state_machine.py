"""S3 Mapping State Machine.

Deterministic qualification engine for the S3_MAPPING plan tier.

PHASE 1 — Mapped Questions (up to 30)
  - Admin-defined questions with quick reply options
  - Each option has a signal level: High / Medium / Low / Disqualify
  - Disqualify: immediate exit with a custom message (conversation ends)
  - Questions may carry an optional inline table (rendered in the chat UI)
  - All answers recorded with their signal levels

PHASE 2 — Evaluate + Lead Output
  - Compute lead priority from all phase signals
  - Generate closing summary for the user
"""

import re
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.core.logging_config import get_logger
from app.models.conversation import Conversation, ConversationState
from app.models.tenant import TenantConfig, QuestionMapItem, QuestionMapTable, SignalLevel
from app.services.ai.claude_service import ClaudeService
from app.utils.localization import get_message, get_language_name

logger = get_logger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
SIGNAL_SCORES: Dict[str, int] = {
    SignalLevel.HIGH: 3,
    SignalLevel.MEDIUM: 2,
    SignalLevel.LOW: 1,
    SignalLevel.DISQUALIFY: 0,  # Never reaches scoring (exits immediately)
}


MAX_IGNORE_COUNT = 2  # Skip question after 2 ignored turns



# ── Result dataclass ───────────────────────────────────────────────────────────
@dataclass
class S3MappingResult:
    """Result from S3 Mapping state machine processing."""

    next_state: str
    action: str  # "ask_contact" | "mapped_question" | "disqualified" | "show_summary" | "ended"
    bot_response: str = ""
    quick_replies: List[str] = field(default_factory=list)
    show_actions: bool = False
    summary_data: Optional[Dict[str, Any]] = None
    lead_output: Optional[Dict[str, Any]] = None
    updated_mapping_state: Optional[Dict[str, Any]] = None


# ── Mapping state (persisted in conversation.s3_mapping_state) ─────────────────
def _default_mapping_state() -> Dict[str, Any]:
    return {
        "phase": 1,            # 1 = mapped questions, 2 = done
        "mapped_index": 0,     # current question index (0-based)
        "answers": [],         # list of {id, question, answer, signal}
        "ignore_count": 0,     # consecutive ignores on current question
        "disqualified": False,
        "name": "",
        "phone": "",
    }


def _load_state(conversation: Conversation) -> Dict[str, Any]:
    if conversation.s3_mapping_state:
        return dict(conversation.s3_mapping_state)
    return _default_mapping_state()


# ── Phone / name extraction ────────────────────────────────────────────────────
def _extract_phone(text: str) -> Optional[str]:
    """Extract and validate a phone number from text.

    Validates:
    - Indian mobile: exactly 10 digits starting with 6-9
    - With country code: +91 / 91 prefix followed by 10 valid digits

    Rejects anything that doesn't match exactly (wrong length, wrong prefix).
    """
    # Strip common contextual phrases before extracting digits
    clean = text.lower()
    for phrase in ("my phone is", "my number is", "call me at", "reach me at",
                   "my mobile is", "my contact is"):
        clean = clean.replace(phrase, " ")
    clean = clean.strip()

    # Grab all digit characters
    digits = re.sub(r"[^\d]", "", clean)

    if not digits:
        return None

    # Handle +91 / 91 prefix — strip it to get the bare 10-digit number
    if len(digits) == 12 and digits.startswith("91") and digits[2] in "6789":
        logger.info(f"Valid Indian phone with +91 prefix: {digits[2:]}")
        return digits[2:]

    # Validate bare 10-digit Indian mobile
    if len(digits) == 10 and digits[0] in "6789":
        logger.info(f"Valid Indian phone extracted: {digits}")
        return digits

    logger.warning(
        f"Invalid phone rejected from S3 Mapping: '{text[:50]}' "
        f"(digits='{digits}', len={len(digits)})"
    )
    return None


def _extract_name(message: str) -> str:
    msg_lower = message.lower().strip()

    greetings = ["hi", "hello", "hey", "hola", "greetings", "good morning", "good evening", "good afternoon"]
    # Exact greeting-only messages have no name
    if msg_lower in greetings:
        return ""

    # Words that commonly follow "I'm / I am" but are NOT names
    NOT_A_NAME = {
        "interested", "looking", "trying", "hoping", "calling", "writing",
        "reaching", "contacting", "here", "not", "a", "an", "the", "just",
        "also", "very", "really", "quite", "sure", "ok", "okay", "fine",
        "good", "great", "well", "ready", "happy", "glad", "curious",
        "planning", "thinking", "wondering", "considering",
    }

    match = re.search(r"(?:my name is|name is|i am|i'm|call me|i am called)\s+([A-Za-z]+)", msg_lower)
    if match:
        candidate = match.group(1)
        if candidate not in NOT_A_NAME:
            return candidate.title()

    # Short all-alpha messages (1-2 words) that don't start with a greeting are likely just a name
    words = msg_lower.split()
    if len(words) <= 2 and all(w.isalpha() for w in words):
        if not any(msg_lower.startswith(g) for g in greetings):
            return " ".join(words).title()

    return ""


# ── Signal computation ─────────────────────────────────────────────────────────
def _compute_priority(answers: List[Dict[str, Any]]) -> Tuple[str, float]:
    """Compute overall lead priority from collected answer signals.

    Returns:
        (priority_label, avg_score)  e.g. ("HIGH", 2.7)
    """
    scorable = [a for a in answers if a.get("signal") != SignalLevel.DISQUALIFY]
    if not scorable:
        return ("LOW", 0.0)

    total = sum(SIGNAL_SCORES.get(a.get("signal", SignalLevel.LOW), 1) for a in scorable)
    avg = total / len(scorable)

    if avg >= 2.5:
        label = "HIGH"
    elif avg >= 1.5:
        label = "MEDIUM"
    else:
        label = "LOW"

    return label, round(avg, 2)


# ── Table → Markdown formatter ─────────────────────────────────────────────────
def _table_to_markdown(table: QuestionMapTable) -> str:
    """Convert a QuestionMapTable into a GitHub-flavoured markdown table string.

    Example output (rendered as an HTML table in the chat widget via remark-gfm):

        | Vintage  | Turnover | Own Property | Offered ROI % |
        |----------|----------|--------------|---------------|
        | 2–3 yrs  | ₹1–2Cr   | Yes          | 20%–22%       |
        ...
    """
    if not table.headers or not table.rows:
        return ""

    def _pad(cells: List[str], widths: List[int]) -> str:
        return "| " + " | ".join(c.ljust(w) for c, w in zip(cells, widths)) + " |"

    # Compute column widths for nice alignment
    all_rows = [table.headers] + table.rows
    col_count = len(table.headers)
    widths = [
        max(len(str(row[i])) if i < len(row) else 0 for row in all_rows)
        for i in range(col_count)
    ]

    lines = [
        _pad(table.headers, widths),
        "| " + " | ".join("-" * w for w in widths) + " |",
    ]
    for row in table.rows:
        # Pad short rows with empty strings
        padded_row = list(row) + [""] * (col_count - len(row))
        lines.append(_pad(padded_row[:col_count], widths))

    return "\n".join(lines)


def _format_question_message(q: QuestionMapItem) -> str:
    """Build the full bot message for a mapped question.

    If the question has an attached table, appends it as a markdown table
    after the question text (separated by a blank line).
    """
    text = f"**{q.question}**"
    if q.table:
        table_md = _table_to_markdown(q.table)
        if table_md:
            text = f"{text}\n\n{table_md}"
    return text


# ── Lead summary builders ──────────────────────────────────────────────────────
def _build_lead_summary(ms: Dict[str, Any], config: TenantConfig) -> Dict[str, Any]:
    answers = ms.get("answers", [])
    priority, avg_score = _compute_priority(answers)

    return {
        "lead_priority": priority,
        "avg_signal_score": avg_score,
        "name": ms.get("name", ""),
        "phone": ms.get("phone", ""),
        "total_questions": len(answers),
        "answers": answers,
        "signal_breakdown": {
            "high": sum(1 for a in answers if a.get("signal") == SignalLevel.HIGH),
            "medium": sum(1 for a in answers if a.get("signal") == SignalLevel.MEDIUM),
            "low": sum(1 for a in answers if a.get("signal") == SignalLevel.LOW),
        },
        "disqualified": ms.get("disqualified", False),
    }


def _build_summary_message(ms: Dict[str, Any], config: TenantConfig) -> str:
    """Generate a concise user-facing closing message."""
    name = ms.get("name", "")
    lead_output = _build_lead_summary(ms, config)
    priority = lead_output["lead_priority"].lower()

    name_part = f", {name}" if name else ""
    company = config.company_name or "our team"
    lang = config.language or "en"
    
    return get_message(lang, "mapping_summary")[priority].format(
        name_part=name_part, company=company
    )


# ── Main state machine class ───────────────────────────────────────────────────
class S3MappingStateMachine:
    """S3 Mapping — deterministic qualification state machine.

    Asks admin-defined mapped questions one by one (up to 30).
    Each question may carry an optional inline table rendered in the chat.
    On any DISQUALIFY answer the conversation exits immediately.
    On completion all answers are scored and a lead is created.
    """

    def process_message(
        self,
        message: str,
        conversation: Conversation,
        config: TenantConfig,
        message_history: Optional[List[Dict[str, Any]]] = None,
    ) -> S3MappingResult:
        ms = _load_state(conversation)
        question_map = config.question_map or []

        # ── CONTACT COLLECTION ────────────────────────────────────────────────
        if not ms.get("phone") or not ms.get("name"):
            return self._handle_contact(message, ms, config)

        # ── PHASE 1: MAPPED QUESTIONS ─────────────────────────────────────────
        if ms["phase"] == 1:
            return self._handle_mapped_phase(message, ms, question_map, config, message_history)

        # ── DONE ──────────────────────────────────────────────────────────────
        lower_msg = message.lower()
        lang = config.language or "en"
        if "yes" in lower_msg and "schedule" in lower_msg:
            return S3MappingResult(
                next_state=ConversationState.COMPLETED.value,
                action="schedule_call",
                bot_response=get_message(lang, "mapping_ended"),
                show_actions=False,
                updated_mapping_state=ms,
            )
            
        if "maybe later" in lower_msg:
            return S3MappingResult(
                next_state=ConversationState.COMPLETED.value,
                action="disqualify_lead",
                bot_response="No problem at all. Feel free to reach out whenever you're ready.",
                show_actions=False,
                updated_mapping_state=ms,
            )

        lang = config.language or "en"
        bot_response = get_message(lang, "mapping_ended")
        
        return S3MappingResult(
            next_state=ConversationState.COMPLETED.value,
            action="ended",
            bot_response=bot_response,
            show_actions=False,
            updated_mapping_state=ms,
        )

    # ── Contact collection ─────────────────────────────────────────────────────
    def _handle_contact(
        self,
        message: str,
        ms: Dict[str, Any],
        config: TenantConfig,
    ) -> S3MappingResult:
        # Pre-extract standard rules just in case it's obvious to save LLM tokens or catch edge cases
        phone = _extract_phone(message)
        name = _extract_name(message)
        if phone and not ms.get("phone"):
            ms["phone"] = phone
        if name and not ms.get("name"):
            ms["name"] = name
            
        company = config.company_name or ""
        history = ms.get("contact_history", [{"role": "assistant", "content": f"Hi! 👋 Welcome at {company}. To get started, what's your name and phone number?"}])
        history.append({"role": "user", "content": message})
        ms["contact_history"] = history

        # If we magically already have both from simple extraction, skip Claude call!
        if ms.get("phone") and ms.get("name"):
            return self._transition_to_phase_1(ms, config)

        language_name = get_language_name(config.language)

        prompt = f"""You are a polite receptionist assistant for {company}.
Your goal is to collect the user's Name and Phone Number.
Speak ONLY in {language_name}.

You must return ONLY a raw JSON object with no markdown formatting.

Current knowledge:
Name: {ms.get('name')}
Phone: {ms.get('phone')}

Conversation so far:
{history}

PHONE NUMBER VALIDATION RULES (CRITICAL — strictly enforce these):
- A valid Indian mobile number has EXACTLY 10 digits
- It MUST start with 6, 7, 8, or 9
- Examples of VALID numbers: 9876543210, 8123456789, 7000000001
- Examples of INVALID numbers: 2345434 (7 digits), 00000 (wrong prefix), 265630 (6 digits), 123456 (starts with 1)
- If the user provides a number that does NOT match these rules, set extracted_phone to null
  and ask them politely to share a valid 10-digit Indian mobile number.
- NEVER confirm or echo back an invalid phone number. NEVER ask "is this correct?" for an invalid number.

Instructions:
1. Examine the user's latest message. Extract 'extracted_name' or 'extracted_phone' if provided.
2. Validate the phone using the rules above BEFORE setting extracted_phone. If invalid, set it to null and ask again.
3. If they ask a question regarding {company} or anything else, briefly answer it or acknowledge it politely, but ALWAYS gently steer back to asking for the missing contact details (Name or Phone).
4. If they firmly refuse to provide a phone number, set 'is_refused_phone' to true.
5. If they firmly refuse to provide a name, set 'is_refused_name' to true.
6. Provide a short, friendly 'reply' string. If you don't have both pieces of info naturally ask for them. If they provided something, thank them and ask for the rest.
7. If the phone they provided was invalid, your 'reply' MUST politely explain it wasn't a valid Indian mobile number and ask them to share a 10-digit number starting with 6, 7, 8, or 9.
8. Respond ONLY in {language_name}.

Output JSON Format:
{{
  "extracted_name": string | null,
  "extracted_phone": string | null,
  "is_refused_phone": boolean,
  "is_refused_name": boolean,
  "reply": string
}}
Ensure the output is ONLY valid JSON. Avoid wrapping in markdown blocks."""

        try:
            c = ClaudeService()
            resp = c.generate_response(prompt)
            # Robust JSON extraction
            match = re.search(r'\{.*\}', resp, re.DOTALL)
            if match:
                resp = match.group(0)
            data = json.loads(resp)
            
            if data.get("extracted_name"):
                ms["name"] = data["extracted_name"]
            # Validate Claude's phone before trusting it — never store a non-validated number
            if data.get("extracted_phone"):
                validated = _extract_phone(str(data["extracted_phone"]))
                if validated:
                    ms["phone"] = validated
                else:
                    logger.warning(
                        f"Claude returned invalid phone '{data['extracted_phone']}' — ignoring"
                    )
            if data.get("is_refused_phone"):
                ms["phone"] = "Not provided"
            if data.get("is_refused_name"):
                ms["name"] = "Not provided"
            
            lang = config.language or "en"
            bot_reply = data.get("reply", get_message(lang, "ask_contact"))
            if isinstance(bot_reply, list):
                import random
                bot_reply = random.choice(bot_reply)
            
        except Exception as e:
            logger.error(f"Claude fallback in S3 Mapping contact collection: {e}")
            lang = config.language or "en"
            # Fallback hardcode
            if not ms.get("name") and not ms.get("phone"):
                bot_reply = get_message(lang, "ask_contact")
                if isinstance(bot_reply, list):
                    import random
                    bot_reply = random.choice(bot_reply)
            elif not ms.get("phone"):
                name = ms.get("name")
                bot_reply = get_message(lang, "phone_ask_with_name", name=name)
                if isinstance(bot_reply, list):
                    import random
                    bot_reply = random.choice(bot_reply)
            else:
                bot_reply = get_message(lang, "got_phone_ask_name")
                if isinstance(bot_reply, list):
                    import random
                    bot_reply = random.choice(bot_reply)

        history.append({"role": "assistant", "content": bot_reply})
        ms["contact_history"] = history

        has_name = ms.get("name")
        has_phone = ms.get("phone")

        # Transition into Phase 1 if both handled
        if has_phone and has_name:
            return self._transition_to_phase_1(ms, config)

        return S3MappingResult(
            next_state=ConversationState.AWAITING_CONTACT.value,
            action="ask_contact",
            bot_response=bot_reply,
            updated_mapping_state=ms,
        )

    def _transition_to_phase_1(self, ms: Dict[str, Any], config: TenantConfig) -> S3MappingResult:
        first_q = (config.question_map or [None])[0]
        if first_q:
            lang = config.language or "en"
            p = get_message(lang, "mapping_transition")

            name_part = f", {ms['name']}" if ms.get('name') and ms['name'] != 'Not provided' else ""
            
            if ms.get("phone") == "Not provided":
                prefix = p["no_phone"]
            else:
                prefix = p["thanks"].format(name_part=name_part)
            
            greeting = f"{prefix} {p['suffix']}\n\n" + _format_question_message(first_q)
            qr = [opt.text for opt in first_q.quick_replies]
            return S3MappingResult(
                next_state=ConversationState.QUALIFYING.value,
                action="mapped_question",
                bot_response=greeting,
                quick_replies=qr,
                updated_mapping_state=ms,
            )
        else:
            # Safe fallback if mapped questions are empty
            return S3MappingResult(
                next_state=ConversationState.COMPLETED.value,
                action="show_summary",
                bot_response="Thank you for providing your contact information. We will get in touch soon!",
                updated_mapping_state=ms,
            )

    # ── Phase 1: Mapped questions ──────────────────────────────────────────────
    def _handle_mapped_phase(
        self,
        message: str,
        ms: Dict[str, Any],
        question_map: List[QuestionMapItem],
        config: TenantConfig,
        message_history: Optional[List[Dict[str, Any]]] = None,
    ) -> S3MappingResult:
        idx = int(ms["mapped_index"])

        # All mapped questions done → finalize
        if idx >= len(question_map):
            return self._finalize(ms, config)

        current_q = question_map[idx]
        matched_option = self._match_option(message, current_q)

        if matched_option is None:
            # Didn't match exactly. Let's ask Claude to try matching or handle detours!
            try:
                c = ClaudeService()
                options_str = "\n".join([f"- {opt.text}" for opt in current_q.quick_replies])
                company_context = config.company_context or ""
                company_name = config.company_name or ""
                
                # We'll include a brief history for context (last 4 turns)
                history_text = "None"
                if message_history:
                    history_clip = message_history[-100:] if len(message_history) > 4 else message_history
                    history_text = "\n".join([f"{msg['role'].capitalize()}: {msg['content']}" for msg in history_clip])
                
                language_name = get_language_name(config.language)

                prompt = f"""You are a helpful AI assistant for {company_name}.
We asked the user this question: "{current_q.question}"
The acceptable options are:
{options_str}

The user's latest reply: "{message}"

Company Context: {company_context}
Recent Chat History:
{history_text}

Your task:
1. Determine if the user's latest reply semantically means one of the acceptable options. If it does, set "matched_option" to exactly the text of that option.
2. If it does NOT match, determine if the user is asking a question or making a statement regarding the company or anything else. Briefly and politely answer it using the Company Context. 
3. If they did not match an option, you MUST provide a friendly "bot_response" (in {language_name}) that answers their question (if any) and politely asks them to please select one of the valid options for: "{current_q.question}".
4. Respond ONLY in {language_name}.

Return ONLY valid JSON (no markdown wrapping):
{{
  "matched_option": string | null,
  "bot_response": string | null
}}"""
                resp = c.generate_response(prompt)
                match = re.search(r'\{.*\}', resp, re.DOTALL)
                if match:
                    resp = match.group(0)
                data = json.loads(resp)
                
                if data.get("matched_option"):
                    # Find the actual option object by text
                    for opt in current_q.quick_replies:
                        if opt.text.lower().strip() == data["matched_option"].lower().strip():
                            matched_option = opt
                            break
                            
                bot_fallback_reply = data.get("bot_response")
            except Exception as e:
                logger.error(f"Claude fallback error in mapped phase: {e}")
                bot_fallback_reply = None

        if matched_option is None:
            # Still didn't match! Increment ignore count

            # Didn't match — increment ignore count
            ms["ignore_count"] = ms.get("ignore_count", 0) + 1
            if ms["ignore_count"] >= MAX_IGNORE_COUNT:
                # Auto-advance: treat as LOW signal
                ms["answers"].append({
                    "id": current_q.id,
                    "question": current_q.question,
                    "answer": "(skipped)",
                    "signal": SignalLevel.LOW,
                })
                ms["ignore_count"] = 0
                ms["mapped_index"] += 1
                return self._ask_next_mapped(ms, question_map, config)
            else:
                # Re-ask with a gentle nudge
                qr = [opt.text for opt in current_q.quick_replies]
                final_bot_response = bot_fallback_reply if bot_fallback_reply else (
                    "Please choose one of the options below to continue:\n\n"
                    + _format_question_message(current_q)
                )
                return S3MappingResult(
                    next_state=ConversationState.QUALIFYING.value,
                    action="mapped_question",
                    bot_response=final_bot_response,
                    quick_replies=qr,
                    updated_mapping_state=ms,
                )

        # ── DISQUALIFY CHECK (immediate exit) ──────────────────────────────────
        if matched_option.signal == SignalLevel.DISQUALIFY:
            ms["disqualified"] = True
            ms["answers"].append({
                "id": current_q.id,
                "question": current_q.question,
                "answer": matched_option.text,
                "signal": SignalLevel.DISQUALIFY,
            })
            ms["phase"] = 2
            exit_msg = (
                matched_option.exit_message
                or "Thank you for your interest. Unfortunately, this program may not be the right fit at this time."
            )
            return S3MappingResult(
                next_state=ConversationState.COMPLETED.value,
                action="disqualified",
                bot_response=exit_msg,
                show_actions=False,
                lead_output=_build_lead_summary(ms, config),
                updated_mapping_state=ms,
            )

        # ── Record answer ──────────────────────────────────────────────────────
        ms["answers"].append({
            "id": current_q.id,
            "question": current_q.question,
            "answer": matched_option.text,
            "signal": matched_option.signal,
        })
        ms["ignore_count"] = 0
        ms["mapped_index"] += 1

        return self._ask_next_mapped(ms, question_map, config)

    def _match_option(self, message: str, question: QuestionMapItem):
        """Match user message to a quick reply option (case-insensitive, partial ok)."""
        msg_lower = message.lower().strip()
        best = None
        best_score = 0
        for opt in question.quick_replies:
            opt_lower = opt.text.lower().strip()
            if msg_lower == opt_lower:
                return opt  # Exact match
            if opt_lower in msg_lower or msg_lower in opt_lower:
                score = len(opt_lower)
                if score > best_score:
                    best_score = score
                    best = opt
        return best

    def _ask_next_mapped(
        self,
        ms: Dict[str, Any],
        question_map: List[QuestionMapItem],
        config: TenantConfig,
    ) -> S3MappingResult:
        idx = int(ms["mapped_index"])

        if idx >= len(question_map):
            return self._finalize(ms, config)

        next_q = question_map[idx]
        qr = [opt.text for opt in next_q.quick_replies]

        return S3MappingResult(
            next_state=ConversationState.QUALIFYING.value,
            action="mapped_question",
            bot_response=_format_question_message(next_q),
            quick_replies=qr,
            updated_mapping_state=ms,
        )

    # ── Finalize ───────────────────────────────────────────────────────────────
    def _finalize(self, ms: Dict[str, Any], config: TenantConfig) -> S3MappingResult:
        ms["phase"] = 2
        summary_msg = _build_summary_message(ms, config)
        lead_output = _build_lead_summary(ms, config)

        return S3MappingResult(
            next_state=ConversationState.COMPLETED.value,
            action="show_summary",
            bot_response=summary_msg,
            show_actions=False,
            summary_data=lead_output,
            lead_output=lead_output,
            updated_mapping_state=ms,
        )


# ── Singleton ──────────────────────────────────────────────────────────────────
s3_mapping_state_machine = S3MappingStateMachine()
