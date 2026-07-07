"""Enterprise strategy, intent classification, lead qualification, and guardrails engines."""

import json
import re
from typing import Any, Dict, List, Optional, Tuple
from app.models.outbound.outbound_enterprise_entities import (
    CustomerIntentType,
    SalesGoal,
    ObjectionType,
    CustomerPersona,
    LeadQualification,
    ConversationHealth,
    MultiStepPlan,
    AIReasoningMetadata,
    SalesMemory,
)
from app.services.ai.outbound.conversation_pipeline import OutboundAISession
from app.services.ai.outbound_llm_service import outbound_llm_service
from app.core.logging_config import get_logger

logger = get_logger(__name__)


# ── 1. INTENT & OBJECTION CLASSIFIER ──────────────────────────────────

class IntentClassifier:
    """Uses GLM-5 converse to classify message intent and objections in JSON."""

    async def classify(self, session: OutboundAISession) -> Tuple[CustomerIntentType, float, Optional[ObjectionType]]:
        """Classify customer message text into SaaS intents and objections."""
        try:
            # Fallback for simple greeting / help triggers to save API calls
            text = session.incoming_text.lower().strip()
            if text in ["hi", "hello", "hey", "good morning", "good afternoon"]:
                return CustomerIntentType.CURIOUS, 0.95, None

            system_prompt = (
                "You are an AI sales classifier. Analyze the customer's incoming message and return ONLY a JSON object containing:\n"
                "- intent: Must be one of: BUYING, CURIOUS, PRICING, SUPPORT_QUESTION, COMPARISON, SCHEDULING, NEGOTIATION, COLD, WARM, HOT, LOST, RETURNING\n"
                "- confidence: float value between 0.0 and 1.0\n"
                "- objection: If an objection is detected, categorize it as one of: PRICE, TRUST, COMPETITOR, TIMING, NEED, AUTHORITY, FEATURE, IMPLEMENTATION, ROI. Otherwise return null.\n"
                "\nReturn ONLY JSON, no conversational text."
            )

            # Send current message context
            prompt = f"Customer message: '{session.incoming_text}'"
            messages = [{"role": "user", "content": [{"text": prompt}]}]

            output, _, _, _ = outbound_llm_service.converse(
                messages=messages,
                system_prompt=system_prompt,
                temperature=0.0,  # Strict classification
                max_tokens=150
            )

            response_text = output.get("content", [{}])[0].get("text", "{}").strip()
            # Clean possible markdown wrap
            response_text = re.sub(r"```json\s*", "", response_text)
            response_text = re.sub(r"\s*```", "", response_text)
            
            data = json.loads(response_text)
            
            intent_val = data.get("intent", "CURIOUS")
            confidence = float(data.get("confidence", 0.8))
            objection_val = data.get("objection")

            # Match Enums safely
            try:
                intent = CustomerIntentType(intent_val)
            except ValueError:
                intent = CustomerIntentType.CURIOUS

            objection = None
            if objection_val:
                try:
                    objection = ObjectionType(objection_val)
                except ValueError:
                    objection = None

            logger.info(f"[IntentClassifier] Detected Intent: {intent} (Confidence: {confidence:.2f}) | Objection: {objection}")
            return intent, confidence, objection

        except Exception as e:
            logger.warning(f"[IntentClassifier] Classification failed: {e}. Using fallback.")
            return CustomerIntentType.CURIOUS, 0.5, None


# ── 2. LEAD QUALIFICATION ENGINE ──────────────────────────────────────

class LeadQualificationEngine:
    """Computes qualification markers and BANT criteria metrics."""

    def evaluate(self, session: OutboundAISession, sales_mem: SalesMemory) -> None:
        """Parse chat history turns to calculate lead scoring."""
        qual = sales_mem.qualification
        persona = sales_mem.persona
        history_text = "\n".join(f"{m.role}: {m.content}" for m in session.history[-15:]).lower()
        combined_text = (history_text + "\n" + session.incoming_text).lower()

        # 1. Budget check
        if (session.memory and session.memory.budget) or "budget" in combined_text or "price" in combined_text or "$" in combined_text:
            qual.budget_confirmed = True
            
        # 2. Decision Maker (Authority) check
        if any(keyword in combined_text for keyword in ["i am the founder", "ceo", "director", "owner", "decide", "i handle", "decision"]):
            qual.decision_maker_probability = 0.90
        elif any(keyword in combined_text for keyword in ["ask my boss", "manager decides", "need approval", "discuss with team"]):
            qual.decision_maker_probability = 0.20
        else:
            qual.decision_maker_probability = 0.50

        # 3. Timeline check
        if any(keyword in combined_text for keyword in ["urgent", "this week", "next month", "timeline", "asap", "starting soon"]):
            qual.timeline_confirmed = True

        # 4. Calculate overall lead score (BANT heuristics)
        score = 0
        if qual.budget_confirmed:
            score += 25
        if qual.decision_maker_probability >= 0.5:
            score += 25
        if qual.timeline_confirmed:
            score += 25
        if len(persona.pain_points) > 0:
            score += 25  # Need identified

        qual.lead_score = score

        # Update qualification status
        if score >= 75:
            qual.qualification_status = "QUALIFIED"
        elif score >= 50:
            qual.qualification_status = "PARTIALLY_QUALIFIED"
        else:
            qual.qualification_status = "UNQUALIFIED"

        logger.info(f"[LeadQual] Lead score calculated: {score} ({qual.qualification_status})")


