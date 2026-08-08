from __future__ import annotations

import re
import time
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from app.db import Database
from app.llm import LLMClient, LLMConnectionError
from app.retrieval import HybridRetriever
from app.tools import calculate


class AgentState(TypedDict, total=False):
    run_id: str
    session_id: str
    query: str
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


def event(state: AgentState, node: str, detail: str) -> list[dict[str, Any]]:
    return [*state.get("events", []), {"node": node, "detail": detail, "at_ms": round((time.perf_counter() - state["started_at"]) * 1000, 2)}]


def build_graph(db: Database, retriever: HybridRetriever, llm: LLMClient, retrieval_top_k: int = 6):
    def plan_node(state: AgentState) -> dict[str, Any]:
        query = state["query"].strip()
        has_math = bool(re.search(r"\d\s*[-+*/%]\s*\d", query))
        route = "tool" if has_math else ("rag" if db.chunk_count() else "direct")
        plans = {
            "tool": ["识别计算表达式", "执行安全计算工具", "校验并返回结果"],
            "rag": ["检索科研文档", "基于证据生成答案", "校验引用完整性"],
            "direct": ["检查知识库状态", "生成受限回答"],
        }
        return {"route": route, "plan": plans[route], "events": event(state, "plan", f"route={route}")}

    def retrieve_node(state: AgentState) -> dict[str, Any]:
        query = state["query"]
        if state.get("retry_count", 0):
            query = f"{query} 方法 结果 结论"
        results = [item.as_dict() for item in retriever.search(query, top_k=retrieval_top_k)]
        return {"retrieved": results, "events": event(state, "retrieve", f"hits={len(results)}")}

    def tool_node(state: AgentState) -> dict[str, Any]:
        try:
            result = calculate(state["query"])
            errors = state.get("errors", [])
        except Exception as exc:  # surfaced to the graph instead of crashing the request
            result = ""
            errors = [*state.get("errors", []), str(exc)]
        return {"tool_result": result, "errors": errors, "events": event(state, "tool", result or "tool_error")}

    def answer_node(state: AgentState) -> dict[str, Any]:
        memory = db.get_messages(state["session_id"], limit=6)
        contexts = state.get("retrieved", [])
        try:
            answer = llm.generate(state["query"], contexts, memory, state.get("tool_result", ""))
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
            "events": event(state, "answer", f"citations={len(citations)}"),
        }

    def verify_node(state: AgentState) -> dict[str, Any]:
        route = state["route"]
        if route == "rag":
            verified = bool(state.get("citations")) and any(
                f"[{index}]" in state.get("answer", "")
                for index in range(1, len(state.get("citations", [])) + 1)
            )
        elif route == "tool":
            verified = bool(state.get("tool_result"))
        else:
            verified = bool(state.get("answer"))
        retry_count = state.get("retry_count", 0)
        if not verified and route == "rag":
            retry_count += 1
        return {
            "verified": verified,
            "retry_count": retry_count,
            "events": event(state, "verify", f"verified={verified}; retries={retry_count}"),
        }

    def persist_node(state: AgentState) -> dict[str, Any]:
        latency_ms = round((time.perf_counter() - state["started_at"]) * 1000, 2)
        db.add_message(state["session_id"], "user", state["query"])
        db.add_message(state["session_id"], "assistant", state["answer"])
        events = event(state, "persist", f"latency_ms={latency_ms}")
        db.save_run(
            {
                "run_id": state["run_id"],
                "session_id": state["session_id"],
                "query": state["query"],
                "route": state["route"],
                "answer": state["answer"],
                "verified": state["verified"],
                "latency_ms": latency_ms,
                "events": events,
                "errors": state.get("errors", []),
            }
        )
        return {"latency_ms": latency_ms, "events": events}

    def after_plan(state: AgentState) -> str:
        return {"rag": "retrieve", "tool": "tool", "direct": "answer"}[state["route"]]

    def after_verify(state: AgentState) -> str:
        if state["route"] == "rag" and not state["verified"] and state.get("retry_count", 0) == 1:
            return "retrieve"
        return "persist"

    graph = StateGraph(AgentState)
    graph.add_node("plan", plan_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("tool", tool_node)
    graph.add_node("answer", answer_node)
    graph.add_node("verify", verify_node)
    graph.add_node("persist", persist_node)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", after_plan, {"retrieve": "retrieve", "tool": "tool", "answer": "answer"})
    graph.add_edge("retrieve", "answer")
    graph.add_edge("tool", "answer")
    graph.add_edge("answer", "verify")
    graph.add_conditional_edges("verify", after_verify, {"retrieve": "retrieve", "persist": "persist"})
    graph.add_edge("persist", END)
    return graph.compile()


def initial_state(session_id: str, query: str) -> AgentState:
    return {
        "run_id": f"run_{uuid4().hex[:12]}",
        "session_id": session_id,
        "query": query,
        "retry_count": 0,
        "started_at": time.perf_counter(),
        "events": [],
        "errors": [],
    }
