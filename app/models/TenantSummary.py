from pydantic import BaseModel
from typing import Optional

class TenantSummary(BaseModel):
    id: str
    name: str
    onboardedDate: Optional[str] = None
    plan: Optional[str] = None
    slug: Optional[str] = None
    status: Optional[str] = None
    totalUsers: int = 0