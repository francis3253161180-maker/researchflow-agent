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
                llm.rewrite_query(query, history, failure_reason=str(retry_reason))
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
        memory = [] if state.get("document_ids") is not None else history_as_messages(trusted_short_term_history(db, state["session_id"]))
        contexts = state.get("retrieved", [])
        try:
            answer = llm.generate(
                state["query"], contexts, memory, state.get("tool_result", ""), state.get("thinking_mode"),
                citation_retry=state.get("retry_count", 0) > 0 and state.get("verify_reason") in {"citation_missing", "citation_out_of_range"},
                citation_failure_reason=state.get("verify_reason", "") if state.get("retry_count", 0) > 0 else "",
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
        if route == "rag":
            citations = state.get("citations", [])
            indices = [int(index) for index in re.findall(r"\[(\d+)\]", state.get("answer", ""))]
            if not citations:
                verified, verify_reason = False, "no_evidence"
            elif not indices:
                verified, verify_reason = False, "citation_missing"
            elif any(index < 1 or index > len(citations) for index in indices):
                verified, verify_reason = False, "citation_out_of_range"
            elif state.get("evidence_status") == "not_relevant":
                # A relevance verdict is useful only after the answer has met
                # basic citation structure.  Otherwise a missing/invalid
                # citation is a generation repair, not a recall repair.
                verified, verify_reason = False, "evidence_not_relevant"
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
            return "retrieve" if state.get("verify_reason") in {"no_evidence", "evidence_not_relevant"} else "answer"
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
        "evidence_status": "not_reported",
        "thinking_mode": thinking_mode,
        "document_ids": document_ids,
        "retry_count": 0,
        "started_at": time.perf_counter(),
        "events": [],
        "errors": [],
    }
