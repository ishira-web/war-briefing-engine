from datetime import datetime

from pydantic import BaseModel, HttpUrl


class ClaimPayload(BaseModel):
    claim_text: str
    citation_url: HttpUrl
    confidence_score: float = 0.5
    verdict: str = "unverified"


class IngestPayload(BaseModel):
    title: str
    summary: str
    body: str
    source_name: str = ""
    source_url: HttpUrl
    raw_title: str = ""
    raw_content: str = ""
    run_id: str = "manual"
    status: str = "ok"
    tokens: int = 0
    error: str = ""
    published_at: datetime | None = None
    region: str = "global"
    event_key: str = ""
    confidence_hint: float | None = None
    claims: list[ClaimPayload] = []
