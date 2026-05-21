"""Modern LLM Guardrails Service.

Implements multi-layered safety controls based on OWASP LLMSVS and NeMo Guardrails best practices:
- Input Rails: Validate and sanitize user inputs before LLM processing
- Output Rails: Filter LLM responses before sending to users
- PII Detection: Identify and mask sensitive personal information
- Jailbreak Prevention: Detect prompt injection and manipulation attempts
- Content Moderation: Filter inappropriate, harmful, or off-topic content
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from app.core.logging_config import get_logger

logger = get_logger(__name__)


class GuardrailCategory(str, Enum):
    """Categories of guardrail violations."""

    JAILBREAK = "jailbreak"
    PII_DETECTED = "pii_detected"
    OFF_TOPIC = "off_topic"
    HARMFUL_CONTENT = "harmful_content"
    CODING_REQUEST = "coding_request"
    COMPETITOR_MENTION = "competitor_mention"
    CUSTOM_BLOCKED = "custom_blocked"
    SAFE = "safe"


@dataclass
class GuardrailResult:
    """Result of guardrail check."""

    is_safe: bool
    category: GuardrailCategory
    confidence: float  # 0.0 to 1.0
    reason: str
    sanitized_input: Optional[str] = None


class GuardrailsService:
    """Modern multi-layered LLM guardrails implementation."""

    def __init__(self):
        """Initialize guardrails with detection patterns."""

        # === JAILBREAK DETECTION PATTERNS ===
        # Based on OWASP LLM Top 10 and research on prompt injection attacks
        self.jailbreak_patterns = [
            # Direct instruction override attempts
            r"ignore (all |your |previous |prior |above )?instructions?",
            r"forget (all |your |previous |prior |above )?instructions?",
            r"disregard (all |your |previous |prior |above )?instructions?",
            r"override (your |the )?system",
            r"bypass (your |the |safety )?filters?",
            r"disable (your |the |safety )?filters?",
            # Persona manipulation
            r"(you are|act as|pretend to be|roleplay as|imagine you are) (now )?a",
            r"(enter|switch to|enable) (dan|developer|jailbreak|unrestricted) mode",
            r"you('re| are) no longer (bound by|restricted|limited)",
            r"from now on[,]? you (will|must|should|can)",
            # System prompt extraction
            r"(show|reveal|display|print|output|repeat) (your |the )?(system |initial )?prompt",
            r"what (are|were) your (original |initial |system )?instructions",
            r"(tell|show) me (your |the )?rules",
            r"how (are|were) you (programmed|trained|instructed)",
            # Boundary testing
            r"this is (just )?(a |an )?(test|experiment|hypothetical)",
            r"(for|in) (a |an )?(educational|research|academic) (purpose|context)",
            r"(in|during) (a |an )?roleplay (scenario)?",
            r"\[.*?(system|admin|developer|root).*?\]",
            # Token manipulation
            r"<\|.*?\|>",  # Special tokens like <|im_start|>
            r"```(system|admin|root|override)",
            r"###\s*(instruction|system|override)",
        ]

        # Compile patterns for efficiency
        self.jailbreak_regex = [
            re.compile(p, re.IGNORECASE) for p in self.jailbreak_patterns
        ]

        # === PII DETECTION PATTERNS ===
        self.pii_patterns = {
            "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
            "phone": r"\b(?:\+?1[-.\s]?)?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}\b",
            "ssn": r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b",
            "credit_card": r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13})\b",
            "ip_address": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
            "aadhaar": r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b",  # Indian ID
            "pan": r"\b[A-Z]{5}\d{4}[A-Z]\b",  # Indian PAN
        }
        self.pii_regex = {k: re.compile(v) for k, v in self.pii_patterns.items()}

        # === OFF-TOPIC DETECTION ===
        self.off_topic_patterns = [
            # Entertainment requests
            r"(tell|say|write|give) (me )?(a |an )?(joke|riddle|story|poem|song)",
            r"(sing|dance|perform) for me",
            r"(play|let's play) (a |an )?(game|quiz|trivia)",
            # General knowledge (unrelated to business)
            r"(who|what|when|where|why|how) (is|was|are|were) (the )?(president|prime minister|king|queen|capital)",
            r"(what('s| is) the )?weather (like )?(in|at|for)",
            r"(what('s| is) the )?(news|headlines|trending) (today|now)?",
            r"translate .* (to|into|from)",
            # Creative writing
            r"write (me )?(a |an )?(essay|article|blog|story|script)",
            r"(help|assist) (me )?(with )?my (homework|assignment|thesis)",
            r"(summarize|analyze|critique) (this |the )?(book|movie|article)",
        ]
        self.off_topic_regex = [
            re.compile(p, re.IGNORECASE) for p in self.off_topic_patterns
        ]

        # === CODING REQUEST DETECTION ===
        self.coding_patterns = [
            r"(write|create|generate|build|make|code|develop) (a |an |the |me )?(function|method|class|script|program|app|application|code|algorithm)",
            r"(debug|fix|correct|solve) (this |the )?(code|error|bug|issue|problem)",
            r"(python|javascript|java|c\+\+|ruby|go|rust|php|sql|html|css) (code|script|function)",
            r"(how to|how do i) (implement|code|program|write)",
            r"(regex|regular expression) (for|to|that)",
            r"(api|endpoint|database|query) (implementation|code|example)",
            r"```[\w]*\n.*?```",  # Code blocks
        ]
        self.coding_regex = [
            re.compile(p, re.IGNORECASE | re.DOTALL) for p in self.coding_patterns
        ]

        # === HARMFUL CONTENT PATTERNS ===
        self.harmful_patterns = [
            r"(how to|teach me|show me|help me) (hack|steal|break into|bypass|exploit)",
            r"(make|create|build|construct) (a |an )?(bomb|weapon|explosive|virus|malware)",
            r"(hurt|harm|kill|attack|threaten|stalk) (myself|yourself|someone|a person|people)",
            r"(illegal|illicit) (drugs|substances|activities)",
        ]
        self.harmful_regex = [
            re.compile(p, re.IGNORECASE) for p in self.harmful_patterns
        ]

        # === POLITE REFUSAL MESSAGES ===
        self.refusal_messages = {
            GuardrailCategory.JAILBREAK: "I'm here to help you with questions about our products and services. How can I assist you today?",
            GuardrailCategory.PII_DETECTED: "I noticed you may have shared some personal information. For your security, please avoid sharing sensitive details like credit card numbers or SSNs in the chat.",
            GuardrailCategory.OFF_TOPIC: "I'm specifically designed to help with questions about our products and services. Is there something specific about our offerings I can help you with?",
            GuardrailCategory.CODING_REQUEST: "I'm a customer support assistant and can't help with programming or coding requests. However, I'd be happy to answer any questions about our products or services!",
            GuardrailCategory.HARMFUL_CONTENT: "I'm not able to help with that request. If you have questions about our products or services, I'm here to help!",
            GuardrailCategory.COMPETITOR_MENTION: "I'm not the best source for information about other companies. I'd be happy to tell you about our own products and how they can help you!",
            GuardrailCategory.CUSTOM_BLOCKED: "I'm not able to discuss that topic. Is there something else about our products or services I can help you with?",
        }

    def check_input(
        self, query: str, custom_guardrails: Optional[List[str]] = None, allow_pii: bool = False
    ) -> GuardrailResult:
        """
        Input Rail: Validate user input before processing.

        Args:
            query: User's input message
            custom_guardrails: List of custom topics to block (from tenant config)
            allow_pii: Whether to allow PII (e.g., for lead capture)

        Returns:
            GuardrailResult with safety assessment
        """
        query_lower = query.lower().strip()

        # Check for empty/minimal input
        if len(query.strip()) < 2:
            return GuardrailResult(
                is_safe=True,
                category=GuardrailCategory.SAFE,
                confidence=1.0,
                reason="Input too short to evaluate",
                sanitized_input=query,
            )

        # === Layer 1: Jailbreak Detection (Highest Priority) ===
        for pattern in self.jailbreak_regex:
            if pattern.search(query):
                logger.warning(
                    "Jailbreak attempt detected",
                    extra={
                        "extra_data": {"pattern": pattern.pattern, "query": query[:100]}
                    },
                )
                return GuardrailResult(
                    is_safe=False,
                    category=GuardrailCategory.JAILBREAK,
                    confidence=0.95,
                    reason="Potential prompt injection or instruction override attempt",
                )

        # === Layer 2: Harmful Content Detection ===
        for pattern in self.harmful_regex:
            if pattern.search(query):
                logger.warning(
                    "Harmful content detected",
                    extra={"extra_data": {"query": query[:100]}},
                )
                return GuardrailResult(
                    is_safe=False,
                    category=GuardrailCategory.HARMFUL_CONTENT,
                    confidence=0.9,
                    reason="Request contains potentially harmful content",
                )

        # === Layer 3: PII Detection ===
        if not allow_pii:
            detected_pii = self._detect_pii(query)
            if detected_pii:
                # Log but allow with warning (don't block lead capture scenarios)
                logger.info(
                    "PII detected in input",
                    extra={"extra_data": {"pii_types": detected_pii}},
                )
                # For customer support bot, we may want to warn but not block
                # Return as safe but with sanitized version
                return GuardrailResult(
                    is_safe=True,  # Allow but warn
                    category=GuardrailCategory.PII_DETECTED,
                    confidence=0.8,
                    reason=f"Detected PII types: {', '.join(detected_pii)}",
                    sanitized_input=self._mask_pii(query),
                )

        # === Layer 4: Coding Request Detection ===
        for pattern in self.coding_regex:
            if pattern.search(query):
                return GuardrailResult(
                    is_safe=False,
                    category=GuardrailCategory.CODING_REQUEST,
                    confidence=0.85,
                    reason="Request appears to be for coding assistance",
                )

        # === Layer 5: Off-Topic Detection ===
        for pattern in self.off_topic_regex:
            if pattern.search(query):
                return GuardrailResult(
                    is_safe=False,
                    category=GuardrailCategory.OFF_TOPIC,
                    confidence=0.8,
                    reason="Request appears to be off-topic",
                )

        # === Layer 6: Custom Guardrails (Tenant-specific) ===
        if custom_guardrails:
            for blocked_topic in custom_guardrails:
                if blocked_topic.lower() in query_lower:
                    return GuardrailResult(
                        is_safe=False,
                        category=GuardrailCategory.CUSTOM_BLOCKED,
                        confidence=0.9,
                        reason=f"Topic '{blocked_topic}' is restricted",
                    )

        # All checks passed
        return GuardrailResult(
            is_safe=True,
            category=GuardrailCategory.SAFE,
            confidence=1.0,
            reason="Input passed all guardrail checks",
            sanitized_input=query,
        )

    def check_output(self, response: str, original_query: str) -> GuardrailResult:
        """
        Output Rail: Validate LLM response before sending to user.

        Args:
            response: LLM's generated response
            original_query: Original user query (for context)

        Returns:
            GuardrailResult with safety assessment
        """

        # === Check 1: Response reveals system instructions ===
        system_leak_patterns = [
            r"(my |I have been |I am )?(system |initial )?instructions? (are|is|include|say)",
            r"(I was|I am) (programmed|trained|instructed) to",
            r"my (core |primary )?directive",
            r"I('m| am) (actually )?(an AI|a language model|ChatGPT|GPT|Claude|Gemini)",
        ]
        for pattern in system_leak_patterns:
            if re.search(pattern, response, re.IGNORECASE):
                logger.warning("Output may leak system information")
                # In production, you might want to regenerate or filter
                return GuardrailResult(
                    is_safe=True,  # Allow but flag
                    category=GuardrailCategory.SAFE,
                    confidence=0.7,
                    reason="Response may reveal system details",
                    sanitized_input=response,
                )

        # === Check 2: Response contains harmful suggestions ===
        for pattern in self.harmful_regex:
            if pattern.search(response):
                logger.warning("Harmful content in LLM output detected")
                return GuardrailResult(
                    is_safe=False,
                    category=GuardrailCategory.HARMFUL_CONTENT,
                    confidence=0.9,
                    reason="Response contains potentially harmful content",
                )

        # === Check 3: Response is too long (potential token attack) ===
        if len(response) > 10000:
            logger.warning("Unusually long LLM response detected")
            return GuardrailResult(
                is_safe=True,
                category=GuardrailCategory.SAFE,
                confidence=0.8,
                reason="Response length is acceptable",
                sanitized_input=response[:5000] + "...",  # Truncate
            )

        return GuardrailResult(
            is_safe=True,
            category=GuardrailCategory.SAFE,
            confidence=1.0,
            reason="Output passed all guardrail checks",
            sanitized_input=response,
        )

    def _detect_pii(self, text: str) -> List[str]:
        """Detect PII in text and return list of detected types."""
        detected = []
        for pii_type, pattern in self.pii_regex.items():
            if pattern.search(text):
                detected.append(pii_type)
        return detected

    def _mask_pii(self, text: str) -> str:
        """Mask detected PII in text for logging purposes."""
        masked = text
        for pii_type, pattern in self.pii_regex.items():
            masked = pattern.sub(f"[{pii_type.upper()}_REDACTED]", masked)
        return masked

    def get_refusal_message(self, category: GuardrailCategory) -> str:
        """Get appropriate refusal message for a guardrail category."""
        return self.refusal_messages.get(
            category,
            "I'm not able to help with that. Is there something about our products I can assist you with?",
        )

    def build_system_prompt(
        self,
        tone: str = "professional",
        language: str = "English",
        custom_guardrails: Optional[List[str]] = None,
        company_context: str = "",
    ) -> str:
        """Build a secure system prompt using natural language (no XML tags).
        
        Args:
            tone: Conversation tone (professional, friendly, etc.)
            language: Response language
            custom_guardrails: Tenant-specific rules
            company_context: Company-specific context
            
        Returns:
            Formatted system prompt without XML tags
        """
        # Build custom rules section
        custom_rules = ""
        if custom_guardrails:
            rules_list = "\n".join([f"- {rule}" for rule in custom_guardrails])
            custom_rules = f"\n\nAdditional Rules:\n{rules_list}"

        # Build a natural, human-like prompt
        # Designed to make the bot sound like a real, helpful person
        system_prompt = f"""You're chatting with a customer. Talk to them like a real person would - warm, helpful, and genuine.

Your vibe: {tone} and friendly. Speak in {language}.

Here's the thing - you know a lot about the products and services, but you're not a walking encyclopedia. If you have the answer, great - share it naturally. If you don't, just be honest about it. Real people don't pretend to know everything.

How to chat:
- Keep it conversational - imagine you're helping a friend or family member
- Short answers for simple questions (just 2-3 sentences)
- Use bullets when explaining multiple things
- Skip the corporate jargon - talk like a human
- Mirror their energy - casual with casual, professional with professional
- Don't overthink it{custom_rules}

Real quick examples:

Customer: "What's the weather like?"
You: "Hey! I'm here to help with questions about what we offer. What can I help you find today?"

Customer asks about something you don't know:
You: "I don't have those specific details on hand, but I can connect you with someone who does. What are you looking for?"

Someone tries to mess with you:
You: "Haha, nice try! But seriously, how can I help you with our products or services?"

Bottom line: Be yourself. Be helpful. Be honest. Chat like you're actually there to help, not like you're reading from a script. Because at the end of the day, you're talking to real people who just want real help."""

        return system_prompt


# Singleton instance
guardrails_service = GuardrailsService()