# ── 3. CONVERSATION HEALTH CALCULATOR ─────────────────────────────────

class ConversationHealthCalculator:
    """Calculates risk levels, booking momentum, and conversion trends."""

    def calculate(self, session: OutboundAISession, sales_mem: SalesMemory) -> None:
        """Calculate conversation metrics."""
        health = sales_mem.health
        text_lower = session.incoming_text.lower()

        # Risk Heuristics
        risk = 0
        if any(w in text_lower for w in ["stop", "uninterested", "no thanks", "remove", "dont write"]):
            risk += 80
        if len(session.history) > 30:
            risk += 30  # High turn count (loop risk)
        
        # Momentum check based on count of AI response times
        conv = session.conversation
        if conv and conv.response_count > 0:
            avg_time = conv.response_time_sum / conv.response_count
            if avg_time > 60:
                health.momentum_score = max(20, health.momentum_score - 10)
            else:
                health.momentum_score = min(95, health.momentum_score + 5)

        health.risk_score = min(100, risk)
        
        # Interest Level
        interest_words = {"pricing", "cost", "demo", "interested", "buy"}
        words = set(re.findall(r"\b\w+\b", text_lower))
        if interest_words.intersection(words) or "how does" in text_lower:
            health.customer_interest_level = "HIGH"
        elif any(w in text_lower for w in ["maybe", "later", "busy", "stop", "uninterested", "remove", "dont write"]):
            health.customer_interest_level = "LOW"
        else:
            health.customer_interest_level = "MEDIUM"

        # Conversion Probability
        prob = 0.10
        if sales_mem.active_goal == SalesGoal.BOOK_MEETING:
            prob += 0.20
        if sales_mem.qualification.qualification_status == "QUALIFIED":
            prob += 0.30
        if health.customer_interest_level == "HIGH":
            prob += 0.30
        if health.risk_score > 50:
            prob -= 0.40

        health.conversion_probability = max(0.0, min(0.95, prob))


# ── 4. SALES STRATEGY ENGINE ──────────────────────────────────────────

class SalesStrategyEngine:
    """Selects best next actions and Objection counters."""

    def select_strategy(
        self,
        session: OutboundAISession,
        sales_mem: SalesMemory,
        intent: CustomerIntentType,
        objection: Optional[ObjectionType]
    ) -> Tuple[SalesGoal, str, str]:
        """Decide the active sales goal, next best action, and conversational counter-strategy."""
        current_goal = sales_mem.active_goal
        rationale = ""
        strategy = "Consultative Nurturing"

        # 1. Update goals based on BANT completion
        qual = sales_mem.qualification
        if session.memory and session.memory.meeting_status == "BOOKED":
            current_goal = SalesGoal.CLOSE_SALE
            rationale = "Meeting is booked. Strategy is to prepare client and close details."
        elif qual.lead_score >= 75:
            current_goal = SalesGoal.BOOK_MEETING
            rationale = "Lead is qualified. Strategy is to book discovery session."
        elif not qual.budget_confirmed:
            current_goal = SalesGoal.COLLECT_BUDGET
            rationale = "Budget is missing. Aim to extract budget bounds."
        elif len(sales_mem.persona.pain_points) == 0:
            current_goal = SalesGoal.COLLECT_REQUIREMENTS
            rationale = "Customer requirements/pain points are unknown. Strategy is discovery."
        else:
            current_goal = SalesGoal.PRODUCT_RECOMMENDATION
            rationale = "Requirements known, budget confirmed. Target product selection."

        # 2. Objection Handling Strategies
        if objection:
            strategy = f"Objection Mitigation ({objection.value})"
            if objection == ObjectionType.PRICE:
                next_action = "Explain ROI and offer flexible payment details or brochure specifications."
            elif objection == ObjectionType.COMPETITOR:
                next_action = "Highlight core feature differentiation benefits."
            elif objection == ObjectionType.TIMING:
                next_action = "Create gentle urgency by stating ongoing campaign benefits."
            else:
                next_action = "Build trust by offering product spec details."
        else:
            # 3. Next Best Actions matching Goal states
            if current_goal == SalesGoal.COLLECT_BUDGET:
                next_action = "Politely request budget limits or pricing expectations."
            elif current_goal == SalesGoal.COLLECT_REQUIREMENTS:
                next_action = "Inquire about key pain points in their current sales workflow."
            elif current_goal == SalesGoal.PRODUCT_RECOMMENDATION:
                next_action = "Suggest Product details based on identified category fits."
            elif current_goal == SalesGoal.BOOK_MEETING:
                next_action = "Present calendar booking options and share availability."
            else:
                next_action = "Answer customer queries and guide to closure."

        sales_mem.active_goal = current_goal
        return current_goal, next_action, strategy


