from __future__ import annotations

import re
import time
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from app.db import Database
from app.llm import LLMClient, LLMConnectionError
from app.retrieval import HybridRetriever
from app.web_search import DisabledWebSearch, WebSearchClient, WebSearchError


class AgentState(TypedDict, total=False):
    run_id: str
    session_id: str
    query: str
    retrieval_query: str
    rewrite_reason: str
    verify_reason: str
    thinking_mode: str
    document_ids: list[str] | None
    source_mode: str
    route: str
    route_steps: list[str]
    retrieved: list[dict[str, Any]]
    web_fallback_used: bool
    answer: str
    evidence_status: str
    citations: list[dict[str, Any]]
    verified: bool
    retry_count: int
    latency_ms: float
    started_at: float
    events: list[dict[str, Any]]
    errors: list[str]


def event(state: AgentState, node: str, detail: str, started_at: float | None = None) -> list[dict[str, Any]]:
    item = {"node": node, "detail": detail, "at_ms": round((time.perf_counter() - state["started_at"]) * 1000, 2)}
    if started_at is not None:
        item["duration_ms"] = round((time.perf_counter() - started_at) * 1000, 2)
    return [*state.get("events", []), item]


def emit_progress(node: str, message: str) -> None:
    """Emit a UI-safe status only when the graph is being streamed.

    Normal ``invoke`` remains supported for the REST API and test suite; in
    that mode LangGraph provides no custom stream writer, so progress is a
    deliberate no-op rather than a second execution path.
    """
    try:
        writer = get_stream_writer()
    except RuntimeError:
        return
    writer({"node": node, "phase": "running", "message": message})


def _clip_text(value: str, limit: int) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else f"{compact[:limit]}…"


def trusted_short_term_history(db: Database, session_id: str, max_turns: int = 3, max_chars: int = 2400) -> list[dict[str, str]]:
    """Build bounded rewrite context from recent verified user/assistant turns.

    The history helps resolve references such as “it” while remaining
    conversational context only: retrieval evidence still comes exclusively
    from the current search result.
    """
    remaining = max_chars
    history: list[dict[str, str]] = []
    for turn in db.get_recent_verified_turns(session_id, limit=max_turns):
        query = _clip_text(str(turn["query"]), 480)
        if len(query) >= remaining:
            break
        remaining -= len(query)
        answer_limit = min(700, remaining)
        if answer_limit < 80:
            break
        answer = _clip_text(str(turn["answer"]), answer_limit)
        remaining -= len(answer)
        history.append({"query": query, "answer": answer})
    return history


