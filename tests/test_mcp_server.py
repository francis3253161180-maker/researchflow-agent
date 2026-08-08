import asyncio
from pathlib import Path
import sys

from app.config import Settings
from app.mcp_server import create_mcp_server
from app.service import ResearchFlowService
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def test_mcp_server_lists_tools_and_searches_traceable_content(tmp_path):
    settings = Settings(db_path=str(tmp_path / "mcp.db"))
    seed = ResearchFlowService(settings)
    seed.ingest(
        "MCP note",
        "unit-test",
        "Model Context Protocol standardizes how an AI host invokes external tools and reads context.",
    )
    server = create_mcp_server(settings)

    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert {"search_research_documents", "get_citation_context", "calculate_expression"} <= names

    result = asyncio.run(server.call_tool("search_research_documents", {"query": "Model Context Protocol", "top_k": 3}))
    payload = result.structured_content
    assert payload["result_count"] == 1
    assert payload["citations"][0]["title"] == "MCP note"
    assert payload["citations"][0]["chunk_id"].startswith("chk_")


def test_mcp_server_reads_citation_and_exposes_inventory_resource(tmp_path):
    settings = Settings(db_path=str(tmp_path / "mcp-resource.db"))
    seed = ResearchFlowService(settings)
    seed.ingest("ResearchFlow", "unit-test", "Citation verification requires preserving document provenance.")
    server = create_mcp_server(settings)

    searched = asyncio.run(server.call_tool("search_research_documents", {"query": "citation verification"}))
    chunk_id = searched.structured_content["citations"][0]["chunk_id"]
    citation = asyncio.run(server.call_tool("get_citation_context", {"chunk_id": chunk_id}))
    assert citation.structured_content["citation"]["document_id"].startswith("doc_")
    assert "Citation verification" in citation.structured_content["citation"]["content"]

    resources = list(asyncio.run(server.read_resource("researchflow://documents")))
    assert "ResearchFlow" in resources[0].content


def test_mcp_safe_calculator_reuses_restricted_tool(tmp_path):
    server = create_mcp_server(Settings(db_path=str(tmp_path / "mcp-calculator.db")))
    result = asyncio.run(server.call_tool("calculate_expression", {"expression": "12 * (3 + 2)"}))
    assert result.structured_content == {"result": "12 * (3 + 2) = 60"}


def test_mcp_stdio_client_discovers_and_calls_server(tmp_path):
    """Verify the actual protocol boundary, not only Python-level adapter calls."""
    settings = Settings(db_path=str(tmp_path / "mcp-e2e.db"))
    seed = ResearchFlowService(settings)
    seed.ingest("E2E MCP", "unit-test", "ResearchFlow exposes traceable research retrieval through MCP tools.")

    async def run_client() -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp_server"],
            cwd=str(Path.cwd()),
            env={"RESEARCHFLOW_DB_PATH": settings.db_path},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert "search_research_documents" in {tool.name for tool in tools.tools}
                result = await session.call_tool(
                    "search_research_documents", {"query": "traceable research retrieval"}
                )
                assert result.structured_content["citations"][0]["title"] == "E2E MCP"

    asyncio.run(run_client())
