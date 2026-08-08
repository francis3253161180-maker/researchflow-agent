"""Reproducible document-retrieval evaluation for ResearchFlow.

This is deliberately a small, labelled evaluation over research papers, not a
claim that ResearchFlow reproduces or outperforms the graph/RAG systems in the
papers. It measures only whether the local retriever ranks the annotated source
paper for a fixed set of questions.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from time import perf_counter
from tempfile import TemporaryDirectory

from app.config import Settings
from app.ingestion import parse_upload
from app.service import ResearchFlowService


ROOT = Path(__file__).resolve().parents[1]
PAPER_FILES = {
    "CWA-GRAPH": "13410_CWAGraph_Retrieving_What.pdf",
    "SAR-UIE": "14375_Structural_Alignment_Ret.pdf",
    "ERES-FROG": "14840_Multi_Hop_Question_Answe.pdf",
    "TopoR": "19791_Topology_Before_Semantic.pdf",
}


def unique_document_ranking(results) -> list[str]:
    """Deduplicate chunks so rank is a paper rank, not a repeated-chunk rank."""
    seen: set[str] = set()
    ranking: list[str] = []
    for result in results:
        if result.title not in seen:
            seen.add(result.title)
            ranking.append(result.title)
    return ranking


def metrics(rows: list[dict]) -> dict[str, float | int]:
    total = len(rows)
    return {
        "queries": total,
        "recall_at_1": round(sum(row["rank"] <= 1 for row in rows) / total, 4),
        "recall_at_2": round(sum(row["rank"] <= 2 for row in rows) / total, 4),
        "mrr_at_4": round(mean(1 / row["rank"] if row["rank"] <= 4 else 0 for row in rows), 4),
        "mean_latency_ms": round(mean(row["latency_ms"] for row in rows), 2),
    }


def evaluate(corpus_dir: Path, embedding_provider: str, top_k: int = 16) -> dict:
    queries = json.loads((ROOT / "evals" / "paper_retrieval_queries.json").read_text(encoding="utf-8"))
    missing = [filename for filename in PAPER_FILES.values() if not (corpus_dir / filename).exists()]
    if missing:
        raise FileNotFoundError(f"Missing evaluation PDFs in {corpus_dir}: {', '.join(missing)}")

    with TemporaryDirectory() as directory:
        service = ResearchFlowService(
            Settings(
                db_path=str(Path(directory) / "paper-retrieval.db"),
                embedding_provider=embedding_provider,
                fastembed_cache_dir=str(ROOT / "data" / "models"),
            )
        )
        for paper_id, filename in PAPER_FILES.items():
            path = corpus_dir / filename
            parsed = parse_upload(path.name, path.read_bytes(), source="public-paper-eval")
            # Use stable IDs rather than anonymous-paper titles as judgement labels.
            parsed = parsed.__class__(
                title=paper_id,
                source=parsed.source,
                content=parsed.content,
                blocks=parsed.blocks,
                filename=parsed.filename,
                media_type=parsed.media_type,
            )
            service.ingest_parsed(parsed)

        by_strategy: dict[str, list[dict]] = defaultdict(list)
        for item in queries:
            for strategy in ("lexical", "dense", "hybrid"):
                started = perf_counter()
                ranking = unique_document_ranking(service.retriever.search(item["query"], top_k=top_k, strategy=strategy))
                elapsed_ms = (perf_counter() - started) * 1000
                try:
                    rank = ranking.index(item["relevant_document"]) + 1
                except ValueError:
                    rank = 999
                by_strategy[strategy].append(
                    {"id": item["id"], "query": item["query"], "target": item["relevant_document"], "rank": rank, "latency_ms": round(elapsed_ms, 2)}
                )

    return {
        "protocol": {
            "corpus": "4 locally supplied public research-paper PDFs",
            "queries": "16 manually labelled document-retrieval questions",
            "unit_of_relevance": "source paper, deduplicated across retrieved chunks",
            "strategies": ["lexical", "dense", "hybrid"],
            "warning": "This evaluates ResearchFlow retrieval only; it is not a reproduction or comparison of the papers' methods.",
        },
        "embedding_provider": embedding_provider,
        "summary": {strategy: metrics(rows) for strategy, rows in by_strategy.items()},
        "details": by_strategy,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the labelled ResearchFlow paper-retrieval evaluation.")
    parser.add_argument("--corpus-dir", type=Path, default=ROOT.parent, help="Directory containing the four named PDFs")
    parser.add_argument("--embedding-provider", choices=["hash", "fastembed"], default="hash")
    parser.add_argument("--output", type=Path, default=ROOT / "evals" / "results" / "paper_retrieval.json")
    args = parser.parse_args()
    report = evaluate(args.corpus_dir, args.embedding_provider)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
