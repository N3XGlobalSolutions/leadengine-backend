"""State Machine for Sales Bot Qualification Flow.

This service provides deterministic flow control for the sales bot.
The LLM is only responsible for generating natural language - NOT flow control.

Key Principles:
1. Backend controls flow - LLM generates text
2. Phone number is STRICTLY MANDATORY
3. After 2 ignores, move to next question
4. Summary is created by backend, not LLM
"""

import random
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.core.logging_config import get_logger
from app.models.conversation import Conversation, ConversationState
from app.models.tenant import TenantConfig, QualificationQuestion
from app.services.ai.llm_service import llm_service
from app.services.chat.text_cleaners import clean_llm_text
from app.utils.localization import get_message, get_language_name

logger = get_logger(__name__)

# Constants
MAX_IGNORE_COUNT = 2  # Per user request
MAX_NAME_ASK_COUNT = 2  # Ask for name at most 2 times


@dataclass
class StateMachineResult:
    """Result from state machine processing."""
    
    next_state: str
    action: str  # "ask_contact", "ask_question", "answer_rag", "answer_and_ask", "show_summary", "ended"
    step_index: int = 0
    question_to_ask: Optional[str] = None
    rag_query: Optional[str] = None
    summary_data: Optional[Dict[str, Any]] = None
    quick_replies: List[str] = field(default_factory=list)
    show_actions: bool = False
    extracted_data: Dict[str, str] = field(default_factory=dict)
    increment_name_ask: bool = False  # Signal to increment name_ask_count


