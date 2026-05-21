"""Memory Agent — Maintains qualification scorecard and tracks coverage.

The "brain" of the S3 Max flow. Tracks:
- Which dimensions have been covered
- Confidence level per dimension
- Overall qualification confidence score
- Estimated progress for frontend display
- Whether early exit conditions are met
"""

from typing import Any, Dict

from app.core.logging_config import get_logger
from app.services.chat.agents.graph_state import S3MaxState

logger = get_logger(__name__)

# Core dimensions that should be covered for a thorough qualification
DEFAULT_REQUIRED_DIMENSIONS = [
    "budget",
    "timeline",
    "need",
    "property_requirements",
    "call_consent",
]

# Minimum dimensions covered before allowing early exit
MIN_DIMENSIONS_FOR_EARLY_EXIT = 3

# Confidence threshold for early exit
EARLY_EXIT_CONFIDENCE = 0.85

# Minimum scorecard fill ratio before allowing evaluation
MIN_INFO_THRESHOLD = 0.6


def update_memory_node(state: S3MaxState) -> Dict[str, Any]:
    """Update the qualification scorecard and compute progress metrics.

    Reads from:
    - scorecard (updated by response_analyzer)
    - covered_dimensions
    - required_dimensions
    - questions_asked

    Writes:
    - confidence_score (overall)
    - estimated_progress (for frontend)
    """
    scorecard = state.get("scorecard", {})
    covered = state.get("covered_dimensions", [])
    required = state.get("required_dimensions", DEFAULT_REQUIRED_DIMENSIONS)
    questions_asked = state.get("questions_asked", 0)

    # 1. Compute per-dimension confidence
    dimension_confidences = []
    for dim, entry in scorecard.items():
        conf = entry.get("confidence", 0.0) if isinstance(entry, dict) else 0.0
        dimension_confidences.append(conf)

    avg_confidence = (
        sum(dimension_confidences) / len(dimension_confidences)
        if dimension_confidences
        else 0.0
    )

    # 2. Compute coverage ratio (how many required dimensions are covered)
    required_covered = sum(1 for d in required if d in covered)
    coverage_ratio = required_covered / len(required) if required else 0.0

    # 3. Compute overall info ratio (scorecard fill)
    total_possible = max(len(required), 5)  # At least 5 dimensions expected
    info_ratio = len(scorecard) / total_possible
    info_ratio = min(info_ratio, 1.0)  # Cap at 1.0

    # 4. Compute overall confidence score
    # Weight: 40% average_confidence + 30% coverage + 30% info_ratio
    confidence_score = (
        avg_confidence * 0.4
        + coverage_ratio * 0.3
        + info_ratio * 0.3
    )
    confidence_score = min(confidence_score, 1.0)

    # 5. Estimate progress for frontend
    # Based on: dimensions covered, questions asked (normalized to 3-20 range)
    question_progress = min(questions_asked / 10.0, 1.0)  # Normalize to ~10 Q target
    dimension_progress = len(covered) / max(len(required), 3)
    dimension_progress = min(dimension_progress, 1.0)

    estimated_progress = (
        dimension_progress * 0.6
        + question_progress * 0.3
        + avg_confidence * 0.1
    )
    estimated_progress = min(estimated_progress, 0.95)  # Never show 100% until done

    logger.info(
        f"Memory update: confidence={confidence_score:.2f}, "
        f"progress={estimated_progress:.2f}, "
        f"covered={len(covered)}/{len(required)}, "
        f"questions={questions_asked}"
    )

    return {
        "confidence_score": confidence_score,
        "estimated_progress": estimated_progress,
    }


def can_early_exit(state: S3MaxState) -> bool:
    """Check if conditions for early exit are met.

    Requirements:
    1. At least 5 questions asked
    2. Confidence score > 0.85
    3. At least 3 dimensions covered
    4. Required dimensions covered
    """
    questions_asked = state.get("questions_asked", 0)
    confidence = state.get("confidence_score", 0.0)
    covered = state.get("covered_dimensions", [])
    required = state.get("required_dimensions", DEFAULT_REQUIRED_DIMENSIONS)

    if questions_asked < 5:
        return False

    if confidence < EARLY_EXIT_CONFIDENCE:
        return False

    if len(covered) < MIN_DIMENSIONS_FOR_EARLY_EXIT:
        return False

    # Check that at least most required dimensions are covered
    required_covered = sum(1 for d in required if d in covered)
    if required_covered < len(required) * 0.7:
        return False

    logger.info(
        f"Early exit conditions met: q={questions_asked}, "
        f"conf={confidence:.2f}, dims={len(covered)}"
    )
    return True


def has_minimum_info(state: S3MaxState) -> bool:
    """Check if we have enough info for any evaluation (including safety net).

    Must have at least 60% of expected information.
    """
    scorecard = state.get("scorecard", {})
    required = state.get("required_dimensions", DEFAULT_REQUIRED_DIMENSIONS)

    total_possible = max(len(required), 3)
    info_ratio = len(scorecard) / total_possible

    return info_ratio >= MIN_INFO_THRESHOLD