def history_as_messages(history: list[dict[str, str]]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for turn in history:
        messages.extend(({"role": "user", "content": turn["query"]}, {"role": "assistant", "content": turn["answer"]}))
    return messages


EVIDENCE_STATUS_MARKER = re.compile(
    r"\s*<!--\s*evidence_status\s*:\s*(grounded|not_relevant)\s*-->\s*$", re.IGNORECASE
)


def strip_evidence_status_marker(answer: str) -> tuple[str, str]:
    """Remove the model-only relevance verdict before storing or rendering it.

    A final marker is an explicit protocol field, not a fragile match against
    natural-language refusal wording.  A missing marker remains neutral so
    legacy/offline responders continue through normal citation validation.
    """
    match = EVIDENCE_STATUS_MARKER.search(answer)
    if not match:
        return answer, "not_reported"
    return answer[: match.start()].rstrip(), match.group(1).lower()


def build_graph(
    db: Database,
    retriever: HybridRetriever,
    llm: LLMClient,
    web_search: WebSearchClient | None = None,
    retrieval_top_k: int = 6,
    web_search_max_results: int = 5,
):
    resolved_web_search = web_search or DisabledWebSearch()
    def route_node(state: AgentState) -> dict[str, Any]:
        started = time.perf_counter()
        emit_progress("route", "正在判断问题类型与执行路径…")
        query = state["query"].strip()
        source_mode = state.get("source_mode", "auto")
        current_information = bool(
            re.search(
                r"\b(latest|current|today|recent|news|price|schedule|202[5-9])\b|"
                r"最新|今天|今日|实时|当前|近期|新闻|价格|官网|现任|今年",
                query,
                re.IGNORECASE,
            )
        )
        if source_mode == "local":
            route = "rag" if db.chunk_count() else "direct"
        elif source_mode == "web":
            route = "web"
        elif source_mode == "hybrid":
            route = "hybrid"
        elif current_information and resolved_web_search.available:
            route = "web"
        elif db.chunk_count():
            route = "rag"
        elif resolved_web_search.available:
            route = "web"
        else:
            route = "direct"
        route_steps = {
            "rag": ["检索科研文档", "基于证据生成答案", "校验引用完整性"],
            "web": ["通过 MCP 搜索网络", "基于网页证据生成答案", "校验引用完整性"],
            "hybrid": ["检索本地文档", "通过 MCP 搜索网络", "融合证据并校验引用"],
            "direct": ["检查知识库状态", "生成受限回答"],
        }
        document_ids = state.get("document_ids")
        scope_mode = "explicit" if document_ids is not None else "all_documents"
        return {
            "route": route,
            "route_steps": route_steps[route],
            "document_ids": document_ids,
            "scope_mode": scope_mode,
            "events": event(state, "route", f"route={route}; requested={source_mode}; scope={scope_mode}", started),
        }

    def rewrite_node(state: AgentState) -> dict[str, Any]:
        started = time.perf_counter()
        emit_progress("rewrite", "正在结合本会话上下文改写检索问题…")
        history = trusted_short_term_history(db, state["session_id"])
        rewrite = (
            llm.rewrite_query(state["query"], history)
            if hasattr(llm, "rewrite_query")
            else {"retrieval_query": state["query"], "rewritten": False, "reason": "rewriter_unavailable"}
        )
        retrieval_query = str(rewrite["retrieval_query"])
        detail = f"rewritten={rewrite['rewritten']}; reason={rewrite['reason']}"
        return {"retrieval_query": retrieval_query, "rewrite_reason": str(rewrite["reason"]), "events": event(state, "rewrite", detail, started)}

    def retrieve_node(state: AgentState) -> dict[str, Any]:
        started = time.perf_counter()
        emit_progress("retrieve", "正在进行 BM25 与向量混合检索…")
        query = state.get("retrieval_query", state["query"])
        rewrite_reason = state.get("rewrite_reason", "")
        retry_reason = state.get("verify_reason")
        if state.get("retry_count", 0) and retry_reason in {"no_evidence", "evidence_not_relevant"}:
            history = trusted_short_term_history(db, state["session_id"])
            retry_rewrite = (
                llm.rewrite_query(
                    state["query"],
                    history,
                    failure_reason=str(retry_reason),
                    previous_retrieval_query=query,
                    retrieval_diagnostics=state.get("retrieved", []),
                )
                if hasattr(llm, "rewrite_query")
                else {"retrieval_query": query, "rewritten": False, "reason": "rewriter_unavailable"}
            )
            query = str(retry_rewrite["retrieval_query"])
            rewrite_reason = f"retry:{retry_rewrite['reason']}"
        document_ids = state.get("document_ids")
        results = [
            item.as_dict()
            for item in retriever.search(
                query,
                top_k=retrieval_top_k,
                document_ids=document_ids,
            )
        ]
        scope = state.get("scope_mode", "all_documents")
        if document_ids is not None:
            scope = f"{scope}; documents={len(document_ids)}"
        return {"retrieved": results, "retrieval_query": query, "rewrite_reason": rewrite_reason, "events": event(state, "retrieve", f"hits={len(results)}; scope={scope}", started)}

    def web_search_node(state: AgentState) -> dict[str, Any]:
        started = time.perf_counter()
        emit_progress("web_search", "正在通过 MCP 调用网络搜索工具…")
        existing = state.get("retrieved", []) if state.get("route") == "hybrid" else []
        try:
            web_results = resolved_web_search.search(state["query"], web_search_max_results)
            errors = state.get("errors", [])
        except WebSearchError as exc:
            web_results = []
            errors = [*state.get("errors", []), str(exc)]
        fallback = state.get("route") == "rag"
        combined = [*existing, *web_results]
        return {
            "route": "web" if fallback else state["route"],
            "retrieved": combined,
            "web_fallback_used": fallback or state.get("web_fallback_used", False),
            "errors": errors,
            "events": event(state, "web_search", f"hits={len(web_results)}; combined={len(combined)}", started),
        }

    def answer_node(state: AgentState) -> dict[str, Any]:
        started = time.perf_counter()
        emit_progress("answer", "正在生成带引用的回答…")
        # A source-scoped request must not inherit factual claims from an
        # earlier answer produced over a different document set.
        memory = [] if state.get("document_ids") is not None else history_as_messages(trusted_short_term_history(db, state["session_id"]))
        contexts = state.get("retrieved", [])
        citation_retry = state.get("retry_count", 0) > 0 and state.get("verify_reason") in {
            "citation_missing",
            "citation_out_of_range",
        }
        if state.get("route") == "web" and not contexts and state.get("errors"):
            answer = "网络搜索服务当前未配置或暂不可用，未获得可核验的网页证据。"
            errors = state.get("errors", [])
        else:
            try:
                answer = llm.generate(
                    state["query"],
                    contexts,
                    memory,
                    thinking_mode=state.get("thinking_mode"),
                    citation_retry=citation_retry,
                    citation_failure_reason=state.get("verify_reason", "") if citation_retry else "",
                )
                errors = state.get("errors", [])
            except Exception as exc:
                answer = (
                    "模型网络连接暂时失败，已保留检索结果，请稍后重试。"
                    if isinstance(exc, LLMConnectionError)
                    else "模型服务暂时不可用，已保留检索结果，请稍后重试。"
                )
                # Keep persisted traces useful without storing provider responses,
                # request details, or any accidental secrets.
                error_code = f"llm_error: {type(exc).__name__}"
                errors = state.get("errors", [])
                if error_code not in errors:
                    errors = [*errors, error_code]
        answer, evidence_status = strip_evidence_status_marker(answer)
        # The prompt numbers every retrieved context. Return the same complete
        # list to clients so a model citation such as [4] never points outside
        # the displayed citation inventory.
        citations = contexts
        return {
            "answer": answer,
            "evidence_status": evidence_status,
            "citations": citations,
            "errors": errors,
            "thinking_mode": state.get("thinking_mode", "disabled"),
            "events": event(state, "answer", f"citations={len(citations)}; evidence_status={evidence_status}", started),
        }

    def verify_node(state: AgentState) -> dict[str, Any]:
        started = time.perf_counter()
        emit_progress("verify", "正在核验回答引用与证据范围…")
        route = state["route"]
        if route in {"rag", "web", "hybrid"}:
            citations = state.get("citations", [])
            indices = [int(index) for index in re.findall(r"\[(\d+)\]", state.get("answer", ""))]
            if not citations:
                verified, verify_reason = False, "no_evidence"
            elif state.get("evidence_status") == "not_relevant":
                # A model's explicit relevance verdict is a recall repair:
                # retry retrieval once before spending another generation on
                # the same candidate set.  The verdict itself is a structured
                # protocol field, never inferred from refusal wording.
                verified, verify_reason = False, "evidence_not_relevant"
            elif not indices:
                verified, verify_reason = False, "citation_missing"
            elif any(index < 1 or index > len(citations) for index in indices):
                verified, verify_reason = False, "citation_out_of_range"
            else:
                verified, verify_reason = True, "citation_indices_valid"
        else:
            verified, verify_reason = bool(state.get("answer")), "direct_answer_present"
        retry_count = state.get("retry_count", 0)
        if not verified and route in {"rag", "web", "hybrid"}:
            retry_count += 1
        return {
            "verified": verified,
            "retry_count": retry_count,
            "verify_reason": verify_reason,
            "events": event(state, "verify", f"verified={verified}; reason={verify_reason}; retries={retry_count}", started),
        }

    def persist_node(state: AgentState) -> dict[str, Any]:
        emit_progress("persist", "正在保存会话、引用和运行轨迹…")
        latency_ms = round((time.perf_counter() - state["started_at"]) * 1000, 2)
        db.add_message(state["session_id"], "user", state["query"])
        db.add_message(state["session_id"], "assistant", state["answer"])
        persist_started = time.perf_counter()
        events = event(state, "persist", f"latency_ms={latency_ms}", persist_started)
        db.save_run(
            {
                "run_id": state["run_id"],
                "session_id": state["session_id"],
                "query": state["query"],
                "retrieval_query": state.get("retrieval_query", state["query"]),
                "rewrite_reason": state.get("rewrite_reason", "not_applicable"),
                "route": state["route"],
                "answer": state["answer"],
                "verified": state["verified"],
                "latency_ms": latency_ms,
                "events": events,
                "errors": state.get("errors", []),
                "citations": state.get("citations", []),
                "thinking_mode": state.get("thinking_mode", "disabled"),
                "verify_reason": state.get("verify_reason", "not_applicable"),
            }
        )
        return {"latency_ms": latency_ms, "events": events}

    def after_route(state: AgentState) -> str:
        return {"rag": "rewrite", "web": "web_search", "hybrid": "rewrite", "direct": "answer"}[state["route"]]

    def after_retrieve(state: AgentState) -> str:
        return "web_search" if state["route"] == "hybrid" else "answer"

    def after_verify(state: AgentState) -> str:
        if state["route"] in {"rag", "hybrid"} and not state["verified"] and state.get("retry_count", 0) == 1:
            return "retrieve" if state.get("verify_reason") in {"no_evidence", "evidence_not_relevant"} else "answer"
        if (
            state["route"] == "rag"
            and state.get("source_mode") == "auto"
            and resolved_web_search.available
            and not state.get("verified")
            and state.get("verify_reason") in {"no_evidence", "evidence_not_relevant"}
            and not state.get("web_fallback_used")
        ):
            return "web_search"
        return "persist"

    graph = StateGraph(AgentState)
    graph.add_node("route", route_node)
    graph.add_node("rewrite", rewrite_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("web_search", web_search_node)
    graph.add_node("answer", answer_node)
    graph.add_node("verify", verify_node)
    graph.add_node("persist", persist_node)
    graph.add_edge(START, "route")
    graph.add_conditional_edges("route", after_route, {"rewrite": "rewrite", "web_search": "web_search", "answer": "answer"})
    graph.add_edge("rewrite", "retrieve")
    graph.add_conditional_edges("retrieve", after_retrieve, {"web_search": "web_search", "answer": "answer"})
    graph.add_edge("web_search", "answer")
    graph.add_edge("answer", "verify")
    graph.add_conditional_edges("verify", after_verify, {"retrieve": "retrieve", "web_search": "web_search", "answer": "answer", "persist": "persist"})
    graph.add_edge("persist", END)
    return graph.compile()


def initial_state(
    session_id: str,
    query: str,
    document_ids: list[str] | None = None,
    thinking_mode: str = "disabled",
    source_mode: str = "auto",
) -> AgentState:
    return {
        "run_id": f"run_{uuid4().hex[:12]}",
        "session_id": session_id,
        "query": query,
        "retrieval_query": query,
        "rewrite_reason": "not_started",
        "verify_reason": "not_started",
        "evidence_status": "not_reported",
        "thinking_mode": thinking_mode,
        "document_ids": document_ids,
        "source_mode": source_mode,
        "web_fallback_used": False,
        "retry_count": 0,
        "started_at": time.perf_counter(),
        "events": [],
        "errors": [],
    }
