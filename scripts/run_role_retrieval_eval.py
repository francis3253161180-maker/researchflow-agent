"""Compare passage retrieval strategies on natural reviewer/author questions.

Expected document labels are evaluation-only. The queries deliberately avoid
file names, document identifiers and source-selection wording, so the result
measures whether retrieved passages themselves preserve speaker provenance.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from app.config import Settings
from app.ingestion import parse_upload
from app.service import ResearchFlowService


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "evals" / "review_role_routing_cases.json"


def ingest_corpus(service: ResearchFlowService, corpus_root: Path, documents: dict[str, str]) -> None:
    for title, relative_path in documents.items():
        path = corpus_root / relative_path
        if not path.exists():
            raise FileNotFoundError(f"Missing local corpus file: {path}")
        parsed = parse_upload(path.name, path.read_bytes(), source="role-retrieval-eval")
        service.ingest_parsed(
            parsed.__class__(
                title=title,
                source=parsed.source,
                filename=parsed.filename,
                media_type=parsed.media_type,
                content=parsed.content,
                blocks=parsed.blocks,
            )
        )


def evaluate(service: ResearchFlowService, cases: list[dict], strategy: str, top_k: int) -> dict:
    rows = []
    for case in cases:
        started = perf_counter()
        hits = service.retriever.search(case["query"], top_k=top_k, strategy=strategy)
        elapsed = (perf_counter() - started) * 1000
        titles = [hit.title for hit in hits]
        expected = case["allowed_documents"]
        rows.append(
            {
                "id": case["id"],
                "expected_documents": expected,
                "retrieved_documents": titles,
                "top1_correct": bool(titles and titles[0] in expected),
                "recall_at_k": any(title in expected for title in titles),
                "latency_ms": round(elapsed, 2),
            }
        )
    return {
        "top1_document_accuracy": round(sum(row["top1_correct"] for row in rows) / len(rows), 4),
        f"document_recall_at_{top_k}": round(sum(row["recall_at_k"] for row in rows) / len(rows), 4),
        "mean_query_latency_ms": round(sum(row["latency_ms"] for row in rows) / len(rows), 2),
        "details": rows,
    }


def run(corpus_root: Path, manifest_path: Path, device: str = "auto", top_k: int = 6) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    base = Settings.from_env()
    with TemporaryDirectory() as directory:
        common = {
            "db_path": str(Path(directory) / "role-retrieval-eval.db"),
            "embedding_provider": "fastembed",
            "fastembed_cache_dir": base.fastembed_cache_dir,
            "reranker_model": base.reranker_model,
            "reranker_cache_dir": base.reranker_cache_dir,
            "reranker_device": device,
            "reranker_candidates": 20,
        }
        baseline = ResearchFlowService(Settings(**common, reranker_provider="none"))
        ingest_corpus(baseline, corpus_root, manifest["documents"])
        reranked = ResearchFlowService(Settings(**common, reranker_provider="bge"))
        ingest_corpus(reranked, corpus_root, manifest["documents"])
        return {
            "protocol": {
                "task": "passage retrieval for natural reviewer/author questions",
                "queries": "No file name, document identifier, original/rebuttal phrase or source-selection instruction.",
                "metric": "Expected document appears at rank 1 or within top-k retrieved passages.",
                "limitation": "Document-level provenance in retrieval is not answer faithfulness; answers still require citation review.",
            },
            "device": device,
            "cases": len(manifest["cases"]),
            "top_k": top_k,
            "hybrid_rrf": evaluate(baseline, manifest["cases"], "hybrid", top_k),
            "hybrid_rrf_bge_rerank": evaluate(reranked, manifest["cases"], "hybrid", top_k),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare hybrid retrieval with and without BGE on natural role questions.")
    parser.add_argument("--corpus-root", type=Path, default=ROOT.parent)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--output", type=Path, default=ROOT / "evals" / "results" / "role_retrieval_eval.json")
    args = parser.parse_args()
    report = run(args.corpus_root, args.manifest, args.device, args.top_k)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in {"hybrid_rrf", "hybrid_rrf_bge_rerank"}}, ensure_ascii=False, indent=2))
    for name in ("hybrid_rrf", "hybrid_rrf_bge_rerank"):
        metrics = report[name]
        print(name, json.dumps({key: value for key, value in metrics.items() if key != "details"}, ensure_ascii=False))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
