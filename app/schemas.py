from typing import Any, Literal

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
    embedding_provider: str
    vector_index: str
    vector_index_size: int
    web_search_available: bool
    reranker_requested: str
    reranker_available: bool
    reranker_can_start: bool
    reranker_active: bool
    reranker_provider: str
    reranker_error: str
    runs: int
    average_latency_ms: float
    verified_rate: float


class RerankerStatus(BaseModel):
    requested: str
    available: bool
    can_start: bool
    active: bool
    provider: str
    error: str


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
    # ``None`` means search the complete knowledge base.  A populated list is
    # an explicit evidence boundary chosen by the caller/UI, not an attempt to
    # infer source restrictions from natural-language wording.
    document_ids: list[str] | None = Field(default=None, max_length=100)
    thinking_mode: Literal["enabled", "disabled"] | None = None
    source_mode: Literal["auto", "local", "web", "hybrid"] = "auto"


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
    errors: list[str] = []
    thinking_mode: Literal["enabled", "disabled"]
    retrieval_query: str
    rewrite_reason: str
    verify_reason: str


class SessionMessage(BaseModel):
    role: str
    content: str
    created_at: str


class RunDetail(BaseModel):
    run_id: str
    session_id: str
    query: str
    retrieval_query: str = ""
    rewrite_reason: str = "not_applicable"
    verify_reason: str = "not_applicable"
    route: str
    answer: str
    verified: bool
    latency_ms: float
    events: list[dict[str, Any]]
    errors: list[str]
    citations: list[Citation] = []
    thinking_mode: Literal["enabled", "disabled"] = "disabled"
    created_at: str


class SessionSummary(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    runs: int
