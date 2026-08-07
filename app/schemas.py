from typing import Any

from pydantic import BaseModel, Field


class DocumentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=20)
    source: str = Field(default="manual", max_length=500)


class DocumentCreated(BaseModel):
    document_id: str
    chunks: int


class DocumentSummary(BaseModel):
    id: str
    title: str
    source: str
    filename: str
    media_type: str
    chunks: int
    created_at: str


class MetricsResponse(BaseModel):
    chunks: int
    llm_configured: bool
    runs: int
    average_latency_ms: float
    verified_rate: float


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None


class Citation(BaseModel):
    chunk_id: str
    document_id: str
    title: str
    source: str
    content: str
    score: float
    page: int | None = None
    section: str | None = None
    filename: str = ""


class ChatResponse(BaseModel):
    run_id: str
    session_id: str
    route: str
    answer: str
    citations: list[Citation]
    verified: bool
    latency_ms: float
    events: list[dict[str, Any]]


class SessionMessage(BaseModel):
    role: str
    content: str
    created_at: str


class RunDetail(BaseModel):
    run_id: str
    session_id: str
    query: str
    route: str
    answer: str
    verified: bool
    latency_ms: float
    events: list[dict[str, Any]]
    created_at: str
