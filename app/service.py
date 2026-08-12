from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

from app.config import Settings
from app.db import Database
from app.graph import build_graph, initial_state
from app.ingestion import ParsedDocument
from app.llm import LLMClient
from app.retrieval import HybridRetriever, build_embedding_provider, build_reranker
from app.web_search import WebSearchClient, build_web_search


class ResearchFlowService:
    def __init__(self, settings: Settings, web_search: WebSearchClient | None = None):
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
        self.web_search = web_search or build_web_search(settings)
        self.graph = build_graph(
            self.db,
            self.retriever,
            self.llm,
            self.web_search,
            retrieval_top_k=max(1, min(settings.retrieval_top_k, 8)),
            web_search_max_results=max(1, min(settings.web_search_max_results, 10)),
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
        deleted = self.db.delete_document(document_id)
        if deleted:
            self.retriever.rebuild_vector_index()
        return deleted

    def metrics(self) -> dict:
        return {
            "chunks": self.db.chunk_count(),
            "llm_configured": self.llm.configured,
            "embedding_provider": self.retriever.embeddings.__class__.__name__,
            "vector_index": "FAISS IndexFlatIP",
            "vector_index_size": self.retriever.vector_index.size,
            "web_search_available": self.web_search.available,
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
        thinking_mode: str | None = None,
        source_mode: str = "auto",
    ) -> dict:
        session_id = session_id or f"ses_{uuid4().hex[:12]}"
        effective_thinking = thinking_mode if thinking_mode in {"enabled", "disabled"} else self.llm.thinking
        self.db.ensure_session(session_id)
        result = self.graph.invoke(
            initial_state(
                session_id,
                query,
                document_ids=document_ids,
                thinking_mode=effective_thinking,
                source_mode=source_mode,
            ),
            {"recursion_limit": 12},
        )
        return self._finish_chat(session_id, query, result)

    def stream_chat(
        self,
        query: str,
        session_id: str | None = None,
        document_ids: list[str] | None = None,
        thinking_mode: str | None = None,
        source_mode: str = "auto",
    ) -> Iterator[dict[str, Any]]:
        """Stream node-level progress, then emit the same final result as ``chat``.

        ``custom`` carries deliberate user-facing statuses from graph nodes;
        ``updates`` confirms each completed node and reconstructs the final
        state without rerunning the graph.
        """
        session_id = session_id or f"ses_{uuid4().hex[:12]}"
        effective_thinking = thinking_mode if thinking_mode in {"enabled", "disabled"} else self.llm.thinking
        self.db.ensure_session(session_id)
        state = initial_state(
            session_id,
            query,
            document_ids=document_ids,
            thinking_mode=effective_thinking,
            source_mode=source_mode,
        )
        final_state: dict[str, Any] = dict(state)
        completed_messages = {
            "route": "已确定执行路径。",
            "rewrite": "检索问题已准备完成。",
            "retrieve": "候选证据已找回。",
            "web_search": "网络搜索结果已获得。",
            "answer": "回答草稿已生成。",
            "verify": "引用校验已完成。",
            "persist": "运行记录已保存。",
        }
        for part in self.graph.stream(
            state,
            {"recursion_limit": 12},
            stream_mode=["custom", "updates"],
            version="v2",
        ):
            if part["type"] == "custom":
                yield {"type": "status", **part["data"]}
            elif part["type"] == "updates":
                for node, update in part["data"].items():
                    final_state.update(update)
                    yield {
                        "type": "status",
                        "node": node,
                        "phase": "completed",
                        "message": completed_messages.get(node, f"{node} 已完成。"),
                    }
        result = self._finish_chat(session_id, query, final_state)
        yield {"type": "complete", "result": result}

    def _finish_chat(self, session_id: str, query: str, result: dict[str, Any]) -> dict[str, Any]:
        # Preserve the local first-question fallback in offline/error cases.
        # A title is generated only once, after the first run has been safely
        # persisted, so it never affects evidence, answer, or run tracing.
        if self.db.session_run_count(session_id) == 1:
            title = self.llm.generate_session_title(query)
            if title:
                self.db.set_session_title(session_id, title)
        return result
