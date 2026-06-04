"""Question Bank Warm-up Service.

Pre-generates qualification questions from the tenant's knowledge base
when documents are uploaded. This gives the question generator agent
a head start with relevant, contextual questions.

Triggered by:
- Document ingestion completion (both sync and async paths)
- Manual refresh by admin

The question bank is stored in tenant_config.question_bank in DynamoDB.
"""

import json
from typing import Any, Dict, List, Optional

from app.core.logging_config import get_logger

logger = get_logger(__name__)


def _get_llm_service():
    from app.services.ai.llm_service import llm_service
    return llm_service


def _get_rag_service():
    from app.services.ai.rag_service import rag_service
    return rag_service


def generate_question_bank(
    tenant_id: str,
    lqc_criteria: str,
    num_questions: int = 20,
) -> List[Dict[str, Any]]:
    """Generate a question bank from the tenant's knowledge base and LQC.
    
    Args:
        tenant_id: Tenant ID to fetch KB chunks for
        lqc_criteria: Natural language qualification criteria
        num_questions: Target number of questions to generate (15-25)
    
    Returns:
        List of question dicts with id, question, dimension, priority, quick_replies
    """
    llm = _get_llm_service()
    rag = _get_rag_service()

    # 1. Retrieve representative chunks from the knowledge base
    try:
        # Search with broad queries to get diverse context
        broad_queries = [
            "product features and pricing",
            "services offered",
            "target customers",
            "common requirements",
            "plans and options",
        ]
        
        all_chunks = []
        seen_texts = set()
        for query in broad_queries:
            results = rag.search(query, tenant_id, limit=3)
            for r in results:
                text = r.get("text", "")
                if text and text[:100] not in seen_texts:
                    all_chunks.append(text)
                    seen_texts.add(text[:100])

        kb_context = "\n\n".join(all_chunks[:10])  # Top 10 unique chunks
    except Exception as e:
        logger.error(f"Failed to retrieve KB context for question bank: {e}")
        kb_context = ""

    if not kb_context:
        logger.warning(f"No KB context found for tenant {tenant_id}, generating generic questions")

    # 2. Generate questions using LLM
    prompt = f"""You are creating a question bank for a sales qualification chatbot.

COMPANY KNOWLEDGE BASE:
{kb_context[:2000] if kb_context else 'No specific knowledge base available. Generate general qualification questions.'}

LEAD QUALIFICATION CRITERIA:
{lqc_criteria or 'General: Assess budget, timeline, need, authority, and fit.'}

TASK: Generate {num_questions} diverse qualification questions that a sales chatbot should ask prospects.

RULES:
1. Questions should be relevant to the company's products/services
2. Cover these dimensions: budget, timeline, need, authority, experience, preference, use_case, team_size, urgency, decision_process
3. Make questions conversational and friendly (not robotic)
4. Include 2-4 relevant quick reply options per question
5. Assign priority: "high" for critical qualifying questions, "medium" for nice-to-have, "low" for optional
6. Each question should collect specific, useful information
7. Mix open-ended and closed questions

Return a JSON array:
[
    {{
        "id": "qb_001",
        "question": "<conversational qualification question>",
        "dimension": "<budget|timeline|need|authority|experience|preference|use_case|team_size|urgency|decision_process>",
        "priority": "<high|medium|low>",
        "quick_replies": ["Option 1", "Option 2", "Option 3"]
    }},
    ...
]

IMPORTANT: Return ONLY the JSON array, nothing else.

JSON:"""

    try:
        response = llm.generate_response(prompt)

        # Find the JSON array boundaries
        start = response.find("[")
        if start == -1:
            logger.warning("No JSON array found in question bank response")
            return _fallback_question_bank()

        raw_json = response[start:]

        # Strategy 1: Clean parse — works when LLM output is complete
        questions = _try_parse_json(raw_json)

        # Strategy 2: Repair truncated JSON — LLM sometimes cuts off mid-entry
        # Find last fully-closed object (ends with "}") before truncation point
        if questions is None:
            repaired = _repair_truncated_json_array(raw_json)
            if repaired:
                questions = _try_parse_json(repaired)
                if questions is not None:
                    logger.info(
                        f"Question bank JSON repaired: recovered "
                        f"{len(questions)} questions after truncation"
                    )

        if not questions:
            logger.warning(f"Could not parse question bank JSON, using fallback")
            return _fallback_question_bank()

        # Validate and normalize structure
        validated = []
        for i, q in enumerate(questions):
            if isinstance(q, dict) and "question" in q:
                validated.append({
                    "id": q.get("id", f"qb_{i+1:03d}"),
                    "question": q["question"],
                    "dimension": q.get("dimension", "general"),
                    "priority": q.get("priority", "medium"),
                    "quick_replies": q.get("quick_replies", []),
                })

        logger.info(
            f"Generated question bank for tenant {tenant_id}: "
            f"{len(validated)} questions"
        )
        return validated if validated else _fallback_question_bank()

    except Exception as e:
        logger.error(f"Question bank generation failed: {e}")
        return _fallback_question_bank()


