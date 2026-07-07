"""Pipeline steps for Outbound Sales Agent enterprise SaaS capabilities."""

import time
import re
from datetime import datetime, timezone
import uuid
from typing import Optional

from app.services.ai.outbound.conversation_pipeline import PipelineStep, OutboundAISession
from app.models.outbound.outbound_enterprise_entities import SalesMemory, AIReasoningMetadata
from app.services.ai.outbound.enterprise_engine import (
    intent_classifier,
    lead_qualification_engine,
    health_calculator,
    sales_strategy_engine,
    multi_step_planner,
    ai_guardrails,
)
from app.services.outbound.outbound_db_service import outbound_db_service
from app.core.logging_config import get_logger

logger = get_logger(__name__)


# ── UTILITY SERIALIZERS ────────────────────────────────────────────────

def get_sales_memory(session: OutboundAISession) -> SalesMemory:
    """Retrieve SalesMemory tree from the existing memory entity preferences."""
    if not session.memory:
        return SalesMemory()
    
    ent_data = session.memory.preferences.get("enterprise")
    if not ent_data:
        return SalesMemory()
        
    try:
        return SalesMemory(**ent_data)
    except Exception as e:
        logger.warning(f"[SalesMemorySerializer] Deserialization failed: {e}. Returning new state.")
        return SalesMemory()


def save_sales_memory(session: OutboundAISession, sales_mem: SalesMemory) -> None:
    """Serialize and save SalesMemory tree back into memory preferences."""
    if session.memory:
        session.memory.preferences["enterprise"] = sales_mem.model_dump(mode="json")


# ── 1. INTENT & STRATEGY ANALYSIS ─────────────────────────────────────

class AnalyzeIntentAndStrategyStep(PipelineStep):
    """Classifies user intent, objection types, health, and sets objectives."""

    async def execute(self, session: OutboundAISession) -> None:
        sales_mem = get_sales_memory(session)

        # 1. Classify intent and objections using classifier service
        intent, confidence, objection = await intent_classifier.classify(session)
        
        if objection:
            if objection not in sales_mem.detected_objections:
                sales_mem.detected_objections.append(objection)

        # 2. Re-calculate health metrics
        health_calculator.calculate(session, sales_mem)

        # 3. Select active sales goal and strategy
        active_goal, next_action, strategy = sales_strategy_engine.select_strategy(
            session, sales_mem, intent, objection
        )

        # 4. Save updates to session context for PromptBuilder
        session.stage = sales_mem.active_goal  # Map goal status to runtime stage state if desired
        
        # Save reasoning metadata logs
        reasoning_id = str(uuid.uuid4())
        reasoning_meta = AIReasoningMetadata(
            reasoning_id=reasoning_id,
            conversation_id=session.conversation_id,
            message_id=session.message_id,
            selected_strategy=strategy,
            strategy_rationale=f"Intent: {intent} (Confidence: {confidence:.2f}) | Objection: {objection}",
            active_goal=active_goal,
            goal_change_rationale=f"Advancing objective goal to: {active_goal}"
        )
        
        # Keep reasoning summary in sales memory
        sales_mem.last_reasoning = reasoning_meta.model_dump_json()

        # Update custom analytical telemetry
        session.metrics["intent_confidence"] = confidence
        session.metrics["risk_score"] = float(sales_mem.health.risk_score)
        session.metrics["momentum_score"] = float(sales_mem.health.momentum_score)

        save_sales_memory(session, sales_mem)
        logger.info(f"[AnalyzeStep] Updated Goal: {active_goal} | Next Action: '{next_action}'")


# ── 2. LEAD QUALIFICATION STEP ────────────────────────────────────────

class LeadQualificationStep(PipelineStep):
    """Extracts customer demographics/BANT details and scores the lead."""

    async def execute(self, session: OutboundAISession) -> None:
        sales_mem = get_sales_memory(session)

        # 1. Parse details from customer text to build CustomerPersona
        text_lower = session.incoming_text.lower()
        
        # Look for role keywords
        roles = ["manager", "owner", "founder", "engineer", "director", "head of", "assistant"]
        for r in roles:
            if r in text_lower:
                sales_mem.persona.role = r.capitalize()
                break

        # Look for budget expressions
        budget_match = re.search(r"\$?\d+[\d,]*", session.incoming_text)
        if budget_match:
            try:
                # Remove dollar symbol and parse float
                cleaned_num = re.sub(r"[^\d.]", "", budget_match.group(0))
                sales_mem.persona.budget_limit = float(cleaned_num)
            except Exception:
                pass

        # 2. Run qualification evaluation
        lead_qualification_engine.evaluate(session, sales_mem)

        # Save updates back
        save_sales_memory(session, sales_mem)


# ── 3. MULTI-STEP PLANNING ────────────────────────────────────────────

class MultiStepPlanningStep(PipelineStep):
    """Predicts a checklist plan to achieve the active sales goal."""

    async def execute(self, session: OutboundAISession) -> None:
        sales_mem = get_sales_memory(session)

        # Formulate internal checklists
        plan = multi_step_planner.formulate_plan(session, sales_mem)
        
        # Save plan specs in metrics for diagnostic reviews
        session.metrics["objective"] = plan.current_objective
        session.metrics["next_action"] = plan.next_best_action
        
        # Save in memory
        save_sales_memory(session, sales_mem)


# ── 4. SAFETY GUARDRAILS STEP ─────────────────────────────────────────

class GuardrailsValidationStep(PipelineStep):
    """Enforces safety loops, limits repeated tool calls, and cleans hallucinations."""

    async def execute(self, session: OutboundAISession) -> None:
        if not session.final_reply:
            return

        # 1. Clean potential hallucinated promise strings
        cleaned = ai_guardrails.clean_hallucinated_offers(session, session.final_reply)
        session.final_reply = cleaned
