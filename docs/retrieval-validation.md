# Retrieval validation and operating boundary

## What the production path does

ResearchFlow uses a generic three-stage design:

1. **First-stage hybrid retrieval:** BM25-style lexical scores and multilingual dense vectors are fused with RRF.
2. **Optional second-stage reranking:** `BAAI/bge-reranker-v2-m3` scores only the bounded Top-N candidates as `(query, passage)` pairs.
3. **Grounded generation:** the Agent receives a configurable Top-K evidence set (default `RETRIEVAL_TOP_K=6`) and must cite it.

The implementation does not contain paper titles, reviewer names, OpenReview field names, question-answer mappings, or language-specific keyword maps. Markdown headings, document titles and filenames are treated as ordinary metadata fields for every supported format; a uniquely matched metadata field can constrain a navigational query to the corresponding document section.

## Why Top-K is six by default

One passage is rarely sufficient for a multi-part question such as a review summary, experiment comparison, or design trade-off. Four passages were too easy to under-cover. Six is still a bounded context, while leaving room for the model to synthesize several independently retrieved facts. Configure `RETRIEVAL_TOP_K` from 1 to 8 if a different latency/context budget is required.

## Reranker result on this Windows CPU

The BGE reranker has been downloaded and successfully loaded from `D:\ResearchFlow-runtime\models`. A fresh-index diagnostic on the Area Chair question returned only Meta Review evidence in both modes. BGE raised the detailed "strongest baselines" and "weaknesses" passages above unrelated material, but a Top-8 candidate rerank took approximately **20.7 seconds** versus **114 ms** for first-stage retrieval on the same corpus and hardware.

The full 16-query CPU comparison exceeded a five-minute execution budget. Therefore BGE is intentionally **implemented but disabled by default** (`RERANKER_PROVIDER=none`). It is a useful optional precision mode for small, deliberate investigations; it is not an appropriate default for the local interactive web UI on this CPU.

## Reproducible checks

```powershell
# Fast regression suite
D:\ResearchFlow-runtime\Scripts\python.exe -m pytest -q

# First-stage multilingual retrieval evaluation over local portfolio files
D:\ResearchFlow-runtime\Scripts\python.exe scripts\run_portfolio_multilingual_eval.py --corpus-root .. --embedding-provider fastembed --reranker-provider none

# Optional BGE experiment (expect much higher CPU latency)
$env:RERANKER_CANDIDATES='8'
D:\ResearchFlow-runtime\Scripts\python.exe scripts\run_portfolio_multilingual_eval.py --corpus-root .. --embedding-provider fastembed --reranker-provider bge --top-k 4
```

These are regression signals, not claims of enterprise-wide retrieval or answer accuracy. Changing an embedding model or the chunking implementation requires deleting and re-importing existing documents because stored vectors and chunks are not retroactively rebuilt.

## Repository documentation regression corpus

`tests/test_repository_docs_retrieval.py` indexes every versioned Markdown file under `docs/` with the deterministic offline test embedding. It asserts that the parser accepts the whole documentation corpus and that hybrid retrieval finds evidence in the MCP, FastAPI, LangGraph, SQLite, and retrieval-validation guides. This test runs as part of the ordinary `pytest` suite, so documentation retrieval regressions are caught without needing local private files.

The separate `scripts/run_eval.py` evaluation parses four locally supplied public research-paper PDFs and reports document-level Recall@K/MRR. It is intentionally a local manual evaluation because the PDFs themselves are not committed to the repository.

For the complete layered protocol, including the four review/rebuttal records and an external BEIR SciFact subset, see [Evaluation suite](evaluation-suite.md).