def warm_up_question_bank(tenant_id: str) -> bool:
    """Trigger question bank regeneration for a tenant.
    
    Called after document ingestion to refresh the bank with new KB context.
    Only runs for S3_MAX plan tenants.
    
    Returns:
        True if bank was updated, False if skipped or failed
    """
    try:
        from app.services import tenant_service
        from app.models.tenant import Plan

        tenant = tenant_service.get_tenant(tenant_id)
        if not tenant:
            logger.warning(f"Tenant not found for question bank warm-up: {tenant_id}")
            return False

        # Only generate for S3_MAX tenants
        if tenant.config.plan != Plan.S3_MAX:
            return False

        lqc = tenant.config.lead_qualification_criteria or ""
        
        # Generate the question bank
        bank = generate_question_bank(tenant_id, lqc)
        
        if not bank:
            logger.warning(f"Empty question bank generated for {tenant_id}")
            return False

        # Save to tenant config
        from app.models.tenant import TenantUpdate, TenantConfig

        # Get current config and update question_bank
        current_config = tenant.config.model_dump(mode="json")
        current_config["question_bank"] = bank
        
        updated_config = TenantConfig(**current_config)
        tenant_service.update_tenant(
            tenant_id,
            TenantUpdate(config=updated_config),
        )

        logger.info(
            f"Question bank warm-up complete for {tenant_id}: "
            f"{len(bank)} questions generated"
        )
        return True

    except Exception as e:
        logger.error(f"Question bank warm-up failed for {tenant_id}: {e}")
        return False


def _try_parse_json(text: str):
    """Attempt JSON parse, return None (not exception) on failure."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _repair_truncated_json_array(raw: str) -> str:
    """Repair a truncated JSON array by finding the last fully-closed object.

    The LLM (Nova Lite) truncates output mid-entry when response is long.
    This recovers all complete question objects before the cut-off point.

    Example input (truncated):
        [{"id":"qb_001","question":"..."},{"id":"qb_002","question":"What's your budg
    Example output (repaired):
        [{"id":"qb_001","question":"..."}]
    """
    if not raw.startswith("["):
        return ""

    # Walk backwards from end to find last '}' that closes a complete object
    # then close the array after it
    last_close = raw.rfind("}")
    if last_close == -1:
        return ""

    # Slice up to and including that closing brace, then close the array
    candidate = raw[: last_close + 1].rstrip().rstrip(",") + "\n]"

    # Quick sanity: make sure it at least starts like a valid array
    if candidate.startswith("[") and candidate.endswith("]"):
        return candidate
    return ""


def _fallback_question_bank() -> List[Dict[str, Any]]:
    """Fallback generic questions when generation fails."""
    return [
        {
            "id": "qb_001",
            "question": "What's the main challenge you're looking to solve?",
            "dimension": "need",
            "priority": "high",
            "quick_replies": ["Cost reduction", "Efficiency", "Growth", "Quality"],
        },
        {
            "id": "qb_002",
            "question": "What's your ideal timeline for getting started?",
            "dimension": "timeline",
            "priority": "high",
            "quick_replies": ["ASAP", "1-3 months", "3-6 months", "Just exploring"],
        },
        {
            "id": "qb_003",
            "question": "Do you have a budget range in mind for this?",
            "dimension": "budget",
            "priority": "high",
            "quick_replies": ["Under 50K", "50K-1L", "1L-5L", "Flexible"],
        },
        {
            "id": "qb_004",
            "question": "Will you be the main decision-maker on this?",
            "dimension": "authority",
            "priority": "high",
            "quick_replies": ["Yes, it's my call", "Part of a team", "Need to consult"],
        },
        {
            "id": "qb_005",
            "question": "Have you used a similar solution before?",
            "dimension": "experience",
            "priority": "medium",
            "quick_replies": ["Yes, currently using one", "Tried before", "First time"],
        },
    ]
