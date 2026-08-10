from __future__ import annotations

from contextlib import asynccontextmanager
import json
from pathlib import Path
from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings
from app.ingestion import parse_upload
from app.schemas import (
    ChatRequest,
    ChatResponse,
    DocumentCreate,
    DocumentCreated,
    DocumentSummary,
    MetricsResponse,
    RerankerStatus,
    RunDetail,
    SessionSummary,
    SessionMessage,
)
from app.service import ResearchFlowService


STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.service = ResearchFlowService(resolved_settings)
        yield

    app = FastAPI(
        title="ResearchFlow Agent",
        version="0.1.0",
        description="Locally deployable research-document Agent/RAG service.",
        lifespan=lifespan,
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
        if resolved_settings.app_api_key and not (
            x_api_key and compare_digest(x_api_key, resolved_settings.app_api_key)
        ):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid API key")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/health")
    def health():
        service: ResearchFlowService = app.state.service
        return {"status": "ok", **service.metrics()}

    @app.post("/api/documents", response_model=DocumentCreated)
    def create_document(payload: DocumentCreate, _: None = Depends(require_api_key)):
        service: ResearchFlowService = app.state.service
        document_id, count = service.ingest(payload.title, payload.source, payload.content)
        return DocumentCreated(document_id=document_id, chunks=count)

    @app.post("/api/documents/upload", response_model=DocumentCreated)
    async def upload_document(
        file: Annotated[UploadFile, File(description="PDF, DOCX, Markdown, or TXT")],
        source: str = "upload",
        _: None = Depends(require_api_key),
    ):
        if not file.filename:
            raise HTTPException(status_code=422, detail="filename is required")
        payload = await file.read(resolved_settings.max_upload_bytes + 1)
        if len(payload) > resolved_settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="file exceeds configured upload limit")
        try:
            parsed = parse_upload(file.filename, payload, source=source)
            service: ResearchFlowService = app.state.service
            document_id, count = service.ingest_parsed(parsed)
            return DocumentCreated(document_id=document_id, chunks=count)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            await file.close()

    @app.get("/api/documents", response_model=list[DocumentSummary])
    def list_documents(_: None = Depends(require_api_key)):
        service: ResearchFlowService = app.state.service
        return service.documents()

    @app.delete("/api/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_document(document_id: str, _: None = Depends(require_api_key)):
        service: ResearchFlowService = app.state.service
        if not service.delete_document(document_id):
            raise HTTPException(status_code=404, detail="document not found")

    @app.post("/api/chat", response_model=ChatResponse)
    def chat(payload: ChatRequest, _: None = Depends(require_api_key)):
        service: ResearchFlowService = app.state.service
        result = service.chat(payload.query, payload.session_id, payload.document_ids, payload.thinking_mode)
        return ChatResponse(
            run_id=result["run_id"],
            session_id=result["session_id"],
            route=result["route"],
            answer=result["answer"],
            citations=result.get("citations", []),
            verified=result["verified"],
            latency_ms=result["latency_ms"],
            events=result.get("events", []),
            errors=result.get("errors", []),
            thinking_mode=result.get("thinking_mode", "disabled"),
            retrieval_query=result.get("retrieval_query", payload.query),
            rewrite_reason=result.get("rewrite_reason", "not_applicable"),
            verify_reason=result.get("verify_reason", "not_applicable"),
        )

    @app.post("/api/chat/stream")
    def chat_stream(payload: ChatRequest, _: None = Depends(require_api_key)):
        """SSE endpoint for node progress; the final packet is ChatResponse-shaped."""
        service: ResearchFlowService = app.state.service

        def encode(event_name: str, data: dict) -> str:
            return f"event: {event_name}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"

        def events():
            try:
                for packet in service.stream_chat(
                    payload.query,
                    payload.session_id,
                    payload.document_ids,
                    payload.thinking_mode,
                ):
                    event_name = str(packet.get("type", "status"))
                    data = {key: value for key, value in packet.items() if key != "type"}
                    yield encode(event_name, data)
            except Exception as exc:
                # The client receives a safe failure category while the normal
                # graph path continues to persist its own sanitized errors.
                yield encode("error", {"message": "Agent 流式执行暂时失败，请稍后重试。", "error_type": type(exc).__name__})

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/sessions", response_model=SessionSummary)
    def create_session(_: None = Depends(require_api_key)):
        service: ResearchFlowService = app.state.service
        return SessionSummary(**service.db.create_session())

    @app.get("/api/sessions", response_model=list[SessionSummary])
    def list_sessions(_: None = Depends(require_api_key)):
        service: ResearchFlowService = app.state.service
        return [SessionSummary(**session) for session in service.db.list_sessions()]

    @app.get("/api/sessions/{session_id}", response_model=list[SessionMessage])
    def session(session_id: str, _: None = Depends(require_api_key)):
        service: ResearchFlowService = app.state.service
        return service.db.get_messages(session_id)

    @app.get("/api/sessions/{session_id}/turns", response_model=list[RunDetail])
    def session_turns(session_id: str, _: None = Depends(require_api_key)):
        service: ResearchFlowService = app.state.service
        return [RunDetail(**run) for run in service.db.get_session_turns(session_id)]

    @app.get("/api/runs/{run_id}", response_model=RunDetail)
    def run_detail(run_id: str, _: None = Depends(require_api_key)):
        service: ResearchFlowService = app.state.service
        run = service.db.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return RunDetail(**run)

    @app.get("/api/metrics", response_model=MetricsResponse)
    def metrics(_: None = Depends(require_api_key)):
        service: ResearchFlowService = app.state.service
        return service.metrics()

    @app.post("/api/reranker/toggle", response_model=RerankerStatus)
    def toggle_reranker(_: None = Depends(require_api_key)):
        service: ResearchFlowService = app.state.service
        result = service.toggle_reranker()
        if not result["available"]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=result["error"] or "BGE reranking is disabled by configuration",
            )
        return result

    return app


app = create_app()
