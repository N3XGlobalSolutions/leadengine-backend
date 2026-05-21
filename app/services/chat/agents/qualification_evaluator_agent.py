"""Qualification Evaluator Agent — Makes the final QUALIFIED/DISQUALIFIED decision.

Evaluates the full qualification scorecard against the tenant's LQC criteria
and produces a rich lead output for the sales team.
"""

import json
from typing import Any, Dict

from app.core.logging_config import get_logger
from app.services.chat.agents.graph_state import S3MaxState
from app.utils.localization import get_message

logger = get_logger(__name__)

# Human-friendly labels for Lead Summary UI
_LABEL_MAP: Dict[str, str] = {
    "budget": "Budget Range",
    "timeline": "Timeline",
    "need": "Investment Goal",
    "property_requirements": "Property Type / Requirements",
    "location": "Preferred Location",
    "call_consent": "Open to a Call?",
    "authority": "Decision Maker",
    "motivation": "Motivation",
    "experience": "Prior Experience",
    "preference": "Preferences",
    "comparison": "Comparing Options",
}


def _get_llm_service():
    # S3_MAX exclusively uses Claude 4.5 Haiku
    from app.services.ai.claude_service import claude_service
    return claude_service


def evaluate_qualification_node(state: S3MaxState) -> Dict[str, Any]:
    """Evaluate qualification based on scorecard, LQC, and conversation context.

    Produces:
    - verdict: QUALIFIED or DISQUALIFIED
    - Rich lead output with priority, reasoning, objections, next steps
    - Summary data for the chat completion screen
    """
    scorecard = state.get("scorecard", {})
    lqc_criteria = state.get("lqc_criteria", "")
    messages = state.get("messages", [])
    questions_asked = state.get("questions_asked", 0)
    confidence_score = state.get("confidence_score", 0.0)
    safety_net = state.get("safety_net_triggered", False)
    name = state.get("name", "Unknown")
    phone = state.get("phone", "Unknown")

    llm = _get_llm_service()

    # Build scorecard text
    scorecard_text = ""
    collected_data = {}
    for dim, entry in scorecard.items():
        if isinstance(entry, dict):
            value = entry.get("value", "N/A")
            conf = entry.get("confidence", 0.0)
            scorecard_text += f"- {dim}: {value} (confidence: {conf:.1f})\n"
            collected_data[dim] = value

    # Add contact info
    collected_data["name"] = name
    collected_data["phone"] = phone

    # Build conversation transcript
    transcript = "\n".join(
        f"{'User' if m.get('role') == 'user' else 'Bot'}: {m.get('content', '')[:300]}"
        for m in messages[-20:]  # Last 20 messages
    )

    prompt = f"""You are a Senior Sales Qualification Analyst. Evaluate this lead against the qualification criteria.

QUALIFICATION CRITERIA:
{lqc_criteria or 'General: Assess budget readiness, timeline, and decision authority'}

COLLECTED DATA (SCORECARD):
{scorecard_text or 'Minimal data collected'}

CONTACT INFO:
- Name: {name}
- Phone: {phone}

CONVERSATION TRANSCRIPT (Last {min(len(messages), 20)} messages):
{transcript}

METADATA:
- Questions asked: {questions_asked}
- Overall confidence: {confidence_score:.2f}
- Safety net triggered: {safety_net}

TASK: Evaluate this lead and return a JSON object:
{{
    "verdict": "QUALIFIED" or "DISQUALIFIED",
    "lead_priority": "High" or "Medium" or "Low",
    "reasoning_summary": "<2-3 sentences explaining your rating>",
    "key_objections": ["<objection 1>", "<objection 2>"],
    "recommended_next_step": "<specific actionable next step for sales team>",
    "qualification_score": <integer 0-100>,
    "dimension_scores": {{
        "<dimension>": {{
            "score": <0-100>,
            "notes": "<brief note>"
        }}
    }}
}}

EVALUATION GUIDELINES:
- QUALIFIED: Strong intent, meets key criteria, reasonable timeline
- DISQUALIFIED: No intent, doesn't meet criteria, explicitly not interested
- High Priority: Urgent need, clear budget, decision-maker
- Medium Priority: Interested but blockers exist
- Low Priority: Browsing, no urgency, unclear fit

Return ONLY valid JSON:"""

    try:
        response = llm.generate_response(prompt)
        
        start = response.find("{")
        end = response.rfind("}") + 1
        if start != -1 and end > start:
            lead_output = json.loads(response[start:end])
        else:
            logger.warning(f"Failed to parse evaluation JSON: {response[:100]}")
            lead_output = _fallback_evaluation(scorecard, confidence_score)
    except Exception as e:
        logger.error(f"Qualification evaluation failed: {e}")
        lead_output = _fallback_evaluation(scorecard, confidence_score)

    verdict = lead_output.get("verdict", "QUALIFIED")

    # ── Contact Consent Priority Adjustment ──
    # If the user said "no" or "maybe" when asked about being contacted,
    # downgrade the lead priority by one level.
    consent_answer = (state.get("contact_consent_answer") or "yes").lower().strip()
    if consent_answer in ("no", "maybe"):
        current_priority = lead_output.get("lead_priority", "Medium")
        downgrade_map = {"High": "Medium", "Medium": "Low", "Low": "Low"}
        new_priority = downgrade_map.get(current_priority, current_priority)
        if new_priority != current_priority:
            logger.info(
                f"Contact consent '{consent_answer}': Downgrading priority "
                f"{current_priority} → {new_priority}"
            )
            lead_output["lead_priority"] = new_priority
            # Append to reasoning so sales team understands the downgrade
            existing_reasoning = lead_output.get("reasoning_summary", "")
            lead_output["reasoning_summary"] = (
                existing_reasoning +
                f" Note: Priority downgraded from {current_priority} to {new_priority} "
                f"because customer indicated they are not open to being contacted."
            )

    summary_data: Dict[str, Any] = {}
    summary_data["name"] = name
    summary_data["phone"] = phone
    for dim, entry in scorecard.items():
        if isinstance(entry, dict) and entry.get("value"):
            label = _LABEL_MAP.get(dim, dim.replace("_", " ").title())
            summary_data[dim] = {
                "label": label,
                "answer": entry.get("value", "N/A"),
            }

    # Add the rich lead output to summary
    summary_data["s3_max_lead_output"] = lead_output

    # Fast-path for explicit disinterest
    if state.get("is_disinterested", False):
        lang = state.get("language", "en")
        bot_response = get_message(lang, "disinterested", name=name)
        logger.info(f"Closed chat due to explicit user disinterest for {name}.")
        return {
            "bot_response": bot_response,
            "is_complete": True,
            "lead_verdict": "DISQUALIFIED",
            "lead_output": {
                "lead_priority": "Low",
                "qualification_score": 0.0,
                "reasoning_summary": "User explicitly stated they are not interested.",
                "key_objections": ["Not interested / requested to stop"],
                "recommended_next_step": "Do not contact. Let user reach out if interested.",
            },
            "next_state": "completed",
            "show_actions": True,
        }

    # Build the completion message
    lang = state.get("language", "en")
    if verdict == "QUALIFIED":
        bot_response = get_message(lang, "qualified", name=name)
    else:
        bot_response = get_message(lang, "disqualified", name=name)

    logger.info(
        f"Qualification result: {verdict}, "
        f"priority={lead_output.get('lead_priority', 'N/A')}, "
        f"score={lead_output.get('qualification_score', 'N/A')}"
    )

    return {
        "bot_response": bot_response,
        "is_complete": True,
        "lead_verdict": verdict,
        "lead_output": lead_output,
        "next_state": "completed",
        "show_actions": True,
        "quick_replies": [],
        "estimated_progress": 1.0,
    }


def _fallback_evaluation(scorecard: dict, confidence: float) -> dict:
    """Fallback evaluation when LLM fails."""
    # Simple heuristic: if we have decent data, qualify
    is_qualified = len(scorecard) >= 3 and confidence >= 0.5

    return {
        "verdict": "QUALIFIED" if is_qualified else "DISQUALIFIED",
        "lead_priority": "Medium",
        "reasoning_summary": "Evaluation completed with limited analysis. Manual review recommended.",
        "key_objections": [],
        "recommended_next_step": "Manual review required — contact the lead directly.",
        "qualification_score": int(confidence * 100),
        "dimension_scores": {},
    }