# ── 5. MULTI-STEP PLANNER ─────────────────────────────────────────────

class MultiStepPlanner:
    """Predicts execution stages for the LLM turn."""

    def formulate_plan(self, session: OutboundAISession, sales_mem: SalesMemory) -> MultiStepPlan:
        """Formulate a step plan checklists array."""
        goal = sales_mem.active_goal
        steps = []

        if goal == SalesGoal.COLLECT_BUDGET:
            steps = ["CHECK_CATALOG", "ASK_BUDGET"]
            obj = "Discover pricing thresholds"
        elif goal == SalesGoal.COLLECT_REQUIREMENTS:
            steps = ["SEARCH_KNOWLEDGE", "ASK_REQUIREMENTS"]
            obj = "Identify workflow challenges"
        elif goal == SalesGoal.PRODUCT_RECOMMENDATION:
            steps = ["SEARCH_PRODUCTS", "RECOMMEND_FIT"]
            obj = "Demonstrate features value"
        elif goal == SalesGoal.BOOK_MEETING:
            steps = ["CHECK_CALENDAR", "OFFER_SLOTS"]
            obj = "Schedule discovery call"
        else:
            steps = ["REPLY"]
            obj = "Maintain relationship"

        return MultiStepPlan(
            current_objective=obj,
            steps=steps,
            next_best_action=steps[-1] if steps else "REPLY"
        )


# ── 6. AI GUARDRAILS ──────────────────────────────────────────────────

class AIGuardrails:
    """Safety guardrails checking tool call repetitions, duplicate brochures, and loops."""

    def validate_and_filter_tools(self, session: OutboundAISession, tool_specs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filter out tools that break safety guidelines (e.g. repeated actions)."""
        filtered = []
        
        # Track past tool outcomes
        past_calls = [out.get("name") for out in session.tool_outputs]
        
        # Rule 1: Prevent duplicate brochure dispatch
        has_sent_brochure = "send_brochure" in past_calls
        
        # Rule 2: Prevent duplicate calendar bookings if already scheduled
        is_already_booked = session.memory.meeting_status == "BOOKED"

        for spec in tool_specs:
            t_name = spec["toolSpec"]["name"]
            
            if t_name == "send_brochure" and has_sent_brochure:
                logger.warning("[Guardrails] Blocking repeated brochure delivery.")
                continue
                
            if t_name == "book_meeting" and is_already_booked:
                logger.warning("[Guardrails] Blocking duplicate meeting booking.")
                continue
                
            filtered.append(spec)

        return filtered

    def clean_hallucinated_offers(self, session: OutboundAISession, text: str) -> str:
        """Strip promises/offers of discounts or tools not supported in active catalog."""
        # Simple safeguard for illegal discount terms
        text_lower = text.lower()
        if "discount" in text_lower or "off" in text_lower:
            # Confirm if discount was approved (by default, outbound agents don't offer unauthorized discounts)
            logger.warning("[Guardrails] Sanitizing potential hallucinated discount promise.")
            # Remove direct discount promises
            text = re.sub(r"(?i)\b\d+%\s*(off|discount)?\b", "", text)
            text = re.sub(r"(?i)\bdiscount\b", "", text)
            
        return text


# Singletons
intent_classifier = IntentClassifier()
lead_qualification_engine = LeadQualificationEngine()
health_calculator = ConversationHealthCalculator()
sales_strategy_engine = SalesStrategyEngine()
multi_step_planner = MultiStepPlanner()
ai_guardrails = AIGuardrails()
