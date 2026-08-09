# Evaluation suite: what is measured, what is not

ResearchFlow now uses five deliberately separate evaluation layers. They answer different questions and must not be combined into a single "accuracy" number.

| Layer | Corpus | What it tests | What it does not prove |
| --- | --- | --- | --- |
| Repository regression | Versioned `docs/` Markdown | Parsing, chunking and hybrid retrieval over project documentation | Real-world RAG quality |
| Local review/rebuttal QA | MAC-KV and Holo OpenReview/rebuttal Markdown | Grounded answer coverage, citation syntax, abstention and source-scope compliance | General enterprise-document accuracy |
| Natural role retrieval | The same four records, with source-neutral Chinese questions | Whether relevant reviewer/author passages are ranked before source-mixing passages | Answer faithfulness or reliable document-level filtering |
| BEIR SciFact subset | External claims, abstracts and official qrels | General document retrieval with judged relevance | Full BEIR leaderboard performance or answer faithfulness |
| QASPER full-text evidence retrieval | Public full scientific papers, original questions and human evidence | Long-document chunking and passage-level evidence recall | Cross-document source routing, PDF layout parsing or answer generation |

## 1. Versioned documentation regression

`tests/test_repository_docs_retrieval.py` parses every versioned Markdown file under `docs/`, then checks that hybrid retrieval finds the expected MCP, FastAPI, LangGraph, SQLite and retrieval-validation guide. It requires no personal files, API key, or model download, and runs with ordinary `pytest`.

## 2. Four-document review/rebuttal QA evaluation

`evals/review_rebuttal_answer_cases.json` contains 16 manually labelled Chinese cases over:

- MAC-KV original OpenReview and final rebuttal;
- Holo original OpenReview and final rebuttal.

The cases cover reviewer strengths, reviewer criticism, Area Chair summaries, specific experimental values, author responses, scope limitations, explicit source restrictions, cross-document questions, and unsupported-information abstention. The answer key is used only by the evaluation script; production code never reads it.

Run it locally after configuring `DEEPSEEK_API_KEY`:

```powershell
D:\ResearchFlow-runtime\Scripts\python.exe scripts\run_portfolio_answer_eval.py `
  --corpus-root .. `
  --embedding-provider fastembed `
  --manifest evals\review_rebuttal_answer_cases.json `
  --output evals\results\review_rebuttal_answer_eval_fastembed.json
```

### Baseline result — 2026-08-09

| Metric | Result |
| --- | ---: |
| Cases | 16 |
| Answerable cases | 14 |
| Reference-claim coverage | 0.7368 |
| LLM-assisted grounded-answer rate | 0.7857 |
| Valid citation-marker rate | 0.8571 |
| Correct abstention rate | 1.0000 |
| **Source-scope compliance rate** | **0.2500** |
| Mean end-to-end latency | 5969.35 ms |

This is a failure-revealing baseline, not a marketing metric. The system handles several factual rebuttal questions but does **not** reliably honor constraints such as "only original reviews" or "do not include author rebuttal." A syntactically valid `[n]` citation is therefore not equivalent to source-scope compliance or claim-level faithfulness.

## 3. Natural reviewer/author retrieval and BGE reranking

`evals/review_role_routing_cases.json` contains 12 questions such as “Reviewer Uv9P 对 FourierFT 基线公平性提出了什么质疑？” and “作者如何收缩 Holo 的机制主张？”. The query text contains **no file name, document ID, `original`/`rebuttal` phrase, or source-selection instruction**. Expected source labels remain evaluation-only.

On one NVIDIA RTX 4090D (CUDA) with FastEmbed first-stage retrieval, the comparison was:

| Strategy | Correct expected-document at rank 1 | Expected-document Recall@6 | Mean query latency |
| --- | ---: | ---: | ---: |
| Hybrid RRF | 0.5833 | 0.9167 | 523.21 ms |
| Hybrid RRF + BGE rerank | **0.9167** | 0.9167 | 595.88 ms |

This supports BGE as a **passage reranker** after first-stage recall. A separate document-identity Top-1 experiment reached only 0.6667, so document-level automatic source filtering is intentionally not enabled in the production graph. It could exclude the correct evidence before answer generation.

Run on a CUDA machine after installing `.[rerank]`:

```bash
RERANKER_CACHE_DIR=/data/models RERANKER_DEVICE=cuda \
python scripts/run_role_retrieval_eval.py --corpus-root /path/to/corpus --device cuda
```

## 4. External abstract-level retrieval: BEIR SciFact

