"""Lead Qualification Criteria (LQC) Evaluator Service.

This service evaluates whether a user's answers meet the tenant's
lead qualification criteria using LLM-based natural language evaluation.
"""

from typing import Dict, List
from app.core.logging_config import get_logger
from app.models.tenant import QualificationQuestion

logger = get_logger(__name__)


class LQCEvaluator:
    """Evaluates lead qualification criteria using LLM."""
    
    def __init__(self):
        self._llm_service = None
    
    @property
    def llm_service(self):
        """Lazy load LLM service to avoid circular imports."""
        if self._llm_service is None:
            from app.services.ai.llm_service import llm_service
            self._llm_service = llm_service
        return self._llm_service
    
    def evaluate_criteria(
        self,
        answers: Dict[str, str],
        questions: List[QualificationQuestion],
        criteria: str
    ) -> bool:
        """
        Evaluate if user's answers meet the qualification criteria.
        
        Args:
            answers: Dict of question IDs to user answers
            questions: List of qualification questions
            criteria: Natural language criteria (e.g., "Budget > $50k, timeline within 6 months")
        
        Returns:
            True if user meets criteria (QUALIFIED), False otherwise
        """
        if not criteria or not criteria.strip():
            # No criteria defined - always qualify
            return True
        
        # Build evaluation prompt
        prompt = self._build_evaluation_prompt(answers, questions, criteria)
        
        try:
            response = self.llm_service.generate_response(prompt)
            
            # Parse LLM response
            is_qualified = self._parse_qualification_result(response)
            
            logger.info(f"LQC Evaluation: {is_qualified}, Response: {response[:100]}")
            
            return is_qualified
            
        except Exception as e:
            logger.error(f"LQC evaluation failed: {e}")
            # On error, default to True (don't penalize user for system error)
            return True
    
    def _build_evaluation_prompt(
        self,
        answers: Dict[str, str],
        questions: List[QualificationQuestion],
        criteria: str
    ) -> str:
        """Build the evaluation prompt for the LLM."""
        
        # Build Q&A list
        qa_list = []
        
        # Add name and phone if present
        if answers.get("name"):
            qa_list.append(f"- Name: {answers['name']}")
        if answers.get("phone"):
            qa_list.append(f"- Phone: {answers['phone']}")
        
        # Add qualification question answers
        for q in questions:
            # Skip contact questions (name/phone)
            collects_lower = [f.lower() for f in q.collects]
            if 'name' in collects_lower or 'phone' in collects_lower:
                continue
            
            answer = answers.get(q.id, "Not provided")
            qa_list.append(f"- {q.label}: {answer}")
        
        qa_text = "\n".join(qa_list)
        
        prompt = f"""You are a lead qualification system. Your ONLY job is to output ONE WORD.

USER'S ANSWERS:
{qa_text}

QUALIFICATION CRITERIA:
{criteria}

INSTRUCTIONS:
1. Analyze if the user's answers meet ALL the qualification criteria
2. Respond with EXACTLY ONE WORD - nothing else
3. Do NOT add explanations, punctuation, or extra text
4. Do NOT use quotes around your answer

EXAMPLES OF CORRECT RESPONSES:
- QUALIFIED
- NOT_QUALIFIED

EXAMPLES OF INCORRECT RESPONSES (DO NOT DO THIS):
- "The user is QUALIFIED because..." ❌
- QUALIFIED. ❌
- "QUALIFIED" ❌
- Yes, QUALIFIED ❌

YOUR ONE-WORD ANSWER (QUALIFIED or NOT_QUALIFIED):"""
        
        return prompt
    
    def _parse_qualification_result(self, response: str) -> bool:
        """Parse LLM response to determine if qualified.
        
        Handles various formats:
        - "QUALIFIED"
        - "NOT_QUALIFIED"
        - "QUALIFIED." (with punctuation)
        - '"QUALIFIED"' (with quotes)
        - "The answer is QUALIFIED" (with extra text)
        """
        # Clean the response
        response_clean = response.strip()
        
        # Remove common punctuation
        response_clean = response_clean.rstrip('.,!?;:')
        
        # Remove quotes
        response_clean = response_clean.strip('"\'')
        
        # Convert to lowercase for comparison
        response_lower = response_clean.lower()
        
        # Extract just the key word if there's extra text
        # Check for "NOT_QUALIFIED" or "not qualified" first (more specific)
        if "not_qualified" in response_lower or "not qualified" in response_lower:
            return False
        
        # Check for "QUALIFIED" without "not"
        if "qualified" in response_lower:
            return True
        
        # Check for YES/NO as fallback
        if response_lower.startswith("yes") or response_lower == "y":
            return True
        if response_lower.startswith("no") or response_lower == "n":
            return False
        
        # If response is just whitespace or unclear
        if not response_clean:
            logger.warning("Empty LQC response, defaulting to QUALIFIED")
            return True
        
        # Log unclear response but default to True (benefit of doubt)
        logger.warning(f"Unclear LQC response: '{response}', defaulting to QUALIFIED")
        return True


# Singleton instance
lqc_evaluator = LQCEvaluator()
