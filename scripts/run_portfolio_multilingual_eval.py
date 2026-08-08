"""Evaluate multilingual retrieval on locally available paper, rebuttal, and review files.

The corpus is intentionally local and not committed. This script measures
document-level ranking and evidence-hint recall; it does not claim answer
faithfulness or general enterprise-RAG accuracy.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from tempfile import TemporaryDirectory
from time import perf_counter

from app.config import Settings
from app.ingestion import parse_upload
from app.service import ResearchFlowService


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "evals" / "portfolio_multilingual_queries.json"


def document_ranking(results) -> list[str]:
    seen: set[str] = set()
    return [item.title for item in results if not (item.title in seen or seen.add(item.title))]


def summarize(rows: list[dict]) -> dict[str, float | int]:
    total = len(rows)
    return {
        "queries": total,
        "recall_at_1": round(sum(row["document_rank"] <= 1 for row in rows) / total, 4),
        "recall_at_3": round(sum(row["document_rank"] <= 3 for row in rows) / total, 4),
        "mrr_at_6": round(mean(1 / row["document_rank"] if row["document_rank"] <= 6 else 0 for row in rows), 4),
        "evidence_hint_at_4": round(sum(row["evidence_hint_found"] for row in rows) / total, 4),
        "mean_latency_ms": round(mean(row["latency_ms"] for row in rows), 2),
    }


def evaluate(corpus_root: Path, provider: str, top_k: int = 12) -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    documents: dict[str, str] = manifest["documents"]
    missing = [path for path in documents.values() if not (corpus_root / path).exists()]
    if missing:
        raise FileNotFoundError(f"Missing local corpus files: {', '.join(missing)}")

    with TemporaryDirectory() as directory:
        service = ResearchFlowService(
            Settings(
                db_path=str(Path(directory) / "portfolio-eval.db"),
                embedding_provider=provider,
                fastembed_cache_dir=str(ROOT / "data" / "models"),
            )
        )
        for document_id, relative_path in documents.items():
            source = corpus_root / relative_path
            parsed = parse_upload(source.name, source.read_bytes(), source="local-portfolio-eval")
            parsed = parsed.__class__(
                title=document_id,
                source=parsed.source,
                filename=parsed.filename,
                media_type=parsed.media_type,
                content=parsed.content,
                blocks=parsed.blocks,
            )
            service.ingest_parsed(parsed)

        rows: dict[str, list[dict]] = defaultdict(list)
        for item in manifest["queries"]:
            for strategy in ("lexical", "dense", "hybrid"):
                started = perf_counter()
                results = service.retriever.search(item["query"], top_k=top_k, strategy=strategy)
                elapsed_ms = (perf_counter() - started) * 1000
                ranking = document_ranking(results)
                try:
                    document_rank = ranking.index(item["relevant_document"]) + 1
                except ValueError:
                    document_rank = 999
                hint = item["evidence_hint"].lower()
                evidence_hint_found = any(
                    result.title == item["relevant_document"] and hint in result.content.lower()
                    for result in results[:4]
                )
                rows[strategy].append(
                    {
                        **item,
                        "strategy": strategy,
                        "document_rank": document_rank,
                        "evidence_hint_found": evidence_hint_found,
                        "latency_ms": round(elapsed_ms, 2),
                        "top_documents": ranking[:6],
                    }
                )

    return {
        "protocol": {
            "corpus": "8 local user-provided files: two papers, two rebuttals, two OpenReview Markdown records, one DOCX resume, and one PDF resume",
            "queries": "16 manually authored Chinese retrieval questions with document and evidence-hint labels",
            "unit_of_relevance": "document rank plus a lightweight evidence-hint check in the top 4 chunks",
            "warning": "Small portfolio regression evaluation only; not a claim of general RAG accuracy or answer faithfulness.",
        },
        "embedding_provider": provider,
        "summary": {strategy: summarize(strategy_rows) for strategy, strategy_rows in rows.items()},
        "details": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local multilingual portfolio retrieval evaluation.")
    parser.add_argument("--corpus-root", type=Path, default=ROOT.parent)
    parser.add_argument("--embedding-provider", choices=["hash", "fastembed"], default="fastembed")
    parser.add_argument("--output", type=Path, default=ROOT / "evals" / "results" / "portfolio_multilingual.json")
    args = parser.parse_args()
    report = evaluate(args.corpus_root, args.embedding_provider)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
