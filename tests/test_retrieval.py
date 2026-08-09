from collections import Counter

from app.config import Settings
from app.db import Database
from app.ingestion import TextBlock
from app.retrieval import HashEmbedding, HybridRetriever, bm25_scores, chunk_text
from app.service import ResearchFlowService


def make_service(tmp_path):
    return ResearchFlowService(Settings(db_path=str(tmp_path / "test.db")))


def test_hybrid_retrieval_returns_relevant_chunk(tmp_path):
    service = make_service(tmp_path)
    service.ingest(
        "KV Cache 量化笔记",
        "unit-test",
        "MAC-KV 使用自适应码本和 K/V 混合精度分配。该方法在低比特场景压缩 KV Cache 显存。",
    )
    for strategy in ("lexical", "dense", "hybrid"):
        results = service.retriever.search("KV Cache 混合精度量化", top_k=2, strategy=strategy)
        assert results
        assert "混合精度" in results[0].content


def test_document_chunking_creates_multiple_chunks(tmp_path):
    service = make_service(tmp_path)
    document_id, count = service.ingest("long", "unit-test", ("第一段实验结论。" * 100) + "\n\n" + ("第二段方法设计。" * 100))
    assert document_id.startswith("doc_")
    assert count >= 2


def test_chunking_prefers_sentence_boundaries_before_character_windows():
    text = "First evidence sentence. " * 28 + "Critical metric is 20.7 points. " + "Closing sentence. " * 28
    chunks = chunk_text(text, max_chars=180, overlap=20)

    assert len(chunks) > 1
    assert any("Critical metric is 20.7 points." in chunk for chunk in chunks)
    assert all(not chunk.startswith("vidence sentence") for chunk in chunks)


def test_chunking_merges_short_section_preamble_into_following_evidence():
    text = "Meta Review:\n\n" + ("The evidence explains the concern in detail. " * 30)
    chunks = chunk_text(text, max_chars=180, overlap=20)

    assert len(chunks) > 1
    assert chunks[0].startswith("Meta Review:")
    assert "evidence explains" in chunks[0]


def test_markdown_section_context_keeps_reviewer_identity_across_chunks(tmp_path):
    service = make_service(tmp_path)
    reviewer_section = "Official Review of Submission13481 by Reviewer jueW"
    blocks = [
        TextBlock(
            "Summary: MAC-KV addresses KV cache memory bottlenecks.\n\n"
            "Strengths: The reviewer credits the symmetric codebook, heterogeneous outlier protection, "
            "and fused CUDA kernel for a strong engineering effort.",
            section=reviewer_section,
        )
    ]
    service.retriever.ingest(
        "OpenReview record",
        "unit-test",
        "\n\n".join(block.content for block in blocks),
        blocks=blocks,
    )

    results = service.retriever.search("Reviewer jueW Strengths", top_k=4)

    assert results
    assert any("Strengths:" in result.content for result in results)
    assert all(reviewer_section in result.content for result in results)


def test_reranker_reorders_only_hybrid_candidates(tmp_path):
    class PreferSecondPassage:
        def score(self, query, passages):
            return [1.0 if "beta" in passage else 0.0 for passage in passages]

    db = Database(str(tmp_path / "rerank.db"))
    retriever = HybridRetriever(db, HashEmbedding(), PreferSecondPassage(), reranker_candidates=2)
    retriever.ingest("first", "test", "shared retrieval wording alpha")
    retriever.ingest("second", "test", "shared retrieval wording beta")

    hybrid = retriever.search("shared retrieval wording", top_k=2, strategy="hybrid")
    lexical = retriever.search("shared retrieval wording", top_k=2, strategy="lexical")

    assert hybrid[0].title == "second"
    assert {item.title for item in lexical} == {"first", "second"}


def test_hybrid_preserves_candidates_from_lexical_and_dense_channels(tmp_path):
    class OpposingDenseEmbedding:
        def embed_documents(self, texts):
            return [[float(index), 1.0] for index, _ in enumerate(texts)]

        def embed_query(self, text):
            return [1.0, 1.0]

    db = Database(str(tmp_path / "coverage.db"))
    retriever = HybridRetriever(db, OpposingDenseEmbedding())
    retriever.ingest("lexical-best", "test", "rare exact anchor phrase")
    retriever.ingest("dense-best", "test", "unrelated semantic passage")

    results = retriever.search("rare exact anchor phrase", top_k=2, strategy="hybrid")

    assert {item.title for item in results} == {"lexical-best", "dense-best"}


def test_fielded_bm25_boosts_explicit_section_metadata(tmp_path):
    service = make_service(tmp_path)
    service.retriever.ingest(
        "original-review", "test", "generic review text", blocks=[TextBlock("generic review text", section="Meta Review by Area Chair")]
    )
    service.ingest("author-response", "test", "The author repeatedly discusses the review and response.")

    results = service.retriever.search("Meta Review Area Chair", top_k=1, strategy="lexical")

    assert results[0].title == "original-review"


def test_bm25_scores_handles_empty_metadata_field():
    assert bm25_scores(Counter({"query": 1}), [[], []]) == [0.0, 0.0]


def test_fielded_bm25_treats_chunks_from_one_section_as_one_metadata_candidate(tmp_path):
    service = make_service(tmp_path)
    blocks = [TextBlock("evidence one.\n\nevidence two.", section="Meta Review by Area Chair")]
    service.retriever.ingest("original-review", "test", blocks[0].content, blocks=blocks)
    service.ingest("author-response", "test", "The author responds to the review and rebuttal.")

    results = service.retriever.search("Meta Review Area Chair", top_k=1, strategy="hybrid")

    assert results[0].title == "original-review"


def test_distinctive_section_match_scopes_hybrid_evidence_to_that_section(tmp_path):
    service = make_service(tmp_path)
    original_blocks = [TextBlock(("area chair concern. " * 120), section="Meta Review by Area Chair")]
    service.retriever.ingest("original-review", "test", original_blocks[0].content, blocks=original_blocks)
    service.ingest("author-response", "test", "The response talks about the review and rebuttal repeatedly. " * 40)

    results = service.retriever.search("Meta Review Area Chair", top_k=3, strategy="hybrid")

    assert results
    assert all(item.title == "original-review" for item in results)


def test_explicit_document_scope_excludes_unselected_sources(tmp_path):
    service = make_service(tmp_path)
    original_id, _ = service.ingest(
        "original-review",
        "test",
        "Area Chair 的原始审稿意见要求澄清理论主张和实验效率。",
    )
    service.ingest(
        "final-rebuttal",
        "test",
        "作者在 rebuttal 中回应了 Area Chair 的理论主张与实验效率意见。",
    )

    results = service.retriever.search(
        "Area Chair 对理论和效率提出什么意见？",
        top_k=4,
        document_ids=[original_id],
    )

    assert results
    assert all(result.document_id == original_id for result in results)
    assert all(result.title == "original-review" for result in results)


def test_auto_document_scope_is_used_when_a_planner_selects_source(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    original_id, _ = service.ingest("original-review", "test", "原始审稿意见质疑理论假设。")
    service.ingest("final-rebuttal", "test", "作者回复解释了理论假设。")
    monkeypatch.setattr(
        service.llm,
        "requests_document_scope",
        lambda query, catalog: True,
    )
    monkeypatch.setattr(service.retriever, "select_document_scope", lambda query, catalog: [original_id])

    result = service.chat("只总结原始审稿意见中的理论质疑。")

    assert result["scope_mode"] == "auto_metadata_rerank"
    assert result["citations"]
    assert all(item["document_id"] == original_id for item in result["citations"])