class ChatStateMachine:
    """Deterministic state machine for sales bot qualification flow."""
    
    # Phone number regex patterns (multiple formats)
    PHONE_PATTERNS = [
        r'[6-9]\d{9}',                                          # Indian mobile: 9876543210
        r'\+91[-.\s]?[6-9]\d{9}',                               # Indian with code: +91 9876543210
    ]
    
    # Question indicators for detecting user questions
    QUESTION_INDICATORS = [
        '?',                    # Explicit question mark
        'what is', 'what are', 'what\'s', 'whats',
        'how much', 'how many', 'how do', 'how can', 'how is', 'how does',
        'when is', 'when can', 'when do',
        'where is', 'where can', 'where do',
        'why is', 'why do', 'why are', 'why would',
        'can you', 'can i', 'could you', 'could i', 'can u',
        'do you', 'does it', 'is there', 'are there', 'do u',
        'tell me about','tell me','explain', 'describe',
        'i want to know', 'i need to know',
        'what about', 'how about',
        # Hypothetical scenarios (from failing tests)
        'suppose', 'suppose that', 'let\'s say', 'lets say',
        'in case', 'in case that', 'imagine if', 'imagine',
        'what if', 'what would',
        # Polite modals (from failing tests)
        'would it be', 'would it be possible', 'might you', 'might you have',
        'could you perhaps', 'may i ask', 'may i know',
        'is it possible', 'would you mind',
        # Imperative information requests (from failing tests)
        'show me', 'let me see', 'let me know', 'lemme know', 'lemme see',
        'i\'d like to see', 'id like to see', 'wanna see',
        # Indirect questions (enhanced)
        'wondering if', 'wondering whether', 'wondering about',
        'curious about', 'curious if', 'curious to know',
        'trying to understand', 'trying to figure',
        # Comparison questions
        'which is', 'which one', 'which would',
        # Typos/casual (from failing tests)
        'wat is', 'wat about', 'how much iz',
        'wanna know', 'wanna see',
    ]
    
    # Deliberation/request phrases - user needs more info before deciding
    DELIBERATION_INDICATORS = [
        'to decide', 'to determine', 'before i decide', 'before deciding',
        'whether or not', 'whether i should', 'should i',
        'i need more', 'i need detailed', 'need more info', 'need details',
        'send me', 'share with me', 'provide me', 'give me more',
        'i\'m not sure', 'i am not sure', 'not sure if', 'not sure how',
        'i\'m confused', 'i don\'t understand', 'don\'t get',
        'clarify', 'elaborate', 'more information', 'more details',
        'what options', 'what choices', 'available options',
        'can you tell', 'could you share', 'would like to know', 'would love to',
        'depends on', 'it depends', 'need to think',
        # Casual uncertainty patterns (from failing tests)
        'dunno', 'idk', 'i dk', 'you tell me', 'u tell me',
        'whatever works', 'anything works', 'up to you', 'your call',
        # Implied information needs
        'still waiting', 'still unclear', 'not clear on', 'not clear about',
        'need clarification', 'need more info',
        'confused about', 'unclear about',
        # Embedded question patterns
        'trying to figure out if', 'need to know whether',
        'wondering what', 'wondering how', 'wondering when',
        'i was wondering', 'was wondering',
        'interested in learning', 'would love to hear',
    ]
    
    # Answer indicators - signals user is likely answering
    # Note: Short phrases like 'i want', 'i need' are only treated as answers
    # if the message is short and doesn't contain deliberation indicators
    ANSWER_INDICATORS = [
        'my name is', 'i am ', 'i\'m ','i m ',
        'my number is', 'my phone is', 'call me at', 'reach me at',
        'yes', 'no', 'maybe', 'sure', 'ok', 'okay',
        'i prefer', 'i like',
        'around', 'about', 'approximately',
        'within', 'under', 'over', 'between',
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
        tenant_config: TenantConfig
    ) -> StateMachineResult:
        """
        Process user message and determine next state.
        
        This is the main entry point for the state machine.
        Returns what action to take and what state to transition to.
        """
        current_state = conversation.current_state
        logger.info(f"Processing message in state: {current_state}, step: {conversation.current_step_index}")
        lang = tenant_config.language or "en"
        
        # Handle based on current state
        if current_state == ConversationState.GREETING.value:
            return self._handle_greeting_state(conversation, user_message, tenant_config, lang)
        
        elif current_state == ConversationState.AWAITING_CONTACT.value:
            return self._handle_contact_state(conversation, user_message, tenant_config, lang)
        
        elif current_state == ConversationState.QUALIFYING.value:
            return self._handle_qualifying_state(conversation, user_message, tenant_config, lang)
        
        elif current_state == ConversationState.COMPLETED.value:
            return self._handle_completed_state(conversation, user_message, tenant_config, lang)
        
        elif current_state == ConversationState.ENDED.value:
            return StateMachineResult(
                next_state=ConversationState.ENDED.value,
                action="ended",
                step_index=conversation.current_step_index
            )
        
        # Default fallback
        return self._handle_greeting_state(conversation, user_message, tenant_config)
    
    def _handle_greeting_state(
        self,
        conversation: Conversation,
        user_message: str,
        tenant_config: TenantConfig,
        lang: str
    ) -> StateMachineResult:
        """Handle initial greeting - transition to contact collection or qualifying."""
        # Try to extract any contact info provided upfront
        extracted = self._extract_contact_info(user_message)
        
        questions = tenant_config.qualification_questions or []
        
        # If user already provided phone in first message, skip to qualifying!
        if extracted.get('phone'):
            first_qualifying_index = self._get_first_qualifying_index(questions)
            
            if first_qualifying_index < len(questions):
                first_q = questions[first_qualifying_index]
                quick_replies = self._get_quick_replies(first_q)
                
                return StateMachineResult(
                    next_state=ConversationState.QUALIFYING.value,
                    action="ask_question",
                    step_index=first_qualifying_index,
                    question_to_ask=first_q.instruction,
                    quick_replies=quick_replies,
                    extracted_data=extracted
                )
            else:
                # No qualifying questions - go to completed
                return StateMachineResult(
                    next_state=ConversationState.COMPLETED.value,
                    action="show_summary",
                    step_index=0,
                    summary_data=self._build_summary(extracted, questions),
                    show_actions=True,
                    extracted_data=extracted
                )
        
        prompt_text = get_message(lang, "ask_contact")

        # No phone yet - go to AWAITING_CONTACT
        return StateMachineResult(
            next_state=ConversationState.AWAITING_CONTACT.value,
            action="ask_contact",
            step_index=0,
            question_to_ask=f"{tenant_config.welcome_message} {prompt_text}",
            extracted_data=extracted
        )
    
    def _handle_contact_state(
        self,
        conversation: Conversation,
        user_message: str,
        tenant_config: TenantConfig,
        lang: str
    ) -> StateMachineResult:
        """
        Handle contact collection state - PHONE IS MANDATORY.
        Will not proceed without a valid phone number.
        """
        # Extract contact info from current message
        extracted = self._extract_contact_info(user_message)
        
        # Merge with existing data - but prioritize NEW valid names
        current_data = dict(conversation.extracted_lead_data)
        
        # Special handling for name: if we have a newly extracted name,
        # OR if the current name looks like a false positive, prefer the new one
        bad_name_indicators = ['interested', 'looking', 'hello', 'hi', 'hey']
        current_name = current_data.get('name', '')
        is_current_name_bad = any(ind in current_name.lower() for ind in bad_name_indicators) if current_name else True
        
        if extracted.get('name'):
            # Always update with newly extracted name
            current_data['name'] = extracted['name']
        elif is_current_name_bad:
            # If current name looks bad and user sent a short message that could be a name
            # Try to parse it as a standalone name
            possible_name = self._extract_standalone_name(user_message)
            if possible_name:
                current_data['name'] = possible_name
        
        # Update phone if newly extracted
        if extracted.get('phone'):
            current_data['phone'] = extracted['phone']
        
        # Check if we have phone
        has_phone = bool(current_data.get('phone'))
        has_name = bool(current_data.get('name'))
        
        logger.info(f"Contact check - has_phone: {has_phone}, has_name: {has_name}, extracted: {extracted}")
        
        if has_phone:
            # Phone collected! Now check if we need to ask for name
            name_ask_count = conversation.name_ask_count
            
            if not has_name and name_ask_count < MAX_NAME_ASK_COUNT:
                lang = tenant_config.language or "en"
                name_prompts = get_message(lang, "got_phone_ask_name")

                
                return StateMachineResult(
                    next_state=ConversationState.AWAITING_CONTACT.value,
                    action="ask_name",  # New action type for name-specific ask
                    step_index=0,
                    question_to_ask=random.choice(name_prompts),
                    extracted_data=current_data,
                    increment_name_ask=True  # Signal to increment name_ask_count
                )
            
            # Either has name OR exceeded name ask attempts - proceed to qualification!
            questions = tenant_config.qualification_questions or []
            
            # IMPORTANT: Skip questions that collect name/phone (already done!)
            first_qualifying_index = self._get_first_qualifying_index(questions)
            
            if first_qualifying_index < len(questions):
                first_q = questions[first_qualifying_index]
                quick_replies = self._get_quick_replies(first_q)
                
                return StateMachineResult(
                    next_state=ConversationState.QUALIFYING.value,
                    action="ask_question",
                    step_index=first_qualifying_index,  # Start at first non-contact question
                    question_to_ask=first_q.instruction,
                    quick_replies=quick_replies,
                    extracted_data=current_data
                )
            else:
                # All questions are contact questions OR no questions - go to completed
                return StateMachineResult(
                    next_state=ConversationState.COMPLETED.value,
                    action="show_summary",
                    step_index=0,
                    summary_data=self._build_summary(current_data, questions),
                    show_actions=True,
                    extracted_data=current_data
                )
        else:
            # Check if user is asking a question instead of providing info
            if self._is_user_asking_question(user_message):
                lang = tenant_config.language or "en"
                phone_asks = get_message(lang, "need_phone_to_continue")
                phone_question = random.choice(phone_asks)
                
                return StateMachineResult(
                    next_state=ConversationState.AWAITING_CONTACT.value,
                    action="answer_and_ask",
                    step_index=0,
                    question_to_ask=phone_question,
                    rag_query=user_message,
                    extracted_data=current_data
                )
            
            name = current_data.get('name', '')
            if has_name:
                phone_prompts = get_message(lang, "phone_ask_with_name", name=name)
                if isinstance(phone_prompts, list):
                    prompt_text = random.choice(phone_prompts)
                else:
                    prompt_text = phone_prompts
            else:
                # Still no phone - ask again with localization
                prompt_text = get_message(lang, "need_phone_to_continue", name=name)
                if isinstance(prompt_text, list):
                    prompt_text = random.choice(prompt_text)
                
            return StateMachineResult(
                next_state=ConversationState.AWAITING_CONTACT.value,
                action="ask_contact",
                step_index=0,
                question_to_ask=prompt_text,
                extracted_data=current_data
            )
        
        # Phone provided, but maybe no name?
        current_name = current_data.get('name', '')
        if not current_name and conversation.name_ask_count < MAX_NAME_ASK_COUNT:
            # Got phone, but no name - ask for name with localization
            prompt_text = get_message(lang, "got_phone_ask_name")
            if isinstance(prompt_text, list):

                prompt_text = random.choice(prompt_text)

            return StateMachineResult(
                next_state=ConversationState.AWAITING_CONTACT.value,
                action="ask_name",
                step_index=0,
                question_to_ask=prompt_text,
                extracted_data=current_data,
                increment_name_ask=True
            )
    
    def _is_contact_question(self, question: QualificationQuestion) -> bool:
        """Check if a question collects name or phone (already handled in AWAITING_CONTACT).
        
        These are system-generated questions that should be skipped because the
        state machine handles name/phone collection natively in GREETING and AWAITING_CONTACT states.
        """
        collects_lower = [f.lower() for f in question.collects]
        is_contact = 'name' in collects_lower or 'phone' in collects_lower
        
        if is_contact:
            logger.debug(f"Skipping system question '{question.label}' (collects: {question.collects}) - handled by state machine")
        
        return is_contact
    
    def _get_first_qualifying_index(self, questions: List[QualificationQuestion]) -> int:
        """Find index of first question that doesn't collect name/phone.
        
        This ensures backward compatibility with configs that still have questions 0-1
        (legacy system questions for name/phone collection).
        """
        for i, q in enumerate(questions):
            if not self._is_contact_question(q):
                logger.info(f"First qualifying question is at index {i}: '{q.label}'")
                return i
        
        logger.info("All questions are contact questions - moving directly to COMPLETED")
        return len(questions)  # All questions are contact questions
    
    def _handle_qualifying_state(
        self,
        conversation: Conversation,
        user_message: str,
        tenant_config: TenantConfig,
        lang: str
    ) -> StateMachineResult:
        """Handle qualification questions flow."""
        questions = tenant_config.qualification_questions or []
        current_index = conversation.current_step_index
        current_data = dict(conversation.extracted_lead_data)
        
        if current_index >= len(questions):
            # All questions done - show summary
            return StateMachineResult(
                next_state=ConversationState.COMPLETED.value,
                action="show_summary",
                step_index=current_index,
                summary_data=self._build_summary(current_data, questions),
                show_actions=True,
                extracted_data=current_data
            )
        
        current_question = questions[current_index]
        is_user_asking = self._is_user_asking_question(user_message)
        is_user_answering = self._is_user_answering(user_message, current_question)
        
        logger.info(f"Qualifying - Q{current_index+1}, is_asking: {is_user_asking}, is_answering: {is_user_answering}")
        
        # IMPORTANT: Check if user is asking a question FIRST (priority)
        # This ensures questions ending with '?' are handled via RAG
        if is_user_asking:
            # User asked a question - answer via RAG and weave in qualification question
            quick_replies = self._get_quick_replies(current_question)
            
            return StateMachineResult(
                next_state=ConversationState.QUALIFYING.value,
                action="answer_and_ask",
                step_index=current_index,
                question_to_ask=current_question.instruction,
                rag_query=user_message,
                quick_replies=quick_replies,
                extracted_data=current_data
            )
        
        elif is_user_answering:
            # User answered - extract and move to next question
            answer_summary = self._extract_answer_summary(user_message, current_question)
            
            if answer_summary:
                # Store the answer with question ID
                current_data[current_question.id] = answer_summary
            
            next_index = current_index + 1
            
            if next_index >= len(questions):
                # All questions answered - show summary
                return StateMachineResult(
                    next_state=ConversationState.COMPLETED.value,
                    action="show_summary",
                    step_index=next_index,
                    summary_data=self._build_summary(current_data, questions),
                    show_actions=True,
                    extracted_data=current_data
                )
            else:
                # Move to next question
                next_question = questions[next_index]
                quick_replies = self._get_quick_replies(next_question)
                
                return StateMachineResult(
                    next_state=ConversationState.QUALIFYING.value,
                    action="ask_question",
                    step_index=next_index,
                    question_to_ask=next_question.instruction,
                    quick_replies=quick_replies,
                    extracted_data=current_data
                )
        
        else:
            # User ignored the question (neither answering nor asking)
            ignore_count = conversation.step_ignore_count + 1
            
            if ignore_count >= MAX_IGNORE_COUNT:
                # Max ignores reached - force move to next question
                current_data[current_question.id] = "Not provided"
                next_index = current_index + 1
                
                if next_index >= len(questions):
                    # All questions done
                    return StateMachineResult(
                        next_state=ConversationState.COMPLETED.value,
                        action="show_summary",
                        step_index=next_index,
                        summary_data=self._build_summary(current_data, questions),
                        show_actions=True,
                        extracted_data=current_data
                    )
                else:
                    next_question = questions[next_index]
                    quick_replies = self._get_quick_replies(next_question)
                    
                    return StateMachineResult(
                        next_state=ConversationState.QUALIFYING.value,
                        action="ask_question",
                        step_index=next_index,
                        question_to_ask=next_question.instruction,
                        quick_replies=quick_replies,
                        extracted_data=current_data
                    )
            else:
                # Re-ask the same question with different phrasing
                rephrased = self._rephrase_question(current_question, ignore_count)
                quick_replies = self._get_quick_replies(current_question)
                
                return StateMachineResult(
                    next_state=ConversationState.QUALIFYING.value,
                    action="ask_question",
                    step_index=current_index,
                    question_to_ask=rephrased,
                    quick_replies=quick_replies,
                    extracted_data=current_data
                )
    
    def _handle_completed_state(
        self,
        conversation: Conversation,
        user_message: str,
        tenant_config: TenantConfig,
        lang: str
    ) -> StateMachineResult:
        """Handle completed state - answer questions and accept updates.
        
        After summary is shown, user can:
        1. Ask questions → answer via RAG
        2. Update phone/name → update lead data
        3. Click action buttons → handled by frontend
        """
        current_data = dict(conversation.extracted_lead_data)
        questions = tenant_config.qualification_questions or []
        
        # Check for action button clicks (or typed equivalents)
        msg_lower = user_message.lower().strip()
        
        if "maybe later" in msg_lower or "no thanks" in msg_lower:
            return StateMachineResult(
                next_state=ConversationState.ENDED.value,
                action="disqualify_lead",
                step_index=conversation.current_step_index,
                extracted_data=current_data
            )
            
        if "schedule a call" in msg_lower or "yes" == msg_lower:
             return StateMachineResult(
                next_state=ConversationState.ENDED.value,
                action="schedule_call",
                step_index=conversation.current_step_index,
                extracted_data=current_data
            )

        # Check if user is updating their contact info (phone/name)
        new_phone = self._extract_phone(user_message)
        if new_phone:
            current_data['phone'] = new_phone
            logger.info(f"Phone updated in COMPLETED state: {new_phone}")
            
            return StateMachineResult(
                next_state=ConversationState.COMPLETED.value,
                action="show_summary",  # Re-show summary with updated data
                step_index=conversation.current_step_index,
                summary_data=self._build_summary(current_data, questions),
                show_actions=True,
                extracted_data=current_data
            )
        
        # Check if user is providing their name
        new_name = self._extract_name_via_llm(user_message)
        if new_name and len(user_message) < 50:  # Short message = likely just name
            current_data['name'] = new_name
            logger.info(f"Name updated in COMPLETED state: {new_name}")
            
            return StateMachineResult(
                next_state=ConversationState.COMPLETED.value,
                action="show_summary",
                step_index=conversation.current_step_index,
                summary_data=self._build_summary(current_data, questions),
                show_actions=True,
                extracted_data=current_data
            )
        
        # Check if user is asking a question → answer via RAG
        if self._is_user_asking_question(user_message):
            logger.info(f"User asking question in COMPLETED state, answering via RAG")
            
            return StateMachineResult(
                next_state=ConversationState.COMPLETED.value,
                action="answer_and_ask",  # Will answer via RAG
                step_index=conversation.current_step_index,
                question_to_ask="Is there anything else you'd like to know? Or would you like to schedule a call?",
                rag_query=user_message,
                show_actions=True,
                summary_data=self._build_summary(current_data, questions),
                extracted_data=current_data
            )
        
        # Default: just acknowledge and show actions
        return StateMachineResult(
            next_state=ConversationState.COMPLETED.value,
            action="acknowledge",
            step_index=conversation.current_step_index,
            show_actions=True,
            summary_data=self._build_summary(current_data, questions)
        )
    
    # ==================== EXTRACTION METHODS ====================
    
    def _extract_contact_info(self, message: str) -> Dict[str, str]:
        """Extract name and phone from message. Uses LLM for name, regex for phone."""
        extracted = {}
        
        # Extract phone using regex (reliable)
        phone = self._extract_phone(message)
        if phone:
            extracted['phone'] = phone
            
            # SPECIAL CASE: Check for "name, phone" or "name phone" pattern
            # When user sends something like "nakul, 9434964946" or "John 9876543210"
            name_from_pattern = self._extract_name_before_phone(message, phone)
            if name_from_pattern:
                extracted['name'] = name_from_pattern
                logger.info(f"Extracted name from name+phone pattern: {name_from_pattern}")
                return extracted
        
        # Extract name using LLM (per user request)
        name = self._extract_name_via_llm(message)
        if name:
            extracted['name'] = name
        
        return extracted
    
    def _extract_name_before_phone(self, message: str, phone: str) -> Optional[str]:
        """Extract name that appears before a phone number in patterns like 'name, phone' or 'name phone'.
        
        Handles formats:
        - "nakul, 9434964946"
        - "John 9876543210"
        - "Mukilan 8798798778"
        - "rahul - 9876543210"
        """
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
            before_phone = message[:match.start()].strip()
        
        if not before_phone:
            return None
        
        # Clean up common separators at the end
        before_phone = re.sub(r'[,\-:\s]+$', '', before_phone).strip()
        
        # Remove connecting phrases that link name to phone (e.g., "and my number is", "my phone is")
        connecting_phrases = [
            r'\s+and\s+my\s+(number|phone|mobile)\s+is\s*$',
            r'\s+my\s+(number|phone|mobile)\s+is\s*$',
            r'\s+and\s+call\s+me\s+at\s*$',
            r'\s+contact\s+me\s+at\s*$',
            r'\s+reach\s+me\s+at\s*$',
            r'\s+and\s+$',
        ]
        for pattern in connecting_phrases:
            before_phone = re.sub(pattern, '', before_phone, flags=re.IGNORECASE).strip()
        
        # Skip if it looks like a common phrase (not a name)
        skip_phrases = [
            'my name is', 'i am', "i'm", 'call me', 'this is',
            'my phone is', 'my number is', 'contact me at', 'reach me at',
            'phone', 'number', 'mobile'
        ]
        before_phone_lower = before_phone.lower()
        for phrase in skip_phrases:
            if phrase in before_phone_lower:
                # Remove the phrase and get the actual name
                before_phone = re.sub(rf'({phrase})\s*', '', before_phone, flags=re.IGNORECASE).strip()
                break
        
        # Validate: Should be 1-3 words, mostly alphabetic
        words = before_phone.split()
        if len(words) == 0 or len(words) > 4:
            return None
        
        # Check that words look like names (primarily letters)
        for word in words:
            clean_word = re.sub(r'[^a-zA-Z]', '', word)
            if len(clean_word) < 2:  # Too short to be a name
                return None
            if clean_word.lower() in ['hi', 'hello', 'hey', 'yes', 'no', 'ok', 'okay']:
                return None
        
        # Capitalize properly
        name = ' '.join(word.title() for word in words)
        
        logger.info(f"Extracted name before phone: '{name}' from message: '{message[:50]}'")
        return name
    
    def _extract_phone(self, message: str) -> Optional[str]:
        """Extract phone number using regex patterns.
        
        Validates:
        - Indian mobile: 10 digits starting with 6-9
        - With country code: +91 followed by 10 digits
        
        Rejects:
        - Numbers with wrong digit count
        - Numbers not matching Indian mobile format
        """
        # Remove common prefixes that might confuse extraction
        clean_message = message.replace('my phone is', '').replace('my number is', '')
        clean_message = clean_message.replace('call me at', '').replace('reach me at', '')
        clean_message = clean_message.strip()
        
        # Extract all digits from the message
        all_digits = re.sub(r'[^\d]', '', clean_message)
        
        # Handle +91 prefix case
        if clean_message.startswith('+91') or clean_message.startswith('91') or all_digits.startswith('91'):
            # Remove the 91 prefix for validation
            if all_digits.startswith('91') and len(all_digits) == 12:
                phone_digits = all_digits[2:]  # Remove 91
            else:
                phone_digits = all_digits
        else:
            phone_digits = all_digits
        
        # Validate Indian mobile number: exactly 10 digits starting with 6-9
        if len(phone_digits) == 10 and phone_digits[0] in '6789':
            logger.info(f"Valid Indian phone extracted: {phone_digits}")
            return phone_digits
        
        # If user provided +91 and 10 valid digits
        if len(all_digits) == 12 and all_digits.startswith('91') and all_digits[2] in '6789':
            logger.info(f"Valid Indian phone with +91: {all_digits[2:]}")
            return all_digits[2:]  # Return just the 10-digit number
        
        # STRICT: Reject anything that doesn't match exactly
        logger.warning(f"Invalid phone format rejected: '{clean_message}' (digits: {all_digits}, length: {len(all_digits)})")
        return None
    
    def _extract_name_via_llm(self, message: str) -> Optional[str]:
        """Extract name using regex patterns first, then LLM as backup."""
        
        # FALSE POSITIVE FILTER: Reject common conversational phrases before extraction
        FALSE_POSITIVE_PATTERNS = [
            r'\bmy\s+number\b',        # "my number"
            r'\bmy\s+phone\b',         # "my phone"
            r'\bthis\s+is\s+my\b',     # "this is my number/phone"
            r'\bnumber\s+is\b',        # "number is..."
            r'\bphone\s+is\b',         # "phone is..."
            r'\bcall\s+me\s+at\b',     # "call me at..."
            r'\breach\s+me\b',         # "reach me at..."
        ]
        
        message_lower = message.lower()
        # Check if message is primarily about sharing a phone number
        for pattern in FALSE_POSITIVE_PATTERNS:
            if re.search(pattern, message_lower):
                # If message matches a phone-sharing pattern, don't extract name from it
                # UNLESS there's a clear name indicator too
                if not re.search(r'\b(my name is|i am|i\'m|call me)\s+[A-Z]', message, re.IGNORECASE):
                    logger.debug(f"Skipping name extraction - message appears to be phone-sharing: {message[:50]}")
                    return None
        
        # First try regex patterns (fast and reliable)
        name = self._extract_name_via_regex(message)
        if name:
            logger.info(f"Extracted name via regex: {name}")
            return name
        
        # Fallback to LLM for complex cases
        try:
            prompt = f"""Extract ONLY the person's actual name from this message.
DO NOT extract phrases like "my number", "this is my", "my phone" or other conversational text.
Only extract real human names (first name or full name).
If no actual person's name is mentioned, respond with: NONE

Message: "{message}"

Name:"""
            
            response = self.llm_service.generate_response(prompt)
            name = response.strip()
            
            # Clean up response
            if name.upper() == 'NONE' or len(name) < 2 or len(name) > 50:
                return None
            
            # Remove common prefixes the LLM might include
            name = re.sub(r'^(name:|the name is|my name is)\\s*', '', name, flags=re.IGNORECASE)
            name = name.strip().strip('"\'')
            
            # Additional false positive check on extracted name
            FALSE_POSITIVE_NAMES = [
                'my number', 'my phone', 'this is', 'number', 'phone',
                'contact', 'mobile', 'cell', 'call me'
            ]
            if any(fp in name.lower() for fp in FALSE_POSITIVE_NAMES):
                logger.debug(f"Rejected false positive name: {name}")
                return None
            
            if name and len(name) >= 2:
                logger.info(f"Extracted name via LLM: {name}")
                return name
            
        except Exception as e:
            logger.error(f"Failed to extract name via LLM: {e}")
        
        return None
    
    def _extract_name_via_regex(self, message: str) -> Optional[str]:
        """Extract name using regex patterns - fast and reliable."""
        
        # First, skip messages that are clearly NOT name introductions
        skip_patterns = [
            r"interested\s+in",  # "I'm interested in..."
            r"looking\s+for",    # "I'm looking for..."
            r"trying\s+to",      # "I'm trying to..."
            r"here\s+to",        # "I'm here to..."
            r"calling\s+about",  # "I'm calling about..."
            r"asking\s+about",   # "I'm asking about..."
            r"wondering",        # "I'm wondering..."
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
            'here', 'there', 'interested', 'looking', 'good', 'fine', 'great', 'okay', 'ok',
            'hello', 'hi', 'hey', 'yes', 'no', 'sure', 'thanks', 'thank', 'please',
            'just', 'also', 'actually', 'really', 'very', 'quite', 'well',
            'calling', 'asking', 'wondering', 'trying', 'hoping', 'waiting',
            'new', 'old', 'happy', 'sad', 'busy', 'free', 'available','my','number',
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
        """Extract name when user just sends their name as a standalone message.
        
        This handles cases like:
        - "Sandhya"
        - "John Smith"
        - "my name is sarah" (case insensitive)
        """
        message = message.strip()
        
        # If message is very short (1-3 words), it might be a name
        words = message.split()
        
        if len(words) > 4:
            # Too long to be just a name
            return None
        
        # Expanded list of non-name words
        non_names = [
            'hi', 'hello', 'hey', 'yes', 'no', 'sure', 'ok', 'okay', 'thanks', 'thank',
            'please', 'help', 'good', 'great', 'fine', 'well', 'interested', 'looking',
            'what', 'why', 'how', 'when', 'where', 'who', 'which',
        ]
        
        # Check for "my name is X" pattern (case insensitive)
        match = re.match(r'^(?:my\s+)?name\s+is\s+(.+)$', message, re.IGNORECASE)
        if match:
            name = match.group(1).strip().title()
            if name.lower() not in non_names and 2 <= len(name) <= 50:
                return name
        
        # For very short messages (1-2 words), treat as potential name
        if len(words) <= 2:
            # Check first word looks like a name (capitalized or all lowercase)
            first_word = words[0]
            
            # Remove punctuation
            first_word_clean = re.sub(r'[^a-zA-Z]', '', first_word)
            
            if len(first_word_clean) < 2:
                return None
            
            # Skip common non-name words
            if first_word_clean.lower() in non_names:
                return None
            
            # Build name from words
            potential_name = ' '.join(w.strip() for w in words if re.match(r'^[a-zA-Z]+$', w))
            
            if potential_name and 2 <= len(potential_name) <= 50:
                return potential_name.title()
        
        return None
    
    def _extract_answer_summary(
        self,
        message: str,
        question: QualificationQuestion
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
    
    # ==================== INTENT DETECTION ====================
    
    def _is_user_asking_question(self, message: str) -> bool:
        """Detect if user is asking a question vs answering.
        
        Enhanced to detect:
        1. Explicit question marks
        2. Question phrases (what is, how much, etc.)
        3. Deliberation phrases (to decide, whether or not, etc.)
        4. Information request phrases (need more details, etc.)
        """
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
        if len(message) >= 50 and (',' in message or ' and ' in message_lower or ' but ' in message_lower or ' or ' in message_lower):
            # Check if it starts with 'i need' or 'i want' - these could be info requests
            if message_lower.startswith(('i need ', 'i want ', 'i would like ')):
                logger.info(f"Detected info request in longer message")
                return True
        
        return False
    
    def _is_user_answering(self, message: str, current_question: QualificationQuestion) -> bool:
        """Detect if user is answering the qualification question."""
        message_lower = message.lower().strip()
        
        # Short messages without question marks are likely answers
        if len(message) < 50 and '?' not in message:
            # Check it's not just a greeting
            greetings = ['hi', 'hello', 'hey', 'good morning', 'good afternoon', 'good evening']
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
        if len(message) < 100 and '?' not in message:
            return True
        
        return False
    
    # ==================== HELPER METHODS ====================
    
    def _get_quick_replies(self, question: QualificationQuestion) -> List[str]:
        """Get quick reply options for a question."""
        # Check for direct options first
        if question.quick_replies:
            return [qr.text for qr in question.quick_replies]
        
        # Fallback to options array if available
        # (from frontend config format)
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
            ]
        ]
        

        options = rephrase_options[min(attempt - 1, len(rephrase_options) - 1)]
        return random.choice(options)
    
    def _build_summary(
            self,
            extracted_data: Dict[str, str],
            questions: List[QualificationQuestion],
    ) -> Dict[str, Any]:

        summary: Dict[str, Any] = {
            "name": extracted_data.get("name", "Not provided"),
            "phone": extracted_data.get("phone", "Not provided"),
        }

        q_num = 1

        for q in questions:
            if self._is_contact_question(q):
                continue

            field_key = f"q{q_num}"
            answer = extracted_data.get(q.id, "Not provided")

            summary[field_key] = {
                "label": q.label,
                "answer": answer,
                "order": q_num,
            }

            q_num += 1

        return summary


# Singleton instance
state_machine = ChatStateMachine()
