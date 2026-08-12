from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Protocol

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from app.config import Settings


class WebSearchError(RuntimeError):
    pass


class WebSearchClient(Protocol):
    @property
    def available(self) -> bool: ...

    def search(self, query: str, max_results: int) -> list[dict[str, Any]]: ...


@dataclass
class DisabledWebSearch:
    reason: str = "web search is not configured"

    @property
    def available(self) -> bool:
        return False

    def search(self, query: str, max_results: int) -> list[dict[str, Any]]:
        raise WebSearchError(self.reason)


def _content_text(result: Any) -> str:
    blocks = getattr(result, "content", []) or []
    return "\n".join(str(getattr(block, "text", "")) for block in blocks if getattr(block, "text", "")).strip()


def normalize_search_result(result: Any) -> list[dict[str, Any]]:
    structured = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    if structured is None:
        text = _content_text(result)
        try:
            structured = json.loads(text)
        except (TypeError, json.JSONDecodeError):
            structured = {"results": [{"title": "网络搜索结果", "url": "", "content": text}]}
    if isinstance(structured, list):
        items = structured
    elif isinstance(structured, dict):
        items = structured.get("results") or structured.get("data") or []
        if isinstance(items, dict):
            items = items.get("results") or [items]
    else:
        items = []
    normalized: list[dict[str, Any]] = []
    for position, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("link") or "")
        content = str(item.get("content") or item.get("snippet") or item.get("text") or "").strip()
        if not content:
            continue
        title = str(item.get("title") or url or f"网络搜索结果 {position + 1}")
        digest = hashlib.sha1(f"{url}\n{title}".encode("utf-8")).hexdigest()[:12]
        normalized.append(
            {
                "chunk_id": f"web_{digest}",
                "document_id": url or f"web_{digest}",
                "title": title,
                "source": url or "web-search",
                "filename": "",
                "page": None,
                "section": "Web",
                "score": float(item.get("score") or 0.0),
                "content": content,
            }
        )
    return normalized


class MCPWebSearchClient:
    """Call an external search provider through a configurable MCP stdio server."""

    def __init__(self, command: str, args: tuple[str, ...], tool_name: str):
        self.command = command
        self.args = list(args)
        self.tool_name = tool_name

    @property
    def available(self) -> bool:
        return bool(self.command and self.tool_name)

    async def _search_async(self, query: str, max_results: int) -> list[dict[str, Any]]:
        params = StdioServerParameters(
            command=self.command,
            args=self.args,
            env=dict(os.environ),
        )
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        self.tool_name,
                        {"query": query, "max_results": max(1, min(max_results, 10))},
                    )
        except Exception as exc:
            raise WebSearchError(f"MCP web search failed: {type(exc).__name__}") from exc
        return normalize_search_result(result)

    def search(self, query: str, max_results: int) -> list[dict[str, Any]]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._search_async(query, max_results))
        raise WebSearchError("synchronous MCP search cannot run inside an active event loop")


def build_web_search(settings: Settings) -> WebSearchClient:
    if settings.web_search_provider == "none":
        return DisabledWebSearch()
    if settings.web_search_provider != "mcp":
        raise ValueError("web_search_provider must be none or mcp")
    return MCPWebSearchClient(
        settings.web_search_mcp_command,
        settings.web_search_mcp_args,
        settings.web_search_mcp_tool,
    )
