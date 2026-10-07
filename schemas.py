from typing import Literal, Optional

from pydantic import BaseModel, Field, computed_field


class RedFlag(BaseModel):
    issue: str
    page: Optional[int] = None
    quote: Optional[str] = None


class Audit(BaseModel):
    status: Literal["VALID", "ILLEGAL", "INCOMPLETE", "NOT_A_CONTRACT"]
    confidence: Literal["high", "medium", "low"] = "medium"
    contract_type: str = "Unknown"
    reasoning_summary: str
    missing_critical_elements: list[str] = Field(default_factory=list)
    red_flags: list[RedFlag] = Field(default_factory=list)

    # Derived in code (not asked from the LLM) so it can never contradict `status`.
    @computed_field
    @property
    def is_safe_to_proceed(self) -> bool:
        return self.status in ("VALID", "INCOMPLETE")


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    top_k: int = Field(default=6, ge=1, le=12)


class Citation(BaseModel):
    chunk_id: str
    page: int
    paragraph: str
    excerpt: str


class AskResponse(BaseModel):
    answer: str
    found: bool
    citations: list[Citation]
    disclaimer: str = "This is not legal advice. Please consult a qualified lawyer before signing."
