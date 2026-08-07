from __future__ import annotations

import json
import argparse
from pathlib import Path
from statistics import mean
import tempfile

from app.config import Settings
from app.service import ResearchFlowService


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the bounded offline ResearchFlow retrieval evaluation.")
    parser.add_argument("--embedding-provider", choices=["hash", "fastembed"], default="hash")
    args = parser.parse_args()
    dataset = json.loads((ROOT / "evals" / "eval_set.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as directory:
        service = ResearchFlowService(
            Settings(
                db_path=str(Path(directory) / "eval.db"),
                embedding_provider=args.embedding_provider,
                fastembed_cache_dir=str(ROOT / "data" / "models"),
            )
        )
        for item in dataset:
            service.ingest(item["title"], "eval-set", item["content"])
        results = []
        for index, item in enumerate(dataset):
            response = service.chat(item["question"], f"eval_{index}")
            results.append(
                {
                    "question": item["question"],
                    "retrieval_hit": any(item["expected"] in citation["content"] for citation in response["citations"]),
                    "answer_hit": item["expected"] in response["answer"],
                    "has_citation": bool(response["citations"]),
                    "verified": response["verified"],
                    "latency_ms": response["latency_ms"],
                }
            )
        total = len(results)
        summary = {
            "embedding_provider": args.embedding_provider,
            "samples": total,
            "retrieval_hit_at_4": sum(item["retrieval_hit"] for item in results) / total,
            "answer_hit_rate": sum(item["answer_hit"] for item in results) / total,
            "citation_rate": sum(item["has_citation"] for item in results) / total,
            "verified_rate": sum(item["verified"] for item in results) / total,
            "average_latency_ms": round(mean(item["latency_ms"] for item in results), 2),
        }
        print(json.dumps({"summary": summary, "details": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