[BEIR SciFact](https://github.com/beir-cellar/beir/wiki/Datasets-available) provides scientific claims, **paper abstracts**, and official relevance judgments in the standard corpus/queries/qrels format. It is retained as an external, cross-document retrieval regression—not as a proof of full-paper retrieval. `scripts/run_beir_scifact_subset_eval.py` downloads the public archive to `D:\ResearchFlow-runtime\datasets`, then evaluates a seeded subset so the V1 in-process linear retriever remains practical.

```powershell
D:\ResearchFlow-runtime\Scripts\python.exe scripts\run_beir_scifact_subset_eval.py `
  --embedding-provider fastembed `
  --max-documents 1000 `
  --max-queries 100
```

### Fixed subset result — 2026-08-09

Configuration: seed `20260809`, 1,000 documents, 100 test queries, official SciFact relevance judgments.

| Strategy | Recall@10 | MRR@10 | Mean query latency |
| --- | ---: | ---: | ---: |
| Lexical | 0.8200 | **0.6652** | 740.32 ms |
| Dense | 0.6600 | 0.4286 | 727.51 ms |
| Hybrid RRF | **0.8300** | 0.6284 | 731.38 ms |

The result confirms that the first-stage retriever works beyond the project's own papers. It also shows that a strong lexical baseline remains competitive for scientific terminology, and that V1's Python/SQLite linear scan is not suitable for a large interactive corpus without a dedicated lexical/vector index. This is a controlled subset result, **not** a full-corpus BEIR leaderboard score or a full-text document result.

## 5. External full-text evidence retrieval: QASPER

[QASPER](https://aclanthology.org/2021.naacl-main.365/) contains 5,049 information-seeking questions over 1,585 NLP papers. Questions are written after seeing a paper title and abstract but require information from the paper's full text; separate annotators provide supporting evidence. This is the complementary long-document layer missing from SciFact.

`scripts/run_qasper_fulltext_eval.py` downloads the public QASPER v0.3 train/dev archive to `D:\ResearchFlow-runtime\datasets` and evaluates a fixed development-set sample. The system indexes only title, abstract and full-text paragraphs. It never indexes answers or gold evidence. Each original question is searched within its associated paper, matching the dataset's document-level task. The question text itself contains no filename or document identifier.

```powershell
D:\ResearchFlow-runtime\Scripts\python.exe scripts\run_qasper_fulltext_eval.py `
  --embedding-provider fastembed `
  --max-papers 30 `
  --max-queries 60
```

### Fixed full-text result — 2026-08-09

Configuration: seed `20260809`, up to 30 QASPER development papers, 60 answerable original questions.

| Strategy | Evidence-recall proxy@1 | Evidence-recall proxy@4 | MRR@4 | Mean query latency |
| --- | ---: | ---: | ---: | ---: |
| Lexical | 0.1833 | 0.4167 | 0.2750 | 28.32 ms |
| Dense | 0.1500 | 0.4167 | 0.2556 | 26.92 ms |
| Hybrid RRF | **0.2167** | **0.4500** | **0.3111** | 28.09 ms |

The metric is deliberately named an **evidence-recall proxy**: a retrieved chunk counts when it contains a gold-evidence span or covers at least 72% of that span's normalized tokens, accommodating evidence that crosses a chunk boundary. These substantially lower numbers than SciFact are expected: this is a harder full-paper evidence task, and the result is a baseline that identifies long-context retrieval as the next quality bottleneck. It does not measure answer correctness, cross-document document selection, tables/figures, scanned PDFs, or multi-document synthesis.

### Optional GPU reranking result — NVIDIA RTX 4090D

The same 60 fixed questions were rerun with `BAAI/bge-reranker-v2-m3` on CUDA. The first stage remains exactly the same Hybrid RRF. BGE receives only the question and the first-stage top-20 **text chunks**, then reranks those chunks; it does not rank full documents and it has no document-title, reviewer, filename, or question-mapping rule.

| Strategy | Evidence-recall proxy@1 | Evidence-recall proxy@4 | MRR@4 | Mean warm-query latency |
| --- | ---: | ---: | ---: | ---: |
| Hybrid RRF | 0.2167 | 0.4500 | 0.3111 | 267.18 ms |
| Hybrid RRF + BGE chunk rerank | **0.2667** | **0.5500** | **0.3722** | 560.86 ms |

This is a meaningful +10.0 percentage-point Recall@4 improvement for an additional ~294 ms per warm query on that GPU. It justifies BGE as an **optional GPU second stage**, not as the CPU/local default. The evaluation process loads the model once, removes each paper from the temporary index after its questions, and does not include model-load time in the per-query latency.
