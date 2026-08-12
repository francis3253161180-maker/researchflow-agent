from types import SimpleNamespace
from pathlib import Path
import sys

from app.config import Settings
from app.service import ResearchFlowService
from app.web_search import DisabledWebSearch, MCPWebSearchClient, build_web_search, normalize_search_result


class FakeWebSearch:
    available = True

    def search(self, query, max_results):
        return [{
            "chunk_id": "web_release",
            "document_id": "https://example.com/release",
            "title": "Release notes",
            "source": "https://example.com/release",
            "content": "The current release was published today.",
            "score": 0.9,
            "page": None,
            "section": "Web",
            "filename": "",
        }]


def test_normalize_structured_mcp_search_result():
    result = SimpleNamespace(
        structured_content={
            "results": [{
                "title": "Official release",
                "url": "https://example.com/release",
                "content": "Version 2 is now available.",
                "score": 0.87,
            }]
        },
        content=[],
    )

    normalized = normalize_search_result(result)

    assert normalized[0]["chunk_id"].startswith("web_")
    assert normalized[0]["source"] == "https://example.com/release"
    assert normalized[0]["section"] == "Web"


def test_auto_routes_current_question_to_mcp_web_search(tmp_path):
    service = ResearchFlowService(Settings(db_path=str(tmp_path / "web-auto.db")), web_search=FakeWebSearch())

    result = service.chat("今天发布的最新版本是什么？", "web-auto")

    assert result["route"] == "web"
    assert result["verified"] is True
    assert result["citations"][0]["source"].startswith("https://")


def test_hybrid_mode_combines_local_and_web_evidence(tmp_path):
    service = ResearchFlowService(Settings(db_path=str(tmp_path / "hybrid.db")), web_search=FakeWebSearch())
    service.ingest("Local note", "unit-test", "The local document records the earlier system design.")

    result = service.chat("对比本地设计和当前版本", "hybrid", source_mode="hybrid")

    assert result["route"] == "hybrid"
    assert any(citation["chunk_id"].startswith("chk_") for citation in result["citations"])
    assert any(citation["chunk_id"].startswith("web_") for citation in result["citations"])


def test_web_search_builder_is_explicitly_disabled_by_default():
    client = build_web_search(Settings())

    assert isinstance(client, DisabledWebSearch)
    assert client.available is False


def test_mcp_client_configuration_is_exposed_without_starting_provider():
    client = MCPWebSearchClient("npx", ("-y", "tavily-mcp@latest"), "tavily-search")

    assert client.available is True
    assert client.args == ["-y", "tavily-mcp@latest"]


def test_mcp_web_search_client_calls_real_stdio_protocol_boundary():
    fixture = Path(__file__).parent / "fixtures" / "fake_search_mcp.py"
    client = MCPWebSearchClient(sys.executable, (str(fixture),), "tavily_search")

    results = client.search("LangGraph release", 3)

    assert results[0]["title"] == "Protocol fixture"
    assert "LangGraph release" in results[0]["content"]
