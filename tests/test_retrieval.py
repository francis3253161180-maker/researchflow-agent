from app.config import Settings
from app.ingestion import TextBlock
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


def test_markdown_section_context_keeps_reviewer_identity_across_chunks(tmp_path):
    service = make_service(tmp_path)
    reviewer_section = "Official Review of Submission13481 by Reviewer jueW"
    blocks = [
        TextBlock(
            "Summary: MAC-KV addresses KV cache memory bottlenecks.\n\n"
            "Strengths: The reviewer credits the symmetric codebook, heterogeneous outlier protection, "
            "and fused CUDA kernel for a strong engineering effort.",
            section=f"{reviewer_section} · Strengths",
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
    assert results[0].section == f"{reviewer_section} · Strengths"
