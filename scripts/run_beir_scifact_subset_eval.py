"""Download and evaluate a controlled BEIR SciFact retrieval subset.

SciFact provides scientific claims, paper abstracts, and relevance judgments in
the standard BEIR corpus/queries/qrels layout.  This script deliberately uses a
seeded subset: ResearchFlow V1 performs in-process linear scanning, so a
1,000-document slice gives a repeatable external regression signal without
misrepresenting itself as a full BEIR leaderboard submission.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import zipfile
from pathlib import Path
from statistics import mean
from tempfile import TemporaryDirectory
from time import perf_counter

import httpx

from app.config import Settings
from app.service import ResearchFlowService


ROOT = Path(__file__).resolve().parents[1]
SCIFACT_URL = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip"


def ensure_dataset(dataset_root: Path) -> Path:
    dataset_dir = dataset_root / "scifact"
    if (dataset_dir / "corpus.jsonl").exists() and (dataset_dir / "queries.jsonl").exists():
        return dataset_dir
    dataset_root.mkdir(parents=True, exist_ok=True)
    archive = dataset_root / "scifact.zip"
    if archive.exists() and archive.stat().st_size == 0:
        archive.unlink()
    if not archive.exists():
        print(f"Downloading BEIR SciFact to {archive}")
        with httpx.stream("GET", SCIFACT_URL, follow_redirects=True, timeout=120) as response:
            response.raise_for_status()
            with archive.open("wb") as handle:
                for block in response.iter_bytes():
                    handle.write(block)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(dataset_root)
    if not (dataset_dir / "corpus.jsonl").exists():
        raise FileNotFoundError("SciFact archive did not contain the expected BEIR layout")
    return dataset_dir


def load_jsonl(path: Path) -> dict[str, dict]:
    with path.open(encoding="utf-8") as handle:
        return {str(item["_id"]): item for item in map(json.loads, handle) if item.get("_id") is not None}


def load_qrels(path: Path) -> dict[str, set[str]]:
    qrels: dict[str, set[str]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            if int(row["score"]) > 0:
                qrels.setdefault(str(row["query-id"]), set()).add(str(row["corpus-id"]))
    return qrels


def evaluate(dataset_dir: Path, provider: str, max_documents: int, max_queries: int, seed: int) -> dict:
    corpus = load_jsonl(dataset_dir / "corpus.jsonl")
    queries = load_jsonl(dataset_dir / "queries.jsonl")
    qrels = load_qrels(dataset_dir / "qrels" / "test.tsv")
    eligible = sorted(query_id for query_id in qrels if query_id in queries and qrels[query_id] & corpus.keys())
    rng = random.Random(seed)
    selected_queries = sorted(rng.sample(eligible, min(max_queries, len(eligible))))
    required_documents = set().union(*(qrels[query_id] for query_id in selected_queries))
    if len(required_documents) > max_documents:
        raise ValueError("max_documents is smaller than the selected queries' relevant-document set")
    remaining = sorted(set(corpus) - required_documents)
    sampled_documents = required_documents | set(rng.sample(remaining, max_documents - len(required_documents)))

    rows: dict[str, list[dict]] = {strategy: [] for strategy in ("lexical", "dense", "hybrid")}
    with TemporaryDirectory() as directory:
        settings = Settings(
            db_path=str(Path(directory) / "scifact.db"),
            embedding_provider=provider,
            fastembed_cache_dir="D:/ResearchFlow-runtime/models",
        )
        service = ResearchFlowService(settings)
        for document_id in sorted(sampled_documents):
            record = corpus[document_id]
            service.ingest(
                title=document_id,
                source="beir-scifact",
                content=f"{record.get('title', '')}\n\n{record.get('text', '')}".strip(),
            )
        for query_id in selected_queries:
            relevant = qrels[query_id]
            for strategy in rows:
                started = perf_counter()
                results = service.retriever.search(queries[query_id]["text"], top_k=10, strategy=strategy)
                elapsed_ms = (perf_counter() - started) * 1000
                ranking = [item.title for item in results]
                first_rank = next((rank for rank, doc_id in enumerate(ranking, start=1) if doc_id in relevant), None)
                rows[strategy].append(
                    {
                        "query_id": query_id,
                        "rank": first_rank or 999,
                        "hit_at_10": first_rank is not None,
                        "latency_ms": round(elapsed_ms, 2),
                    }
                )

    def summary(values: list[dict]) -> dict:
        return {
            "queries": len(values),
            "recall_at_10": round(mean(row["hit_at_10"] for row in values), 4),
            "mrr_at_10": round(mean(1 / row["rank"] if row["rank"] <= 10 else 0 for row in values), 4),
            "mean_latency_ms": round(mean(row["latency_ms"] for row in values), 2),
        }

    return {
        "protocol": {
            "dataset": "BEIR SciFact",
            "source": SCIFACT_URL,
            "subset": f"seed={seed}; {len(sampled_documents)} documents; {len(selected_queries)} judged test queries",
            "metrics": "document Recall@10 and MRR@10 using the official BEIR test qrels",
            "warning": "This is a controlled SciFact subset for local regression, not an official full-corpus BEIR leaderboard result.",
        },
        "embedding_provider": provider,
        "summary": {strategy: summary(values) for strategy, values in rows.items()},
        "details": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a controlled BEIR SciFact subset retrieval evaluation.")
    parser.add_argument("--dataset-root", type=Path, default=Path("D:/ResearchFlow-runtime/datasets"))
    parser.add_argument("--embedding-provider", choices=["hash", "fastembed"], default="fastembed")
    parser.add_argument("--max-documents", type=int, default=1000)
    parser.add_argument("--max-queries", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260809)
    parser.add_argument("--output", type=Path, default=ROOT / "evals" / "results" / "beir_scifact_subset.json")
    args = parser.parse_args()
    dataset_dir = ensure_dataset(args.dataset_root)
    report = evaluate(dataset_dir, args.embedding_provider, args.max_documents, args.max_queries, args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
