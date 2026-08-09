# Evaluation suite: what is measured, what is not

ResearchFlow now uses three deliberately separate evaluation layers. They answer different questions and must not be combined into a single "accuracy" number.

| Layer | Corpus | What it tests | What it does not prove |
| --- | --- | --- | --- |
| Repository regression | Versioned `docs/` Markdown | Parsing, chunking and hybrid retrieval over project documentation | Real-world RAG quality |
| Local review/rebuttal QA | MAC-KV and Holo OpenReview/rebuttal Markdown | Grounded answer coverage, citation syntax, abstention and source-scope compliance | General enterprise-document accuracy |
| BEIR SciFact subset | External claims, abstracts and official qrels | General document retrieval with judged relevance | Full BEIR leaderboard performance or answer faithfulness |

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

## 3. External document retrieval: BEIR SciFact

[BEIR SciFact](https://github.com/beir-cellar/beir/wiki/Datasets-available) provides scientific claims, paper abstracts and official relevance judgments in the standard corpus/queries/qrels format. `scripts/run_beir_scifact_subset_eval.py` downloads the public archive to `D:\ResearchFlow-runtime\datasets`, then evaluates a seeded subset so the V1 in-process linear retriever remains practical.

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
| Lexical | 0.8200 | 0.6652 | 736.30 ms |
| Dense | 0.6600 | 0.4286 | 725.71 ms |
| Hybrid RRF | **0.8300** | 0.6234 | 738.82 ms |

The result confirms that the first-stage retriever works beyond the project's own papers. It also shows that a strong lexical baseline remains competitive for scientific terminology, and that V1's Python/SQLite linear scan is not suitable for a large interactive corpus without a dedicated lexical/vector index. This is a controlled subset result, **not** a full-corpus BEIR leaderboard score.
