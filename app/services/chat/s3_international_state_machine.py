"""S3 State Machine for Smart Qualifier Bot.

This service provides adaptive, context-aware flow control for S3 tier bots.
Unlike S1's deterministic fixed-question flow, S3 uses LLM reasoning to:
1. Adapt questions based on prior answers
2. Generate conversational transitions
3. Detect buyer psychology signals
4. Soft-disqualify weak leads politely

Key Principles:
1. Phone number is STILL mandatory (inherited from S1)
2. Questions branch based on conversation context
3. Acknowledges answers before next question
4. Creates richer output for sales team
"""

import random
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import phonenumbers

from app.services.chat.text_cleaners import clean_llm_text
from app.core.logging_config import get_logger
from app.models.conversation import Conversation, ConversationState
from app.models.tenant import TenantConfig, QualificationQuestion
from app.utils.localization import get_message, get_language_name
from app.services.ai.llm_service import llm_service

logger = get_logger(__name__)

# Constants
MAX_IGNORE_COUNT = 2
MAX_NAME_ASK_COUNT = 2
S3_MIN_QUESTIONS = 5  # Minimum questions before allowing completion
S3_MAX_QUESTIONS = 30  # Maximum questions to prevent endless loops


@dataclass
class S3InternationalStateMachineResult:
    """Result from S3 state machine processing."""

    next_state: str
    action: str  # Same as S1 + "soft_disqualify", "adaptive_ask"
    step_index: int = 0
    question_to_ask: Optional[str] = None
    rag_query: Optional[str] = None
    summary_data: Optional[Dict[str, Any]] = None
    quick_replies: List[str] = field(default_factory=list)
    show_actions: bool = False
    extracted_data: Dict[str, str] = field(default_factory=dict)
    increment_name_ask: bool = False
    # S3-specific fields
    conversational_transition: Optional[str] = None  # Acknowledgment before question
    s3_lead_output: Optional[Dict[str, Any]] = None  # Rich output for sales


