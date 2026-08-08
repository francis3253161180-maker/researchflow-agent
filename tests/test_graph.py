from app.config import Settings
from app.db import Database
from app.graph import build_graph, initial_state
from app.retrieval import HashEmbedding, HybridRetriever
from app.service import ResearchFlowService


def test_rag_route_returns_citations_and_persists_memory(tmp_path):
    service = ResearchFlowService(Settings(db_path=str(tmp_path / "graph.db")))
    service.ingest(
        "Holo 论文笔记",
        "unit-test",
        "Holo 提出低秩谱域乘性调制，并将额外激活显存复杂度降至 O(1)。",
    )
    result = service.chat("Holo 的核心方法是什么？", "session_test")
    assert result["route"] == "rag"
    assert result["verified"] is True
    assert result["citations"]
    assert len(result["citations"]) == len(result["retrieved"])
    assert len(service.db.get_messages("session_test")) == 2
    assert service.db.get_run(result["run_id"]) is not None


def test_calculator_tool_route(tmp_path):
    service = ResearchFlowService(Settings(db_path=str(tmp_path / "tool.db")))
    result = service.chat("请计算 (12 + 8) * 3", "session_tool")
    assert result["route"] == "tool"
    assert result["verified"] is True
    assert "60" in result["answer"]


def test_missing_citations_retry_exactly_once(tmp_path):
    class NoCitationLLM:
        def generate(self, query, contexts, memory, tool_result=""):
            return "模型没有按照要求输出引用。"

    db = Database(str(tmp_path / "retry.db"))
    retriever = HybridRetriever(db, HashEmbedding())
    retriever.ingest("重试测试", "unit-test", "Agent 应在引用缺失时扩展查询并最多重试一次。")
    graph = build_graph(db, retriever, NoCitationLLM())
    result = graph.invoke(initial_state("session_retry", "何时执行重试？"), {"recursion_limit": 12})
    retrieve_events = [item for item in result["events"] if item["node"] == "retrieve"]
    assert len(retrieve_events) == 2
    assert result["retry_count"] == 2
    assert result["verified"] is False


def test_model_failure_is_sanitized_and_persisted_in_run_trace(tmp_path):
    class FailingLLM:
        def generate(self, query, contexts, memory, tool_result=""):
            raise RuntimeError("provider returned internal request details")

    db = Database(str(tmp_path / "failure.db"))
    retriever = HybridRetriever(db, HashEmbedding())
    retriever.ingest("failure", "unit-test", "The test corpus gives the RAG route an evidence chunk.")
    graph = build_graph(db, retriever, FailingLLM())

    result = graph.invoke(initial_state("session_failure", "What evidence exists?"), {"recursion_limit": 12})
    persisted = db.get_run(result["run_id"])

    assert result["errors"] == ["llm_error: RuntimeError"]
    assert persisted is not None
    assert persisted["errors"] == ["llm_error: RuntimeError"]
