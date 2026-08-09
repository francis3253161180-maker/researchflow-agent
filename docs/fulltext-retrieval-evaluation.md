# Full-text retrieval evaluation: why SciFact is not enough

## The short answer

Yes: the BEIR SciFact corpus used by this project is abstract-level. It is a useful, public, judged cross-document retrieval regression, but its documents are much shorter than imported research papers. It cannot substantiate claims about long-document chunking or evidence retrieval by itself.

ResearchFlow therefore keeps three non-interchangeable verification layers:

| Evaluation layer | Evidence unit | Question setting | Primary conclusion |
| --- | --- | --- | --- |
| BEIR SciFact subset | Paper abstract | Cross-document claim retrieval | Does the first-stage retriever find a judged relevant scientific record? |
| QASPER v0.3 dev subset | Full-paper paragraphs | Original question, retrieval within the associated full paper | Can the system surface human-marked evidence from a long paper? |
| Local format regression | Real PDF, DOCX, XLSX, Markdown and TXT | Controlled retrieval questions | Do parsers retain useful text/row/page/section evidence across supported formats? |

No row can replace another. In particular, a strong SciFact result does not imply that a complicated PDF, a spreadsheet, or an OpenReview thread will retrieve correctly.

## Why QASPER is the right complement

QASPER is a public scientific-document QA dataset of 5,049 questions over 1,585 NLP papers. Question writers saw only a title and abstract, while answer writers located answers and evidence in the full paper. This makes it appropriate for testing the precise weak point left uncovered by abstract retrieval: locating relevant passages in long, structured scientific text.

ResearchFlow uses the development split and only answerable questions with human-marked text evidence. The evaluator:

1. Downloads the public `qasper-train-dev-v0.3.tgz` archive to `D:\ResearchFlow-runtime\datasets`.
2. Serializes each selected paper as `Abstract` plus the source sections and paragraphs from `full_text`.
3. Indexes those blocks using the same production chunker and the selected retrieval backend.
4. Sends the original QASPER question unchanged to lexical, dense and hybrid retrieval.
5. Computes whether one of the top results overlaps its human-marked evidence.

The gold answer and gold evidence are never inserted into the index. They are used only after search to score the retrieved chunks.

## Boundary of the protocol

QASPER is **document-level QA**: the paper under discussion is known to the benchmark. Accordingly, its evaluator searches within that paper, but the question string itself does not contain a filename, document ID, or source-selection instruction. This is a valid test of chunking and evidence ranking; it is not a test of choosing the correct paper from a large heterogeneous knowledge base.

For cross-document retrieval, SciFact remains the public layer. For production source selection, ResearchFlow deliberately provides an optional user-selected document scope rather than pretending that the current V1 can infer exact source boundaries reliably from ambiguous reviewer/rebuttal text.

## Reproducing the fixed run

```powershell
cd C:\Users\32531\Desktop\找实习\researchflow-agent
D:\ResearchFlow-runtime\Scripts\python.exe scripts\run_qasper_fulltext_eval.py `
  --embedding-provider fastembed `
  --max-papers 30 `
  --max-queries 60 `
  --seed 20260809 `
  --output evals\results\qasper_fulltext_fastembed_20260809.json
```

The 2026-08-09 fixed run gives Hybrid RRF evidence-recall proxy@4 of **0.4500** (60 questions), versus 0.4167 for both individual first-stage methods. This should be treated as an honest baseline, not an enterprise accuracy metric.

On the same fixed protocol, the optional `BAAI/bge-reranker-v2-m3` CUDA second stage improved evidence-recall proxy@4 to **0.5500** and MRR@4 from 0.3111 to **0.3722**. It reranks only Hybrid's top-20 candidate **chunks**, never full documents. The tradeoff on an NVIDIA RTX 4090D was 267.18 ms to 560.86 ms mean warm-query latency. This is enough evidence to keep it as an optional GPU setting; it is not a reason to make local CPU startup depend on a large cross-encoder.

```bash
# GPU experiment: same QASPER protocol, with chunk-level BGE reranking
RERANKER_PROVIDER=bge RERANKER_DEVICE=cuda \
python scripts/run_qasper_fulltext_eval.py \
  --embedding-provider fastembed \
  --reranker-provider bge \
  --reranker-device cuda \
  --max-papers 30 --max-queries 60
```

The next improvement target is therefore not arbitrary feature growth: it is a stronger long-context representation or a reranker configuration that must again be measured on this unchanged protocol.

## What this still does not test

- Answer generation quality or claim-level faithfulness;
- Figures, tables, formula rendering, or scanned-PDF OCR;
- Cross-document source routing, multi-hop synthesis, or citation completeness;
- Chinese queries over English long papers;
- Throughput at a large interactive corpus scale.

Those are intentionally separate work items, not hidden behind a single aggregate score.

## Sources

- [QASPER paper and dataset description](https://aclanthology.org/2021.naacl-main.365/)
- [QASPER public data repository](https://huggingface.co/datasets/allenai/qasper)
- [BEIR SciFact dataset listing](https://github.com/beir-cellar/beir/wiki/Datasets-available)
