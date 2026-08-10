from app.config import Settings
from app.db import Database
from app.graph import build_graph, initial_state, strip_evidence_status_marker
from app.llm import LLMConnectionError
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
        def __init__(self):
            self.failure_reasons = []

        def generate(self, query, contexts, memory, tool_result="", thinking_mode=None, citation_retry=False, citation_failure_reason=""):
            self.failure_reasons.append(citation_failure_reason)
            return "模型没有按照要求输出引用。"

    db = Database(str(tmp_path / "retry.db"))
    retriever = HybridRetriever(db, HashEmbedding())
    retriever.ingest("重试测试", "unit-test", "Agent 应在引用缺失时扩展查询并最多重试一次。")
    llm = NoCitationLLM()
    graph = build_graph(db, retriever, llm)
    result = graph.invoke(initial_state("session_retry", "何时执行重试？"), {"recursion_limit": 12})
    retrieve_events = [item for item in result["events"] if item["node"] == "retrieve"]
    answer_events = [item for item in result["events"] if item["node"] == "answer"]
    # A bad citation is repaired from the same evidence; it does not trigger
    # an unnecessary second retrieval.
    assert len(retrieve_events) == 1
    assert len(answer_events) == 2
    assert result["retry_count"] == 2
    assert result["verified"] is False
    assert llm.failure_reasons == ["", "citation_missing"]


def test_follow_up_uses_rewritten_retrieval_query_and_persists_trace(tmp_path):
    class FollowUpLLM:
        def rewrite_query(self, query, history, failure_reason=""):
            if query == "它在 GSM8K 上表现如何？":
                assert history == [
                    {
                        "query": "介绍 HoloQuant 的量化方法",
                        "answer": "HoloQuant 的 GSM8K 结果来自实验表。[1]",
                    }
                ]
                return {
                    "retrieval_query": "HoloQuant 在 GSM8K 上表现如何？",
                    "rewritten": True,
                    "reason": "resolved_explicit_prior_entity",
                }
            return {"retrieval_query": query, "rewritten": False, "reason": "already_standalone"}

        def generate(self, query, contexts, memory, tool_result="", thinking_mode=None, citation_retry=False, citation_failure_reason=""):
            return "HoloQuant 的 GSM8K 结果来自实验表。[1]"

    db = Database(str(tmp_path / "rewrite.db"))
    retriever = HybridRetriever(db, HashEmbedding())
    retriever.ingest("HoloQuant 实验", "unit-test", "HoloQuant 在 GSM8K 上的实验结果记录在表 2 中。")
    graph = build_graph(db, retriever, FollowUpLLM())

    graph.invoke(initial_state("rewrite_session", "介绍 HoloQuant 的量化方法"), {"recursion_limit": 12})
    result = graph.invoke(initial_state("rewrite_session", "它在 GSM8K 上表现如何？"), {"recursion_limit": 12})
    persisted = db.get_run(result["run_id"])

    assert result["retrieval_query"] == "HoloQuant 在 GSM8K 上表现如何？"
    assert result["rewrite_reason"] == "resolved_explicit_prior_entity"
    assert result["verify_reason"] == "citation_indices_valid"
    assert persisted is not None
    assert persisted["retrieval_query"] == result["retrieval_query"]
    assert persisted["rewrite_reason"] == result["rewrite_reason"]
    assert persisted["verify_reason"] == result["verify_reason"]
    assert any(item["node"] == "rewrite" for item in result["events"])
    assert all("duration_ms" in item for item in result["events"])


def test_invalid_citation_reanswers_against_same_evidence_once(tmp_path):
    class RepairingLLM:
        def __init__(self):
            self.citation_retry_flags = []
            self.citation_failure_reasons = []

        def generate(self, query, contexts, memory, tool_result="", thinking_mode=None, citation_retry=False, citation_failure_reason=""):
            self.citation_retry_flags.append(citation_retry)
            self.citation_failure_reasons.append(citation_failure_reason)
            return "有证据支持该结论。[1]" if citation_retry else "有证据支持该结论。[99]"

    db = Database(str(tmp_path / "citation-repair.db"))
    retriever = HybridRetriever(db, HashEmbedding())
    retriever.ingest("证据", "unit-test", "该文档提供了可引用的事实证据。")
    llm = RepairingLLM()
    result = build_graph(db, retriever, llm).invoke(initial_state("citation_session", "有哪些证据？"), {"recursion_limit": 12})

    assert result["verified"] is True
    assert result["verify_reason"] == "citation_indices_valid"
    assert result["retry_count"] == 1
    assert llm.citation_retry_flags == [False, True]
    assert llm.citation_failure_reasons == ["", "citation_out_of_range"]
    assert len([item for item in result["events"] if item["node"] == "retrieve"]) == 1
    assert len([item for item in result["events"] if item["node"] == "answer"]) == 2


