"""Fast, reproducible regression coverage for the project documentation corpus.

This is intentionally separate from the optional local-paper evaluation: the
Markdown documents are versioned with the repository, so this test can run in
CI without accessing personal papers, resumes, models, or API keys.
"""

from pathlib import Path

from app.config import Settings
from app.ingestion import parse_upload
from app.service import ResearchFlowService


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

# These labels belong to the test only. Production retrieval never imports or
# reads them; they are simple regression checks over public project documents.
CASES = [
    ("mcp-integration.md", "get_citation_context MCP stdio citation context"),
    ("quickstarts/sqlite.md", "SQLite WAL transaction foreign key"),
    ("quickstarts/fastapi.md", "FastAPI uvicorn route request response"),
    ("quickstarts/langgraph.md", "LangGraph StateGraph node edge state"),
    ("retrieval-validation.md", "RETRIEVAL_TOP_K BGE reranker Top-N Top-K"),
]


def test_every_versioned_markdown_document_is_parseable():
    paths = sorted(DOCS.rglob("*.md"))
    assert len(paths) >= 10
    for path in paths:
        parsed = parse_upload(path.name, path.read_bytes(), source="repository-docs-test")
        assert len(parsed.content) >= 20, path
        assert parsed.blocks, path


def test_hybrid_retrieval_finds_versioned_documentation_evidence(tmp_path):
    service = ResearchFlowService(Settings(db_path=str(tmp_path / "docs-retrieval.db")))
    for path in sorted(DOCS.rglob("*.md")):
        parsed = parse_upload(path.name, path.read_bytes(), source="repository-docs-test")
        relative_title = path.relative_to(DOCS).as_posix()
        parsed = parsed.__class__(
            title=relative_title,
            source=parsed.source,
            filename=parsed.filename,
            media_type=parsed.media_type,
            content=parsed.content,
            blocks=parsed.blocks,
        )
        service.ingest_parsed(parsed)

    for expected_title, query in CASES:
        results = service.retriever.search(query, top_k=6, strategy="hybrid")
        assert any(item.title == expected_title for item in results), (expected_title, query)
