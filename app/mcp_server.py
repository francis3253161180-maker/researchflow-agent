"""MCP adapter for ResearchFlow's local research-document capabilities.

The FastAPI application remains the human-facing REST/Web surface. This module
runs separately over MCP stdio so desktop hosts can call the same retrieval
and citation-verification capabilities.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from app.config import Settings
from app.service import ResearchFlowService


def _citation(result: Any) -> dict[str, Any]:
    return {
        "chunk_id": result.chunk_id,
        "document_id": result.document_id,
        "title": result.title,
        "source": result.source,
        "filename": result.filename,
        "page": result.page,
        "section": result.section,
        "score": round(result.score, 6),
        "content": result.content,
    }


def create_mcp_server(settings: Settings | None = None) -> MCPServer:
    """Create an MCP server backed by the configured local ResearchFlow DB."""
    service = ResearchFlowService(settings or Settings.from_env())
    server = MCPServer(
        name="ResearchFlow MCP",
        version="0.1.0",
        description="Local research-document retrieval with page/section-traceable citations.",
        instructions=(
            "Use search_research_documents before answering from imported documents. "
            "Preserve returned citation metadata when reporting factual claims."
        ),
    )

    @server.tool(
        title="Search research documents",
        description=(
            "Search imported ResearchFlow documents with hybrid BM25 + vector "
            "retrieval and reciprocal-rank fusion. Returns traceable excerpts."
        ),
        structured_output=True,
    )
    def search_research_documents(query: str, top_k: int = 4) -> dict[str, Any]:
        """Search the local corpus; top_k is bounded to keep MCP results concise."""
        normalized = query.strip()
        if not normalized:
            raise ValueError("query must not be empty")
        results = service.retriever.search(normalized, top_k=max(1, min(top_k, 8)))
        return {
            "query": normalized,
            "result_count": len(results),
            "citations": [_citation(result) for result in results],
        }

    @server.tool(
        title="Get citation context",
        description=(
            "Retrieve a specific imported-document chunk by citation ID. "
            "Use it to verify a claim before citing it."
        ),
        structured_output=True,
    )
    def get_citation_context(chunk_id: str) -> dict[str, Any]:
        """Fetch a cited chunk exactly, rather than relying on a second search."""
        normalized = chunk_id.strip()
        if not normalized:
            raise ValueError("chunk_id must not be empty")
        chunk = service.db.get_chunk(normalized)
        if chunk is None:
            raise ValueError(f"citation chunk not found: {normalized}")
        return {"citation": chunk}

    @server.resource(
        "researchflow://documents",
        name="ResearchFlow document inventory",
        title="Imported research documents",
        description="Read-only inventory of documents currently indexed by ResearchFlow.",
        mime_type="application/json",
    )
    def document_inventory() -> dict[str, Any]:
        return {"documents": service.documents(), "metrics": service.metrics()}

    return server


def main() -> None:
    """Launch with standard MCP stdio transport for desktop MCP hosts."""
    create_mcp_server().run(transport="stdio")


if __name__ == "__main__":
    main()
