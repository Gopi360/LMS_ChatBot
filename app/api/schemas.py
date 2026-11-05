"""The shape of the REST API. The demo page and the LMS are built against these fields."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    message: str
    student_id: Optional[str] = None        # accepted for the LMS, not used yet


class SourceOut(BaseModel):
    number: int
    document: str
    course_name: str
    page: int
    page_end: int
    section: str


class ChatResponse(BaseModel):
    session_id: str
    status: str                             # answered, not_found, out_of_scope, needs_course or unavailable
    answer: str                             # for the student: no citation markers
    cited_answer: str                       # as the chatbot wrote it, for an admin view
    sources: List[SourceOut]
    seconds: float


class HealthResponse(BaseModel):
    status: str                             # ok or degraded
    provider: str
    model: str
    llm_ready: bool
    index_chunks: int
    courses: int
    warming: bool