def test_no_evidence_rewrites_and_retrieves_once_before_stopping(tmp_path):
    class NoEvidenceLLM:
        def __init__(self):
            self.rewrite_failures = []
            self.rewrite_histories = []

        def rewrite_query(self, query, history, failure_reason=""):
            self.rewrite_failures.append(failure_reason)
            self.rewrite_histories.append(history)
            return {
                "retrieval_query": query + (" evidence" if failure_reason else ""),
                "rewritten": bool(failure_reason),
                "reason": "retry_expand_search_wording" if failure_reason else "already_standalone",
            }

        def generate(self, query, contexts, memory, tool_result="", thinking_mode=None, citation_retry=False, citation_failure_reason=""):
            return "没有可引用的证据。"

    db = Database(str(tmp_path / "no-evidence.db"))
    retriever = HybridRetriever(db, HashEmbedding())
    retriever.ingest("隐藏证据", "unit-test", "该块不在本轮显式检索范围内。")
    llm = NoEvidenceLLM()
    # An explicitly empty evidence boundary produces a real RAG route with no
    # candidates, rather than using the unrelated empty-corpus direct route.
    result = build_graph(db, retriever, llm).invoke(
        initial_state("no_evidence_session", "有哪些证据？", document_ids=[]), {"recursion_limit": 12}
    )

    assert result["verified"] is False
    assert result["verify_reason"] == "no_evidence"
    assert result["retry_count"] == 2
    assert llm.rewrite_failures == ["", "no_evidence"]
    assert llm.rewrite_histories == [[], []]
    assert result["retrieval_query"].endswith(" evidence")
    assert len([item for item in result["events"] if item["node"] == "retrieve"]) == 2


def test_not_relevant_evidence_status_rewrites_and_retrieves_once(tmp_path):
    class RelevanceAwareLLM:
        def __init__(self):
            self.rewrite_failures = []
            self.answers = 0

        def rewrite_query(self, query, history, failure_reason=""):
            self.rewrite_failures.append(failure_reason)
            return {
                "retrieval_query": f"{query} alternative terminology" if failure_reason else query,
                "rewritten": bool(failure_reason),
                "reason": "expand_after_irrelevant_candidates" if failure_reason else "already_standalone",
            }

        def generate(self, query, contexts, memory, tool_result="", thinking_mode=None, citation_retry=False, citation_failure_reason=""):
            self.answers += 1
            if self.answers == 1:
                return "The retrieved passages do not answer the question. [1] <!-- evidence_status: not_relevant -->"
            return "The retried evidence supports this answer. [1] <!-- evidence_status: grounded -->"

    db = Database(str(tmp_path / "not-relevant.db"))
    retriever = HybridRetriever(db, HashEmbedding())
    retriever.ingest("Candidate", "unit-test", "A chunk is returned for the question but may not answer it.")
    llm = RelevanceAwareLLM()
    result = build_graph(db, retriever, llm).invoke(initial_state("not_relevant_session", "What is the target finding?"), {"recursion_limit": 12})

    assert result["verified"] is True
    assert result["verify_reason"] == "citation_indices_valid"
    assert result["evidence_status"] == "grounded"
    assert result["retry_count"] == 1
    assert llm.rewrite_failures == ["", "evidence_not_relevant"]
    assert len([item for item in result["events"] if item["node"] == "retrieve"]) == 2
    assert "evidence_status" not in result["answer"]


