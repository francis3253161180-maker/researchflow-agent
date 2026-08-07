from __future__ import annotations

from uuid import uuid4

from app.config import Settings
from app.db import Database
from app.graph import build_graph, initial_state
from app.ingestion import ParsedDocument
from app.llm import LLMClient
from app.retrieval import HybridRetriever, build_embedding_provider


class ResearchFlowService:
    def __init__(self, settings: Settings):
        settings.ensure_directories()
        self.db = Database(settings.db_path)
        self.retriever = HybridRetriever(self.db, build_embedding_provider(settings))
        self.llm = LLMClient(settings)
        self.graph = build_graph(self.db, self.retriever, self.llm)

    def ingest(self, title: str, source: str, content: str) -> tuple[str, int]:
        return self.retriever.ingest(title, source, content)

    def ingest_parsed(self, document: ParsedDocument) -> tuple[str, int]:
        return self.retriever.ingest(
            title=document.title,
            source=document.source,
            content=document.content,
            blocks=document.blocks,
            filename=document.filename,
            media_type=document.media_type,
        )

    def documents(self) -> list[dict]:
        return self.db.list_documents()

    def delete_document(self, document_id: str) -> bool:
        return self.db.delete_document(document_id)

    def metrics(self) -> dict:
        return {"chunks": self.db.chunk_count(), "llm_configured": self.llm.configured, **self.db.run_summary()}

    def chat(self, query: str, session_id: str | None = None) -> dict:
        session_id = session_id or f"ses_{uuid4().hex[:12]}"
        result = self.graph.invoke(initial_state(session_id, query), {"recursion_limit": 12})
        return result
