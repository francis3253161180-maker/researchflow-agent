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
from app.tools import calculate


class AgentState(TypedDict, total=False):
    run_id: str
    session_id: str
    query: str
    retrieval_query: str
    rewrite_reason: str
    verify_reason: str
    thinking_mode: str
    document_ids: list[str] | None
    route: str
    plan: list[str]
    retrieved: list[dict[str, Any]]
    tool_result: str
    answer: str
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


def build_graph(db: Database, retriever: HybridRetriever, llm: LLMClient, retrieval_top_k: int = 6):
    def plan_node(state: AgentState) -> dict[str, Any]:
        started = time.perf_counter()
        emit_progress("plan", "正在判断问题类型与执行路径…")
        query = state["query"].strip()
        has_math = bool(re.search(r"\d\s*[-+*/%]\s*\d", query))
        route = "tool" if has_math else ("rag" if db.chunk_count() else "direct")
        plans = {
            "tool": ["识别计算表达式", "执行安全计算工具", "校验并返回结果"],
            "rag": ["检索科研文档", "基于证据生成答案", "校验引用完整性"],
            "direct": ["检查知识库状态", "生成受限回答"],
        }
        document_ids = state.get("document_ids")
        scope_mode = "explicit" if document_ids is not None else "all_documents"
        return {
            "route": route,
            "plan": plans[route],
            "document_ids": document_ids,
            "scope_mode": scope_mode,
            "events": event(state, "plan", f"route={route}; scope={scope_mode}", started),
        }

    def rewrite_node(state: AgentState) -> dict[str, Any]:
        started = time.perf_counter()
        emit_progress("rewrite", "正在结合本会话上下文改写检索问题…")
        prior_questions = [item["content"] for item in db.get_messages(state["session_id"], limit=12) if item["role"] == "user"]
        rewrite = (
            llm.rewrite_query(state["query"], prior_questions)
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
        if state.get("retry_count", 0) and state.get("verify_reason") == "no_evidence":
            prior_questions = [item["content"] for item in db.get_messages(state["session_id"], limit=12) if item["role"] == "user"]
            retry_rewrite = (
                llm.rewrite_query(query, prior_questions, failure_reason="no_evidence")
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

    def tool_node(state: AgentState) -> dict[str, Any]:
        started = time.perf_counter()
        emit_progress("tool", "正在调用安全计算工具…")
        try:
            result = calculate(state["query"])
            errors = state.get("errors", [])
        except Exception as exc:  # surfaced to the graph instead of crashing the request
            result = ""
            errors = [*state.get("errors", []), str(exc)]
        return {"tool_result": result, "errors": errors, "events": event(state, "tool", result or "tool_error", started)}

    def answer_node(state: AgentState) -> dict[str, Any]:
        started = time.perf_counter()
        emit_progress("answer", "正在生成带引用的回答…")
        # A source-scoped request must not inherit factual claims from an
        # earlier answer produced over a different document set.
        memory = [] if state.get("document_ids") is not None else db.get_messages(state["session_id"], limit=6)
        contexts = state.get("retrieved", [])
        try:
            answer = llm.generate(
                state["query"], contexts, memory, state.get("tool_result", ""), state.get("thinking_mode"),
                citation_retry=state.get("retry_count", 0) > 0 and state.get("verify_reason") in {"citation_missing", "citation_out_of_range"},
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
        # The prompt numbers every retrieved context. Return the same complete
        # list to clients so a model citation such as [4] never points outside
        # the displayed citation inventory.
        citations = contexts
        return {
            "answer": answer,
            "citations": citations,
            "errors": errors,
            "thinking_mode": state.get("thinking_mode", "disabled"),
            "events": event(state, "answer", f"citations={len(citations)}", started),
        }

    def verify_node(state: AgentState) -> dict[str, Any]:
        started = time.perf_counter()
        emit_progress("verify", "正在核验回答引用与证据范围…")
        route = state["route"]
        if route == "rag":
            citations = state.get("citations", [])
            indices = [int(index) for index in re.findall(r"\[(\d+)\]", state.get("answer", ""))]
            if not citations:
                verified, verify_reason = False, "no_evidence"
            elif not indices:
                verified, verify_reason = False, "citation_missing"
            elif any(index < 1 or index > len(citations) for index in indices):
                verified, verify_reason = False, "citation_out_of_range"
            else:
                verified, verify_reason = True, "citation_indices_valid"
        elif route == "tool":
            verified, verify_reason = bool(state.get("tool_result")), "tool_result_present"
        else:
            verified, verify_reason = bool(state.get("answer")), "direct_answer_present"
        retry_count = state.get("retry_count", 0)
        if not verified and route == "rag":
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

    def after_plan(state: AgentState) -> str:
        return {"rag": "rewrite", "tool": "tool", "direct": "answer"}[state["route"]]

    def after_verify(state: AgentState) -> str:
        if state["route"] == "rag" and not state["verified"] and state.get("retry_count", 0) == 1:
            return "retrieve" if state.get("verify_reason") == "no_evidence" else "answer"
        return "persist"

    graph = StateGraph(AgentState)
    graph.add_node("plan", plan_node)
    graph.add_node("rewrite", rewrite_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("tool", tool_node)
    graph.add_node("answer", answer_node)
    graph.add_node("verify", verify_node)
    graph.add_node("persist", persist_node)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", after_plan, {"rewrite": "rewrite", "tool": "tool", "answer": "answer"})
    graph.add_edge("rewrite", "retrieve")
    graph.add_edge("retrieve", "answer")
    graph.add_edge("tool", "answer")
    graph.add_edge("answer", "verify")
    graph.add_conditional_edges("verify", after_verify, {"retrieve": "retrieve", "answer": "answer", "persist": "persist"})
    graph.add_edge("persist", END)
    return graph.compile()


def initial_state(
    session_id: str,
    query: str,
    document_ids: list[str] | None = None,
    thinking_mode: str = "disabled",
) -> AgentState:
    return {
        "run_id": f"run_{uuid4().hex[:12]}",
        "session_id": session_id,
        "query": query,
        "retrieval_query": query,
        "rewrite_reason": "not_started",
        "verify_reason": "not_started",
        "thinking_mode": thinking_mode,
        "document_ids": document_ids,
        "retry_count": 0,
        "started_at": time.perf_counter(),
        "events": [],
        "errors": [],
    }
