from pydantic import BaseModel
from typing import List, Optional, Dict, Any

from app.models.TenantSummary import TenantSummary


class TenantSummaryPaginationResponse(BaseModel):
    items: List[TenantSummary]
    nextCursor: Optional[Dict[str, Any]] = None