"""
fxi.claims.models - 四层主张可信度与断言模型
"""

from typing import Optional
from pydantic import BaseModel


class ClaimEvidence(BaseModel):
    id: Optional[int] = None
    claim_id: str
    source_id: str
    scene_uuid: str
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    quote: str


class ClaimVersion(BaseModel):
    claim_id: str
    claim_family_id: str
    version: int
    work_id: Optional[str] = None
    status: str = "candidate"  # accepted | candidate | superseded | rejected
    statement: str
    semantic_hash: str
    created_at: Optional[str] = None


class RetconDeclaration(BaseModel):
    retcon_id: str
    work_id: str
    superseded_claim_id: str
    new_claim_id: str
    effective_narrative_order: int
    author_note: str
