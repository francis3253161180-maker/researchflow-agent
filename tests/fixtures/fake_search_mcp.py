from mcp.server.mcpserver import MCPServer
from typing import Any


server = MCPServer(name="Fake Search MCP", version="1.0.0")


@server.tool(structured_output=True)
def tavily_search(query: str, max_results: int = 5) -> dict[str, Any]:
    return {
        "results": [
            {
                "title": "Protocol fixture",
                "url": "https://example.com/mcp-search",
                "content": f"Search evidence for {query}",
                "score": 0.95,
            }
        ][:max_results]
    }


if __name__ == "__main__":
    server.run(transport="stdio")