def test_citation_validation_precedes_not_relevant_status(tmp_path):
    class CitationFirstLLM:
        def __init__(self):
            self.calls = 0
            self.failure_reasons = []

        def generate(self, query, contexts, memory, tool_result="", thinking_mode=None, citation_retry=False, citation_failure_reason=""):
            self.calls += 1
            self.failure_reasons.append(citation_failure_reason)
            if self.calls == 1:
                return "No material answer is available. <!-- evidence_status: not_relevant -->"
            return "The existing evidence supports this answer. [1] <!-- evidence_status: grounded -->"

    db = Database(str(tmp_path / "citation-first.db"))
    retriever = HybridRetriever(db, HashEmbedding())
    retriever.ingest("Evidence", "unit-test", "This evidence chunk is available to both generation attempts.")
    llm = CitationFirstLLM()
    result = build_graph(db, retriever, llm).invoke(initial_state("citation_first", "What is supported?"), {"recursion_limit": 12})

    assert result["verified"] is True
    assert result["retry_count"] == 1
    assert llm.failure_reasons == ["", "citation_missing"]
    assert len([item for item in result["events"] if item["node"] == "retrieve"]) == 1


def test_evidence_status_marker_is_optional_and_removed_from_visible_answer():
    answer, status = strip_evidence_status_marker("Answer [1] <!-- evidence_status: grounded -->")
    assert answer == "Answer [1]"
    assert status == "grounded"
    unchanged, missing = strip_evidence_status_marker("Answer without the optional protocol marker.")
    assert unchanged == "Answer without the optional protocol marker."
    assert missing == "not_reported"


def test_unverified_turn_is_excluded_from_follow_up_rewrite_context(tmp_path):
    class UnverifiedHistoryLLM:
        def __init__(self):
            self.histories = []

        def rewrite_query(self, query, history, failure_reason=""):
            if not failure_reason:
                self.histories.append(history)
            return {"retrieval_query": query, "rewritten": False, "reason": "test"}

        def generate(self, query, contexts, memory, tool_result="", thinking_mode=None, citation_retry=False, citation_failure_reason=""):
            # Deliberately fails citation verification, so this answer must not
            # become trusted context for the next user question.
            return "This draft intentionally has no citation marker."

    db = Database(str(tmp_path / "unverified-history.db"))
    retriever = HybridRetriever(db, HashEmbedding())
    retriever.ingest("Evidence", "unit-test", "The corpus has a citable fact for both test questions.")
    llm = UnverifiedHistoryLLM()
    graph = build_graph(db, retriever, llm)

    first = graph.invoke(initial_state("unverified_history", "Explain the first topic."), {"recursion_limit": 12})
    second = graph.invoke(initial_state("unverified_history", "What about it?"), {"recursion_limit": 12})

    assert first["verified"] is False
    assert second["verified"] is False
    assert llm.histories == [[], []]


def test_model_failure_is_sanitized_and_persisted_in_run_trace(tmp_path):
    class FailingLLM:
        def generate(self, query, contexts, memory, tool_result="", thinking_mode=None, citation_retry=False, citation_failure_reason=""):
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


def test_empty_model_response_is_sanitized_instead_of_rendered_as_blank(tmp_path):
    class EmptyLLM:
        def generate(self, query, contexts, memory, tool_result="", thinking_mode=None, citation_retry=False, citation_failure_reason=""):
            raise RuntimeError("LLM returned an empty final response")

    db = Database(str(tmp_path / "empty-response.db"))
    retriever = HybridRetriever(db, HashEmbedding())
    retriever.ingest("evidence", "unit-test", "The corpus contains a factual evidence chunk.")
    graph = build_graph(db, retriever, EmptyLLM())

    result = graph.invoke(initial_state("session_empty", "What evidence exists?"), {"recursion_limit": 12})

    assert result["answer"].strip()
    assert result["errors"] == ["llm_error: RuntimeError"]


def test_model_connection_error_is_explained_to_the_user(tmp_path):
    class UnreachableLLM:
        def generate(self, query, contexts, memory, tool_result="", thinking_mode=None, citation_retry=False, citation_failure_reason=""):
            raise LLMConnectionError("connection failed")

    db = Database(str(tmp_path / "connection-error.db"))
    retriever = HybridRetriever(db, HashEmbedding())
    retriever.ingest("evidence", "unit-test", "The corpus contains a factual evidence chunk.")
    graph = build_graph(db, retriever, UnreachableLLM())

    result = graph.invoke(initial_state("session_connection", "What evidence exists?"), {"recursion_limit": 12})

    assert "网络连接" in result["answer"]
    assert result["errors"] == ["llm_error: LLMConnectionError"]
