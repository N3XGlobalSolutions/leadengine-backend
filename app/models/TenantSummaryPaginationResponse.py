from pydantic import BaseModel
from typing import List, Optional, Dict, Any

from app.models.TenantSummary import TenantSummary


class TenantSummaryPaginationResponse(BaseModel):
    items: List[TenantSummary]
    nextCursor: Optional[Any] = None
    total: Optional[int] = None
    planCounts: Optional[Dict[str, int]] = None