class S3InternationalChatStateMachine:
    """S3 Smart Qualifier - context-aware qualification assistant.

    S3 Design Principles (from BRD):
    1. Adaptive Questioning - questions branch based on earlier answers
    2. Conversational Transitions - acknowledge answers, don't jump blindly
    3. Qualification Dimensions beyond basic LQC:
       - Motivation (why now?)
       - Timeline realism
       - Budget confidence vs hesitation
       - Decision authority
       - Prior experience
       - Comparison behavior
    4. Soft Disqualification - polite handling of weak leads
    5. Rich Output for sales team
    """

    # Inherit patterns from S1
    # Phone number regex patterns (multiple formats)
    PHONE_PATTERNS = [
        r"[6-9]\d{9}",  # Indian mobile: 9876543210
        r"\+91[-.\s]?[6-9]\d{9}",  # Indian with code: +91 9876543210
    ]

    # Question indicators for detecting user questions
    QUESTION_INDICATORS = [
        "?",  # Explicit question mark
        "what is",
        "what are",
        "what's",
        "whats",
        "how much",
        "how many",
        "how do",
        "how can",
        "how is",
        "how does",
        "when is",
        "when can",
        "when do",
        "where is",
        "where can",
        "where do",
        "why is",
        "why do",
        "why are",
        "why would",
        "can you",
        "can i",
        "could you",
        "could i",
        "can u",
        "do you",
        "does it",
        "is there",
        "are there",
        "do u",
        "tell me about",
        "tell me",
        "explain",
        "describe",
        "i want to know",
        "i need to know",
        "what about",
        "how about",
        # Hypothetical scenarios (from failing tests)
        "suppose",
        "suppose that",
        "let's say",
        "lets say",
        "in case",
        "in case that",
        "imagine if",
        "imagine",
        "what if",
        "what would",
        # Polite modals (from failing tests)
        "would it be",
        "would it be possible",
        "might you",
        "might you have",
        "could you perhaps",
        "may i ask",
        "may i know",
        "is it possible",
        "would you mind",
        # Imperative information requests (from failing tests)
        "show me",
        "let me see",
        "let me know",
        "lemme know",
        "lemme see",
        "i'd like to see",
        "id like to see",
        "wanna see",
        # Indirect questions (enhanced)
        "wondering if",
        "wondering whether",
        "wondering about",
        "curious about",
        "curious if",
        "curious to know",
        "trying to understand",
        "trying to figure",
        # Comparison questions
        "which is",
        "which one",
        "which would",
        # Typos/casual (from failing tests)
        "wat is",
        "wat about",
        "how much iz",
        "wanna know",
        "wanna see",
    ]

    # Deliberation/request phrases - user needs more info before deciding
    DELIBERATION_INDICATORS = [
        "to decide",
        "to determine",
        "before i decide",
        "before deciding",
        "whether or not",
        "whether i should",
        "should i",
        "i need more",
        "i need detailed",
        "need more info",
        "need details",
        "send me",
        "share with me",
        "provide me",
        "give me more",
        "i'm not sure",
        "i am not sure",
        "not sure if",
        "not sure how",
        "i'm confused",
        "i don't understand",
        "don't get",
        "clarify",
        "elaborate",
        "more information",
        "more details",
        "what options",
        "what choices",
        "available options",
        "can you tell",
        "could you share",
        "would like to know",
        "would love to",
        "depends on",
        "it depends",
        "need to think",
        # Casual uncertainty patterns (from failing tests)
        "dunno",
        "idk",
        "i dk",
        "you tell me",
        "u tell me",
        "whatever works",
        "anything works",
        "up to you",
        "your call",
        # Implied information needs
        "still waiting",
        "still unclear",
        "not clear on",
        "not clear about",
        "need clarification",
        "need more info",
        "confused about",
        "unclear about",
        # Embedded question patterns
        "trying to figure out if",
        "need to know whether",
        "wondering what",
        "wondering how",
        "wondering when",
        "i was wondering",
        "was wondering",
        "interested in learning",
        "would love to hear",
    ]

    # Answer indicators - signals user is likely answering
    # Note: Short phrases like 'i want', 'i need' are only treated as answers
    # if the message is short and doesn't contain deliberation indicators
    ANSWER_INDICATORS = [
        "my name is",
        "i am ",
        "i'm ",
        "i m ",
        "my number is",
        "my phone is",
        "call me at",
        "reach me at",
        "yes",
        "no",
        "maybe",
        "sure",
        "ok",
        "okay",
        "i prefer",
        "i like",
        "around",
        "about",
        "approximately",
        "within",
        "under",
        "over",
        "between",
    ]

    # S3-specific: Qualification dimension keywords for detection
    QUALIFICATION_DIMENSIONS = {
        "motivation": ["why", "reason", "motivated", "driving", "decided"],
        "timeline": ["when", "timeline", "timeframe", "deadline", "months", "weeks"],
        "budget": ["budget", "afford", "spend", "cost", "price", "money"],
        "authority": ["decide", "decision", "family", "spouse", "partner", "boss"],
        "experience": ["before", "previously", "experience", "done this", "first time"],
        "comparison": ["others", "comparing", "options", "alternatives", "competitors"],
    }

    # Conversational transition templates
    TRANSITION_TEMPLATES = {
        "budget": [
            "Got it, {answer_summary}. Now about the budget side -",
            "I understand, {answer_summary}. Let me ask about investment -",
            "Thanks for sharing that! Regarding your budget -",
        ],
        "timeline": [
            "Perfect, {answer_summary}. In terms of timing -",
            "Okay, that helps! Speaking of timelines -",
            "Great, {answer_summary}. Now about when you'd need this -",
        ],
        "authority": [
            "Understood, {answer_summary}. Just to understand the process -",
            "Got it! On the decision-making side -",
            "Makes sense, {answer_summary}. Quick question about decisions -",
        ],
        "default": [
            "Thanks for that! {answer_summary}. Next -",
            "Got it, {answer_summary}. Moving on -",
            "Understood! {answer_summary}. Let me ask -",
        ],
    }

    # Soft disqualification templates
    SOFT_DISQUALIFY_TEMPLATES = [
        "Based on what you shared, this might not be the right time. Would you like us to reconnect later?",
        "I appreciate your honesty! It sounds like timing isn't ideal right now. Can we reach out in a few months?",
        "Thanks for sharing that - it helps me understand where you are. Should we check back in when things align better?",
    ]

    def __init__(self):
        self._llm_service = None

    @property
    def llm_service(self):
        """Lazy load LLM service to avoid circular imports."""
        if self._llm_service is None:
            from app.services.ai.llm_service import llm_service

            self._llm_service = llm_service
        return self._llm_service

    def process_message(
            self,
            conversation: Conversation,
            user_message: str,
            tenant_config: TenantConfig,
            history: Optional[List[Dict[str, str]]] = None,
    ) -> S3InternationalStateMachineResult:
        """
        Process user message with S3 adaptive logic.

        Main entry point for S3 state machine.
        """
        lang = tenant_config.language or "en"
        current_state = conversation.current_state
        logger.info(
            f"S3: Processing message in state: {current_state}, step: {conversation.current_step_index}"
        )

        # Handle states - S3 reuses some S1 states but adds adaptive handling
        if current_state == ConversationState.GREETING.value:
            return self._handle_greeting_state(
                conversation, user_message, tenant_config, lang
            )

        elif current_state == ConversationState.AWAITING_CONTACT.value:
            return self._handle_contact_state(
                conversation, user_message, tenant_config, history, lang
            )

        elif current_state in [ConversationState.QUALIFYING.value, "s3_qualifying"]:
            return self._handle_s3_qualifying_state(
                conversation, user_message, tenant_config, history, lang
            )

        elif current_state == "s3_soft_dq":
            return self._handle_s3_soft_disqualify_state(
                conversation, user_message, tenant_config, history, lang
            )

        elif current_state == ConversationState.COMPLETED.value:
            return self._handle_completed_state(
                conversation, user_message, tenant_config, lang
            )

        elif current_state == ConversationState.ENDED.value:
            return S3InternationalStateMachineResult(
                next_state=ConversationState.ENDED.value,
                action="ended",
                step_index=conversation.current_step_index,
            )

        # Default fallback
        return self._handle_greeting_state(conversation, user_message, tenant_config, lang)

    # ==================== STATE HANDLERS ====================

    def _handle_greeting_state(
            self,
            conversation: Conversation,
            user_message: str,
            tenant_config: TenantConfig,
            lang: str,
    ) -> S3InternationalStateMachineResult:
        """Handle initial greeting - same as S1 but with S3 result type."""
        current_data = dict(conversation.extracted_lead_data)
        extracted = self._extract_contact_info(user_message)
        current_data.update(extracted)

        has_phone = bool(current_data.get("phone"))
        has_name = bool(current_data.get("name"))

        if has_phone:
            # Got phone, check name then move to S3 qualifying
            if not has_name and conversation.name_ask_count < MAX_NAME_ASK_COUNT:
                name_prompts = get_message(lang, "got_phone_ask_name")
                if isinstance(name_prompts, str):
                    name_prompts = [name_prompts]

                return S3InternationalStateMachineResult(
                    next_state=ConversationState.AWAITING_CONTACT.value,
                    action="ask_name",
                    step_index=0,
                    question_to_ask=random.choice(name_prompts),
                    extracted_data=current_data,
                    increment_name_ask=True,
                )

            # Have phone (and name or gave up asking) - start S3 qualifying
            questions = tenant_config.qualification_questions or []
            first_idx = self._get_first_qualifying_index(questions)

            if first_idx < len(questions):
                first_q = questions[first_idx]
                quick_replies = self._get_quick_replies(first_q)

                # S3: Add conversational intro
                intro = get_message(lang, "mapping_transition")["thanks"].format(
                    name_part=f", {current_data['name']}" if current_data.get('name') else "")

                return S3InternationalStateMachineResult(
                    next_state="s3_qualifying",  # S3 uses its own qualifying state
                    action="ask_question",
                    step_index=first_idx,
                    question_to_ask=first_q.instruction,
                    conversational_transition=intro,
                    quick_replies=quick_replies,
                    extracted_data=current_data,
                )
            else:
                return S3InternationalStateMachineResult(
                    next_state=ConversationState.COMPLETED.value,
                    action="show_summary",
                    step_index=0,
                    summary_data=self._build_s3_summary(current_data, questions),
                    show_actions=True,
                    extracted_data=current_data,
                )

        # No phone yet - ask for contact
        if self._is_user_asking_question(user_message):
            phone_asks = get_message(lang, "need_phone_to_continue_for_s3International")

            phone_question = random.choice(phone_asks) if isinstance(phone_asks, list) else phone_asks

            return S3InternationalStateMachineResult(
                next_state=ConversationState.AWAITING_CONTACT.value,
                action="answer_and_ask",
                step_index=0,
                question_to_ask=phone_question,
                rag_query=user_message,
                extracted_data=current_data,
            )

        # Ask for contact info
        logger.info(f"user in greeting state : {has_name}" )
        if has_name:
            name = current_data.get("name", "")
            prompt_text = get_message(lang, "phone_ask_with_name_for_s3International", name=name)
            if isinstance(prompt_text, list):

                prompt_text = random.choice(prompt_text)
        else:
            prompt_text = get_message(lang, "ask_contact_for_s3International")

        return S3InternationalStateMachineResult(
            next_state=ConversationState.AWAITING_CONTACT.value,
            action="ask_contact",
            step_index=0,
            question_to_ask=prompt_text,
            extracted_data=current_data,
        )

    def _handle_contact_state(
            self,
            conversation: Conversation,
            user_message: str,
            tenant_config: TenantConfig,
            history: Optional[List[Dict[str, str]]],
            lang: str,
    ) -> S3InternationalStateMachineResult:
        """Handle contact collection - phone is mandatory."""
        current_data = dict(conversation.extracted_lead_data)
        extracted = self._extract_contact_info(user_message)
        current_data.update(extracted)

        has_phone = bool(current_data.get("phone"))
        has_name = bool(current_data.get("name"))

        if has_phone:
            if not has_name and conversation.name_ask_count < MAX_NAME_ASK_COUNT:
                if lang == "en":
                    name_prompts = ["Great, got your number! May I know your name?"]
                else:
                    name_prompts = ["बढ़िया, आपका नंबर मिल गया! क्या मैं आपका नाम जान सकता हूँ?"]
                return S3InternationalStateMachineResult(
                    next_state=ConversationState.AWAITING_CONTACT.value,
                    action="ask_name",
                    step_index=0,
                    question_to_ask=random.choice(name_prompts),
                    extracted_data=current_data,
                    increment_name_ask=True,
                )

            # Start S3 qualifying
            questions = tenant_config.qualification_questions or []
            first_idx = self._get_first_qualifying_index(questions)

            if first_idx < len(questions):
                first_q = questions[first_idx]
                quick_replies = self._get_quick_replies(first_q)
                if lang == "en":
                    intro = "Perfect! Let's learn more about what you're looking for. "
                else:
                    intro = "उत्तम! आइए आप जो खोज रहे हैं उसके बारे में और जानें।. "

                return S3InternationalStateMachineResult(
                    next_state="s3_qualifying",
                    action="ask_question",
                    step_index=first_idx,
                    question_to_ask=first_q.instruction,
                    conversational_transition=intro,
                    quick_replies=quick_replies,
                    extracted_data=current_data,
                )
            else:
                # Prepare context for LLM
                context_str = f"Company: {tenant_config.company_name or 'Unknown'}\nDetails: {tenant_config.company_context}"

                return S3InternationalStateMachineResult(
                    next_state=ConversationState.COMPLETED.value,
                    action="show_summary",
                    step_index=0,
                    summary_data=self._build_s3_summary(current_data, questions),
                    show_actions=True,
                    extracted_data=current_data,
                    s3_lead_output=self._generate_and_store_s3_output(
                        current_data, questions, history, context_str
                    ),
                )

        # Still need phone
        if self._is_user_asking_question(user_message):
            prompt_text = get_message(lang, "need_phone_to_continue_for_s3International")
            return S3InternationalStateMachineResult(
                next_state=ConversationState.AWAITING_CONTACT.value,
                action="answer_and_ask",
                step_index=0,
                question_to_ask=random.choice(prompt_text),
                rag_query=user_message,
                extracted_data=current_data,
            )

        if has_name:
            name = current_data.get("name", "")
            prompt_text = get_message(lang, "phone_ask_with_name_for_s3International", name=name)
            if isinstance(prompt_text, list):

                prompt_text = random.choice(prompt_text)
        else:
            prompt_text = get_message(lang, "ask_contact_for_s3International")

        return S3InternationalStateMachineResult(
            next_state=ConversationState.AWAITING_CONTACT.value,
            action="ask_contact",
            step_index=0,
            question_to_ask=prompt_text,
            extracted_data=current_data,
        )

    def _handle_s3_qualifying_state(
            self,
            conversation: Conversation,
            user_message: str,
            tenant_config: TenantConfig,
            history: Optional[List[Dict[str, str]]],
            lang: str,
    ) -> S3InternationalStateMachineResult:
        """Handle S3 adaptive qualification flow.

        Key S3 differences from S1:
        1. Uses LLM to determine if we should continue, skip, or branch
        2. Generates conversational transitions between questions
        3. Detects soft-disqualify signals
        4. Tracks qualification dimensions
        """
        questions = tenant_config.qualification_questions or []
        current_index = conversation.current_step_index
        current_data = dict(conversation.extracted_lead_data)

        if current_index >= len(questions):
            # All questions done

            # Prepare context for LLM
            context_str = f"Company: {tenant_config.company_name or 'Unknown'}\nDetails: {tenant_config.company_context}"

            return S3InternationalStateMachineResult(
                next_state=ConversationState.COMPLETED.value,
                action="show_summary",
                step_index=current_index,
                summary_data=self._build_s3_summary(current_data, questions),
                s3_lead_output=self._generate_and_store_s3_output(
                    current_data, questions, history, context_str
                ),
                show_actions=True,
                extracted_data=current_data,
            )

        current_question = questions[current_index]
        is_user_asking = self._is_user_asking_question(user_message)
        is_user_answering = self._is_user_answering(user_message, current_question)

        logger.info(
            f"S3 Qualifying - Q{current_index + 1}, asking: {is_user_asking}, answering: {is_user_answering}"
        )

        # Priority: Handle user questions via RAG
        if is_user_asking:
            quick_replies = self._get_quick_replies(current_question)
            return S3InternationalStateMachineResult(
                next_state="s3_qualifying",
                action="answer_and_ask",
                step_index=current_index,
                question_to_ask=current_question.instruction,
                rag_query=user_message,
                quick_replies=quick_replies,
                extracted_data=current_data,
            )

        if is_user_answering:
            # Extract and store answer
            answer_summary = self._extract_answer_summary(
                user_message, current_question
            )
            if answer_summary:
                current_data[current_question.id] = answer_summary

            # S3: Check for soft-disqualify signals
            if self._should_soft_disqualify(user_message, current_data, current_index):
                return S3InternationalStateMachineResult(
                    next_state="s3_soft_dq",
                    action="soft_disqualify",
                    step_index=current_index,
                    question_to_ask=random.choice(self.SOFT_DISQUALIFY_TEMPLATES),
                    extracted_data=current_data,
                )

            # S3: Determine next question adaptively
            next_index = self._determine_next_question(
                current_index, questions, current_data, user_message
            )

            if next_index >= len(questions):
                # All done

                # Prepare context for LLM
                context_str = f"Company: {tenant_config.company_name or 'Unknown'}\nDetails: {tenant_config.company_context}"

                return S3InternationalStateMachineResult(
                    next_state=ConversationState.COMPLETED.value,
                    action="show_summary",
                    step_index=next_index,
                    summary_data=self._build_s3_summary(current_data, questions),
                    s3_lead_output=self._generate_and_store_s3_output(
                        current_data, questions, history, context_str
                    ),
                    show_actions=True,
                    extracted_data=current_data,
                )
            else:
                next_question = questions[next_index]
                quick_replies = self._get_quick_replies(next_question)

                # S3: Generate context-aware question via LLM
                # Combines transition and question into one natural message
                full_question = self._generate_context_aware_question(
                    answer_summary, next_question, tenant_config, history
                )

                return S3InternationalStateMachineResult(
                    next_state="s3_qualifying",
                    action="adaptive_ask",
                    step_index=next_index,
                    question_to_ask=full_question,
                    conversational_transition=None,  # Merged into question
                    quick_replies=quick_replies,
                    extracted_data=current_data,
                )

        # User ignored - handle with rephrase (like S1)
        ignore_count = conversation.step_ignore_count + 1

        if ignore_count >= MAX_IGNORE_COUNT:
            current_data[current_question.id] = "Not provided"
            next_index = current_index + 1

            if next_index >= len(questions):
                # Prepare context for LLM
                context_str = f"Company: {tenant_config.company_name or 'Unknown'}\nDetails: {tenant_config.company_context}"

                return S3InternationalStateMachineResult(
                    next_state=ConversationState.COMPLETED.value,
                    action="show_summary",
                    step_index=next_index,
                    summary_data=self._build_s3_summary(current_data, questions),
                    s3_lead_output=self._generate_and_store_s3_output(
                        current_data, questions, history, context_str
                    ),
                    show_actions=True,
                    extracted_data=current_data,
                )
            else:
                next_question = questions[next_index]
                quick_replies = self._get_quick_replies(next_question)
                transition = "No worries, let's move on. "

                return S3InternationalStateMachineResult(
                    next_state="s3_qualifying",
                    action="ask_question",
                    step_index=next_index,
                    question_to_ask=next_question.instruction,
                    conversational_transition=transition,
                    quick_replies=quick_replies,
                    extracted_data=current_data,
                )

        # Rephrase current question
        rephrased = self._rephrase_question(current_question, ignore_count)
        quick_replies = self._get_quick_replies(current_question)

        return S3InternationalStateMachineResult(
            next_state="s3_qualifying",
            action="ask_question",
            step_index=current_index,
            question_to_ask=rephrased,
            quick_replies=quick_replies,
            extracted_data=current_data,
        )

    def _handle_s3_soft_disqualify_state(
            self,
            conversation: Conversation,
            user_message: str,
            tenant_config: TenantConfig,
            history: Optional[List[Dict[str, str]]],
            lang: str,
    ) -> S3InternationalStateMachineResult:
        """Handle soft disqualification - polite rejection with option to continue."""
        current_data = dict(conversation.extracted_lead_data)
        questions = tenant_config.qualification_questions or []

        # Check if user wants to continue anyway
        continue_signals = [
            "yes",
            "sure",
            "continue",
            "go ahead",
            "proceed",
            "let's continue",
        ]
        if any(signal in user_message.lower() for signal in continue_signals):
            # Resume qualification
            current_index = conversation.current_step_index + 1
            if current_index < len(questions):
                next_q = questions[current_index]
                return S3InternationalStateMachineResult(
                    next_state="s3_qualifying",
                    action="ask_question",
                    step_index=current_index,
                    question_to_ask=next_q.instruction,
                    conversational_transition="Great, let's continue then! ",
                    quick_replies=self._get_quick_replies(next_q),
                    extracted_data=current_data,
                )

        # Complete with soft-disqualify status
        current_data["_s3_status"] = "soft_disqualified"

        # Prepare context for LLM
        context_str = f"Company: {tenant_config.company_name or 'Unknown'}\nDetails: {tenant_config.company_context}"

        return S3InternationalStateMachineResult(
            next_state=ConversationState.COMPLETED.value,
            action="show_summary",
            step_index=conversation.current_step_index,
            summary_data=self._build_s3_summary(current_data, questions),
            s3_lead_output=self._generate_and_store_s3_output(
                current_data, questions, history, context_str
            ),
            show_actions=True,
            extracted_data=current_data,
        )

    def _handle_completed_state(
            self,
            conversation: Conversation,
            user_message: str,
            tenant_config: TenantConfig,
            lang: str,
    ) -> S3InternationalStateMachineResult:
        """Handle completed state - answer questions and accept updates."""
        current_data = dict(conversation.extracted_lead_data)
        questions = tenant_config.qualification_questions or []

        # Check for phone update
        new_phone = self._extract_phone(user_message)
        if new_phone:
            current_data["phone"] = new_phone
            return S3InternationalStateMachineResult(
                next_state=ConversationState.COMPLETED.value,
                action="show_summary",
                step_index=conversation.current_step_index,
                summary_data=self._build_s3_summary(current_data, questions),
                show_actions=False,
                extracted_data=current_data,
            )

        # Check for questions
        if self._is_user_asking_question(user_message):
            return S3InternationalStateMachineResult(
                next_state=ConversationState.COMPLETED.value,
                action="answer_and_ask",
                step_index=conversation.current_step_index,
                question_to_ask="Is there anything else you'd like to know?",
                rag_query=user_message,
                show_actions=False,
                summary_data=self._build_s3_summary(current_data, questions),
                extracted_data=current_data,
            )

        # Check for button clicks and action logic
        if "yes" in user_message.lower() and "schedule" in user_message.lower():
            # "Yes, schedule a call"
            return S3InternationalStateMachineResult(
                next_state=ConversationState.COMPLETED.value,
                action="schedule_call",
                step_index=conversation.current_step_index,
                show_actions=False,
                summary_data=self._build_s3_summary(current_data, questions),
                s3_lead_output=self._get_stored_s3_output(current_data),
            )

        if "maybe later" in user_message.lower():
            # "Maybe later" -> Downgrade High to Medium, Medium to Low
            s3_out = self._get_stored_s3_output(current_data)
            priority = current_data.get("_s3_priority", "Medium")

            if s3_out:
                if priority == "High":
                    logger.info(
                        "S3: Downgrading High priority to Medium due to 'Maybe later'"
                    )
                    s3_out["lead_priority"] = "Medium"
                    s3_out["reasoning_summary"] += (
                        " (Downgraded from High: User clicked 'Maybe later')"
                    )
                    current_data["_s3_priority"] = "Medium"
                elif priority == "Medium":
                    logger.info(
                        "S3: Downgrading Medium priority to Low due to 'Maybe later'"
                    )
                    s3_out["lead_priority"] = "Low"
                    s3_out["reasoning_summary"] += (
                        " (Downgraded from Medium: User clicked 'Maybe later')"
                    )
                    current_data["_s3_priority"] = "Low"

                # Update extracted data mapping
                if priority in ["High", "Medium"]:
                    import json

                    current_data["_s3_lead_output_json"] = json.dumps(s3_out)

                    return S3InternationalStateMachineResult(
                        next_state=ConversationState.COMPLETED.value,
                        action="disqualify_lead",
                        step_index=conversation.current_step_index,
                        show_actions=False,
                        summary_data=self._build_s3_summary(current_data, questions),
                        s3_lead_output=s3_out,
                        extracted_data=current_data,
                    )

        return S3InternationalStateMachineResult(
            next_state=ConversationState.COMPLETED.value,
            action="acknowledge",
            step_index=conversation.current_step_index,
            show_actions=False,
            summary_data=self._build_s3_summary(current_data, questions),
            s3_lead_output=self._get_stored_s3_output(current_data),
        )

    # ==================== S3 ADAPTIVE LOGIC ====================

    def _determine_next_question(
            self,
            current_index: int,
            questions: List[QualificationQuestion],
            extracted_data: Dict[str, str],
            last_answer: str,
    ) -> int:
        """Determine which question to ask next using adaptive logic.

        S3 can skip or branch questions based on prior answers.
        For now, uses rule-based logic. Can be enhanced with LLM.
        """
        next_index = current_index + 1

        if next_index >= len(questions):
            return next_index

        # S3 Adaptive Rules:
        # 1. If budget already confirmed, skip budget revalidation
        # 2. If timeline is "just exploring", add probe questions
        # 3. Skip redundant questions

        budget_keywords = ["budget", "cost", "price", "spend", "afford"]
        timeline_exploring = ["exploring", "just looking", "not sure", "researching"]

        # Check if we already have budget info
        has_budget = any(
            key
            for key in extracted_data
            if any(bk in key.lower() for bk in budget_keywords)
        )

        next_q = questions[next_index]
        next_q_lower = next_q.instruction.lower()

        # Skip budget question if already answered
        if has_budget and any(bk in next_q_lower for bk in budget_keywords):
            logger.info(f"S3: Skipping budget question {next_index} - already answered")
            return self._determine_next_question(
                next_index, questions, extracted_data, last_answer
            )

        # For "just exploring" answers, don't skip intent questions
        last_answer_lower = last_answer.lower()
        if any(exp in last_answer_lower for exp in timeline_exploring):
            # Make sure we ask about motivation/timing
            logger.info(
                "S3: Detected 'exploring' signal - ensuring intent questions are asked"
            )

        return next_index

    def _should_soft_disqualify(
            self, user_message: str, extracted_data: Dict[str, str], current_index: int
    ) -> bool:
        """Detect if we should soft-disqualify this lead.

        Signals for soft disqualification:
        - Very low budget signals
        - Very distant timeline (years away)
        - Clear lack of authority
        - Explicit disinterest
        """
        msg_lower = user_message.lower()

        # Only consider after a few questions
        if current_index < 3:
            return False

        # Disqualify signals
        disqualify_signals = [
            "not interested",
            "no budget",
            "can't afford",
            "maybe next year",
            "maybe in a few years",
            "just browsing",
            "not serious",
            "no plans",
            "definitely not",
            "not looking to buy",
        ]

        for signal in disqualify_signals:
            if signal in msg_lower:
                logger.info(f"S3: Soft disqualify signal detected: {signal}")
                return True

        return False

    def _generate_context_aware_question(
            self,
            last_answer: Optional[str],
            next_question: QualificationQuestion,
            tenant_config: TenantConfig,
            history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        """Generate a natural, context-aware question using LLM.

        Combines acknowledgment of the previous answer with the next question
        to create a seamless conversational flow.
        """
        # If no last answer (start of flow), just return instruction or static intro
        if not last_answer:
            return next_question.instruction

        try:
            # Prepare context for LLM
            company_name = tenant_config.company_name or "our company"
            tone = getattr(tenant_config, "tone", "Professional")

            # Format history string
            history_str = ""
            if history:
                # Format: "User: ...\nAssistant: ..."
                # Skip system messages if any
                history_str = "\n".join(
                    [f"{msg['role'].title()}: {msg['content']}" for msg in history]
                )
            else:
                history_str = f"User: {last_answer}"

            language_name = get_language_name(tenant_config.language)

            prompt = f"""You are a skilled sales assistant for {company_name}.
Current Goal: Acknowledge the user's previous answer and transition smoothly to the next qualification question.

Conversation History:
{history_str}

Next Question Intent: "{next_question.instruction}"
Tone: {tone}
Language: {language_name}

Instructions:
1. Write a single, natural response that combines a brief acknowledgment of the answer and asks the next question.
2. Be conversational but professional. Do not use robotic "Got it" phrases repeatedly.
3. Keep it concise (1-2 sentences maximum).
4. Use the history to make relevant callbacks (e.g., "Since you mentioned X...").
5. Do NOT prefix with "Agent:" or "Bot:".
6. Respond ONLY in {language_name}.

Response:"""

            # Use LLM to generate response
            # Note: We use a lower temperature for consistent formatting but enough for creativity
            response = llm_service.generate_response(prompt)
            return response.strip().replace('"', "")

        except Exception as e:
            logger.error(f"S3: Error generating context-aware question: {e}")
            # Fallback to template if LLM fails
            transition = self._generate_transition_fallback(last_answer, next_question)
            return f"{transition} {next_question.instruction}"

    def _generate_dynamic_intro(self, tenant_config: TenantConfig) -> str:
        """Generate dynamic introduction asking for name and phone."""
        try:
            company_name = tenant_config.company_name or "our company"
            tone = getattr(tenant_config, "tone", "Professional")

            language_name = get_language_name(tenant_config.language)

            prompt = f"""You are a helpful sales assistant for {company_name}.
Tone: {tone}
Language: {language_name}
Task: Write a brief, conversational introduction for a new chat visitor.
Goal: Warmly welcome them to {company_name} and ask for their name and phone number to start the matching process.
Constraints: 
1. Maximum 2 short sentences.
2. Be inviting and human-like.
3. Don't be robotic.
4. Respond ONLY in {language_name}.
Response:"""

            response = llm_service.generate_response(prompt)
            return response.strip().replace('"', "")

        except Exception as e:
            logger.error(f"S3 Intro Generation Error: {e}")
            return "I'd love to help! Can you tell me your name and phone number?"

    def _generate_transition_fallback(
            self, answer_summary: Optional[str], next_question: QualificationQuestion
    ) -> str:
        """Fallback template-based transition (legacy logic)."""
        if not answer_summary:
            answer_summary = "that"

        # Determine question category
        next_q_lower = next_question.instruction.lower()
        category = "default"

        for dim, keywords in self.QUALIFICATION_DIMENSIONS.items():
            if any(kw in next_q_lower for kw in keywords):
                category = dim
                break

        templates = self.TRANSITION_TEMPLATES.get(
            category, self.TRANSITION_TEMPLATES["default"]
        )
        transition = random.choice(templates).format(answer_summary=answer_summary)

        return transition

    # ==================== S3 OUTPUT BUILDING ====================

    def _build_s3_summary(
            self, extracted_data: Dict[str, str], questions: List[QualificationQuestion]
    ) -> Dict[str, Any]:
        """Build S3 summary - same format as S1 for compatibility."""
        summary = {
            "name": extracted_data.get("name", "Not provided"),
            "phone": extracted_data.get("phone", "Not provided"),
        }

        q_num = 1
        for q in questions:
            if self._is_contact_question(q):
                continue

            field_key = f"q{q_num}"
            answer = extracted_data.get(q.id, "Not provided")
            summary[field_key] = {"label": q.label, "answer": answer}
            q_num += 1

        return summary

    def _generate_and_store_s3_output(
            self,
            current_data: Dict[str, str],
            questions: List[QualificationQuestion],
            history: Optional[List[Dict[str, str]]],
            context_str: str,
    ) -> Dict[str, Any]:
        """Generate S3 output and store valid parts in extracted_data for persistence."""
        s3_out = self._build_s3_lead_output(
            current_data, questions, history, context_str
        )

        # Store in extracted_data for persistence (as strings)
        if s3_out:
            import json

            current_data["_s3_priority"] = s3_out.get("lead_priority", "Medium")
            current_data["_s3_lead_output_json"] = json.dumps(s3_out)

        return s3_out

    def _get_stored_s3_output(
            self, current_data: Dict[str, str]
    ) -> Optional[Dict[str, Any]]:
        """Retrieve stored S3 output from extracted_data."""
        try:
            json_str = current_data.get("_s3_lead_output_json")
            if json_str:
                import json

                return json.loads(json_str)
        except Exception as e:
            logger.error(f"Failed to parse stored S3 output: {e}")
        return None

    def _build_s3_lead_output(
            self,
            extracted_data: Dict[str, str],
            questions: List[QualificationQuestion],
            history: Optional[List[Dict[str, str]]] = None,
            tenant_context: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build S3-specific rich output using LLM analysis."""

        # Prepare inputs for LLM
        lqc_data = [{"question_text": q.instruction, "id": q.id} for q in questions]

        # Call LLM Service
        try:
            analysis = self.llm_service.analyze_lead_priority(
                transcript=history or [],
                extracted_data=extracted_data,
                lqc=lqc_data,
                company_context=tenant_context,
            )
            return analysis
        except Exception as e:
            logger.error(f"S3 Lead Analysis Failed: {e}")
            return {
                "lead_priority": "Medium",
                "reasoning_summary": "Analysis unavailable.",
                "key_objections": [],
                "recommended_next_step": "Review transcript.",
            }

    # ==================== EXTRACTION METHODS (inherited from S1 patterns) ====================

    # ==================== EXTRACTION METHODS (Ported from S1) ====================

    def _extract_contact_info(self, message: str) -> Dict[str, str]:
        """Extract name and phone using LLM structured output for high accuracy."""
        extracted = {}

        try:
            # use LLM service with tool calling for structured extraction
            # This is more reliable than regex for diverse inputs
            info = llm_service.extract_lead_info(message, ["name", "phone_number"])

            # Process Name
            if "name" in info and info["name"]:
                name = info["name"].strip()
                # Basic validation
                if len(name) >= 2 and name.lower() not in ["none", "n/a", "unknown"]:
                    # Remove common false positives if LLM leaks them
                    if not any(
                            fp in name.lower()
                            for fp in ["contact", "number", "phone", "call me"]
                    ):
                        extracted["name"] = name.title()
                        logger.info(f"LLM Extracted Name: {extracted['name']}")

            # Fallback: If LLM missed name, try standalone and regex extractors
            if "name" not in extracted:
                name = self._extract_standalone_name(message)
                if not name:
                    name = self._extract_name_via_regex(message)
                if name:
                    extracted["name"] = name
                    logger.info(f"Fallback Extracted Name: {extracted['name']}")

            # Process Phone
            # Prioritize LLM extraction but validate with regex logic or specific format checks
            phone_candidate = info.get("phone_number") or info.get("phone")
            if phone_candidate:
                # Use existing phone validation logic on the LLM output
                valid_phone = self._extract_phone(phone_candidate)
                if valid_phone:
                    extracted["phone"] = valid_phone
                    logger.info(f"LLM Extracted Phone: {extracted['phone']}")

            # Fallback: If LLM missed phone, try regex on original message (Legacy robustness)
            if "phone" not in extracted:
                legacy_phone = self._extract_phone(message)
                if legacy_phone:
                    extracted["phone"] = legacy_phone
                    logger.info(f"Regex Fallback Phone: {extracted['phone']}")

        except Exception as e:
            logger.error(f"LLM Extraction failed: {e}")
            # Fallback to legacy regex extraction if LLM fails completely
            return self._extract_contact_info_legacy(message)

        return extracted

    def _extract_contact_info_legacy(self, message: str) -> Dict[str, str]:
        """Legacy extraction using regex/heuristics (Fallback)."""
        extracted = {}
        phone = self._extract_phone(message)
        if phone:
            extracted["phone"] = phone
            name = self._extract_name_before_phone(message, phone)
            if name:
                extracted["name"] = name

        if "name" not in extracted:
            name = self._extract_standalone_name(message)
            if not name:
                name = self._extract_name_via_regex(message)
            if name:
                extracted["name"] = name

        return extracted


    def _extract_phone(self, message: str) -> Optional[str]:
        """
        Extract and validate international phone numbers.

        Supports:
        - Indian numbers
        - US/UK/EU numbers
        - International formats
        - Numbers with spaces/dashes/brackets
        - +country_code formats

        Returns:
            E.164 formatted phone number
            Example: +919876543210
        """

        if not message:
            return None

        # Remove common phrases
        clean_message = message.lower()

        prefixes = [
            "my phone is",
            "my number is",
            "call me at",
            "reach me at",
            "phone number is",
            "contact me at",
        ]

        for prefix in prefixes:
            clean_message = clean_message.replace(prefix, "")

        clean_message = clean_message.strip()

        try:
            # Try extracting phone numbers from text
            matches = phonenumbers.PhoneNumberMatcher(clean_message, None)

            for match in matches:
                phone_number = match.number

                # Validate number
                if phonenumbers.is_valid_number(phone_number):
                    formatted = phonenumbers.format_number(
                        phone_number,
                        phonenumbers.PhoneNumberFormat.E164
                    )

                    logger.info(f"Valid international phone extracted: {formatted}")
                    return formatted

            logger.warning(f"No valid phone number found: {clean_message}")
            return None

        except Exception as e:
            logger.error(f"Phone extraction failed: {e}")
            return None

    def _extract_name_before_phone(self, message: str, phone: str) -> Optional[str]:
        """Extract name that appears before a phone number in patterns like 'name, phone' or 'name phone'."""
        # Find where the phone number starts in the message
        phone_pattern = re.escape(phone)
        match = re.search(phone_pattern, message)

        if not match:
            # Phone may have formatting, try finding first digit sequence
            for i, char in enumerate(message):
                if char.isdigit():
                    before_phone = message[:i].strip()
                    break
            else:
                return None
        else:
            before_phone = message[: match.start()].strip()

        if not before_phone:
            return None

        # Clean up common separators at the end
        before_phone = re.sub(r"[,\-:\s]+$", "", before_phone).strip()

        # Remove connecting phrases
        connecting_phrases = [
            r"\s+and\s+my\s+(number|phone|mobile)\s+is\s*$",
            r"\s+my\s+(number|phone|mobile)\s+is\s*$",
            r"\s+and\s+call\s+me\s+at\s*$",
            r"\s+contact\s+me\s+at\s*$",
            r"\s+reach\s+me\s+at\s*$",
            r"\s+and\s+$",
            r"\s+is\s*$",
        ]
        for pattern in connecting_phrases:
            before_phone = re.sub(
                pattern, "", before_phone, flags=re.IGNORECASE
            ).strip()

        # Skip if it looks like a common phrase
        skip_phrases = [
            "my name is",
            "i am",
            "i'm",
            "call me",
            "this is",
            "my phone is",
            "my number is",
            "contact me at",
            "reach me at",
            "phone",
            "number",
            "mobile",
        ]
        before_phone_lower = before_phone.lower()
        for phrase in skip_phrases:
            if phrase in before_phone_lower:
                before_phone = re.sub(
                    rf"({phrase})\s*", "", before_phone, flags=re.IGNORECASE
                ).strip()
                break

        # Validate logic
        words = before_phone.split()
        if len(words) == 0 or len(words) > 4:
            return None

        valid_words = []
        for word in words:
            clean_word = re.sub(r"[^a-zA-Z]", "", word)
            if len(clean_word) < 2:
                continue
            if clean_word.lower() in ["hi", "hello", "hey", "yes", "no", "ok"]:
                continue
            valid_words.append(word)

        if not valid_words:
            return None
        return " ".join(word.title() for word in valid_words)

    def _extract_name_via_regex(self, message: str) -> Optional[str]:
        """Extract name using regex patterns - fast and reliable."""

        # First, skip messages that are clearly NOT name introductions
        skip_patterns = [
            r"interested\s+in",  # "I'm interested in..."
            r"looking\s+for",  # "I'm looking for..."
            r"trying\s+to",  # "I'm trying to..."
            r"here\s+to",  # "I'm here to..."
            r"calling\s+about",  # "I'm calling about..."
            r"asking\s+about",  # "I'm asking about..."
            r"wondering",  # "I'm wondering..."
        ]

        message_lower = message.lower()
        for skip in skip_patterns:
            if re.search(skip, message_lower):
                logger.debug(f"Skipping name extraction - matched skip pattern: {skip}")
                return None

        # Common patterns for name introduction
        patterns = [
            # "my name is John" or "name is John"
            r"(?:my\s+)?name\s+is\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
            # "this is John" or "it's John"
            r"(?:this\s+is|it's|its)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
            # "call me John"
            r"call\s+me\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
            # "hey my name is john" (case insensitive, then capitalize)
            r"(?:my\s+)?name\s+is\s+([a-zA-Z]+(?:\s+[a-zA-Z]+)?)",
        ]

        # Expanded list of non-name words to filter out
        non_names = [
            "here",
            "there",
            "interested",
            "looking",
            "good",
            "fine",
            "great",
            "okay",
            "ok",
            "hello",
            "hi",
            "hey",
            "yes",
            "no",
            "sure",
            "thanks",
            "thank",
            "please",
            "just",
            "also",
            "actually",
            "really",
            "very",
            "quite",
            "well",
            "calling",
            "asking",
            "wondering",
            "trying",
            "hoping",
            "waiting",
            "new",
            "old",
            "happy",
            "sad",
            "busy",
            "free",
            "available",
            "my",
            "number",
        ]

        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                name = match.group(1).strip()
                # Capitalize properly
                name = name.title()
                # Filter out common non-name words
                if name.lower() not in non_names and len(name) >= 2 and len(name) <= 50:
                    return name

        return None

    def _extract_standalone_name(self, message: str) -> Optional[str]:
        """Extract name when user just sends their name as a standalone message."""
        message = message.strip()

        # If message is very short (1-3 words), it might be a name
        words = message.split()

        if len(words) > 4:
            # Too long to be just a name
            return None

        # Expanded list of non-name words
        non_names = [
            "hi",
            "hello",
            "hey",
            "yes",
            "no",
            "sure",
            "ok",
            "okay",
            "thanks",
            "thank",
            "please",
            "help",
            "good",
            "great",
            "fine",
            "well",
            "interested",
            "looking",
            "what",
            "why",
            "how",
            "when",
            "where",
            "who",
            "which",
            "number",
            "my",
        ]

        # Check for "my name is X" pattern (case insensitive)
        match = re.match(r"^(?:my\s+)?name\s+is\s+(.+)$", message, re.IGNORECASE)
        if match:
            name = match.group(1).strip().title()
            if name.lower() not in non_names and 2 <= len(name) <= 50:
                return name

        # For very short messages (1-2 words), treat as potential name
        if len(words) <= 2:
            # Check first word looks like a name (capitalized or all lowercase)
            first_word = words[0]

            # Remove punctuation
            first_word_clean = re.sub(r"[^a-zA-Z]", "", first_word)

            if len(first_word_clean) < 2:
                return None

            # Skip common non-name words
            if first_word_clean.lower() in non_names:
                return None

            # Build name from words
            potential_name = " ".join(
                w.strip() for w in words if re.match(r"^[a-zA-Z]+$", w)
            )

            if potential_name and 2 <= len(potential_name) <= 50:
                return potential_name.title()

        return None

    def _extract_answer_summary(
            self, message: str, question: QualificationQuestion
    ) -> Optional[str]:
        """Extract one-word summary from user's answer using LLM."""
        try:
            prompt = f"""Summarize the user's answer in 1-3 words maximum.

Question asked: "{question.instruction}"
User's answer: "{message}"

One-word summary (e.g., "$50k-100k", "3 months", "Yes", "Downtown"):"""

            response = llm_service.generate_response(prompt)
            summary = response.strip().strip("\"'")

            summary = clean_llm_text(summary) or summary
            # Limit to reasonable length
            if len(summary) > 30:
                summary = summary[:30]
            return summary if summary else message[:20]

        except Exception as e:
            logger.error(f"Failed to extract answer summary: {e}")
            return message[:20] if message else None

    # ==================== INTENT DETECTION (Ported from S1) ====================

    def _is_user_asking_question(self, message: str) -> bool:
        """Detect if user is asking a question vs answering."""
        message_lower = message.lower().strip()

        # Check for explicit question indicators
        for indicator in self.QUESTION_INDICATORS:
            if indicator in message_lower:
                return True

        # Check for deliberation/clarification indicators
        for indicator in self.DELIBERATION_INDICATORS:
            if indicator in message_lower:
                logger.info(f"Detected deliberation phrase: '{indicator}'")
                return True

        # Longer messages (50+ chars) with multiple clauses often indicate
        # the user is seeking clarification, not just answering
        if len(message) >= 50 and (
                "," in message
                or " and " in message_lower
                or " but " in message_lower
                or " or " in message_lower
        ):
            # Check if it starts with 'i need' or 'i want' - these could be info requests
            if message_lower.startswith(("i need ", "i want ", "i would like ")):
                logger.info("Detected info request in longer message")
                return True

        return False

    def _is_user_answering(
            self, message: str, current_question: QualificationQuestion
    ) -> bool:
        """Detect if user is answering the qualification question."""
        message_lower = message.lower().strip()

        # Short messages without question marks are likely answers
        if len(message) < 50 and "?" not in message:
            # Check it's not just a greeting
            greetings = [
                "hi",
                "hello",
                "hey",
                "good morning",
                "good afternoon",
                "good evening",
            ]
            if message_lower not in greetings:
                return True

        # Check for answer indicators
        for indicator in self.ANSWER_INDICATORS:
            if indicator in message_lower:
                return True

        # Check if message matches any quick reply options
        quick_replies = self._get_quick_replies(current_question)
        for reply in quick_replies:
            if reply.lower() in message_lower or message_lower in reply.lower():
                return True

        # Check for keywords related to the question's collected fields
        for field in current_question.collects:
            if field.lower() in message_lower:
                return True

        # If message is short and doesn't look like a question, treat as answer
        if len(message) < 100 and "?" not in message:
            return True

        return False

    # ==================== HELPER METHODS (Ported from S1) ====================

    def _is_contact_question(self, question: QualificationQuestion) -> bool:
        """Check if a question collects name or phone (already handled in AWAITING_CONTACT)."""
        collects_lower = [f.lower() for f in question.collects]
        is_contact = "name" in collects_lower or "phone" in collects_lower
        return is_contact

    def _get_first_qualifying_index(
            self, questions: List[QualificationQuestion]
    ) -> int:
        """Find index of first question that doesn't collect name/phone."""
        for i, q in enumerate(questions):
            if not self._is_contact_question(q):
                return i
        return len(questions)

    def _get_quick_replies(self, question: QualificationQuestion) -> List[str]:
        """Get quick reply options for a question."""
        if question.quick_replies:
            return [qr.text for qr in question.quick_replies]
        return []

    def _rephrase_question(self, question: QualificationQuestion, attempt: int) -> str:
        """Rephrase question for re-asking with natural variation."""
        base = question.instruction

        # More natural rephrasing options
        rephrase_options = [
            [
                f"Let me ask again - {base}",
                f"Sorry, didn't quite catch that. {base}",
                f"One more time - {base}",
            ],
            [
                f"Just to make sure we're on the same page - {base}",
                f"Before we move on - {base}",
                f"I want to make sure I understand - {base}",
            ],
        ]


        options = rephrase_options[min(attempt - 1, len(rephrase_options) - 1)]
        return random.choice(options)


# Singleton instance
s3_international_state_machine = S3InternationalChatStateMachine()
