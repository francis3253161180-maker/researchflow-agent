from __future__ import annotations

from uuid import uuid4

from app.config import Settings
from app.db import Database
from app.graph import build_graph, initial_state
from app.ingestion import ParsedDocument
from app.llm import LLMClient
from app.retrieval import HybridRetriever, build_embedding_provider, build_reranker


class ResearchFlowService:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.ensure_directories()
        self.db = Database(settings.db_path)
        # ``auto`` eagerly loads BGE only on CUDA. On CPU, the model remains
        # absent until the user explicitly starts it from the UI/API.
        self._available_reranker = build_reranker(settings)
        self._reranker_error = ""
        self.retriever = HybridRetriever(
            self.db,
            build_embedding_provider(settings),
            self._available_reranker,
            settings.reranker_candidates,
        )
        self.llm = LLMClient(settings)
        self.graph = build_graph(
            self.db,
            self.retriever,
            self.llm,
            retrieval_top_k=max(1, min(settings.retrieval_top_k, 8)),
        )

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
        return {
            "chunks": self.db.chunk_count(),
            "llm_configured": self.llm.configured,
            "embedding_provider": self.retriever.embeddings.__class__.__name__,
            "reranker_requested": self.settings.reranker_provider,
            "reranker_available": self._available_reranker is not None,
            "reranker_can_start": self.settings.reranker_provider != "none",
            "reranker_active": self.retriever.reranker is not None,
            "reranker_provider": self._available_reranker.__class__.__name__ if self._available_reranker else "none",
            "reranker_error": self._reranker_error,
            **self.db.run_summary(),
        }

    def toggle_reranker(self) -> dict:
        """Toggle BGE; an explicit CPU click lazily constructs it once."""
        if self.retriever.reranker is not None:
            self.retriever.reranker = None
            return self.reranker_status()
        if self._available_reranker is None and self.settings.reranker_provider != "none":
            try:
                self._available_reranker = build_reranker(self.settings, allow_cpu=True)
                self._reranker_error = ""
            except Exception as exc:  # configuration/download errors are returned to the control surface
                self._reranker_error = str(exc)
        if self._available_reranker is None:
            return self.reranker_status()
        self.retriever.reranker = self._available_reranker
        return self.reranker_status()

    def reranker_status(self) -> dict:
        return {
            "requested": self.settings.reranker_provider,
            "available": self._available_reranker is not None,
            "can_start": self.settings.reranker_provider != "none",
            "active": self.retriever.reranker is not None,
            "provider": self._available_reranker.__class__.__name__ if self._available_reranker else "none",
            "error": self._reranker_error,
        }

    def chat(
        self,
        query: str,
        session_id: str | None = None,
        document_ids: list[str] | None = None,
    ) -> dict:
        session_id = session_id or f"ses_{uuid4().hex[:12]}"
        result = self.graph.invoke(
            initial_state(session_id, query, document_ids=document_ids),
            {"recursion_limit": 12},
        )
        return result
