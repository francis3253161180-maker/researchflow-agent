"""Evaluate BGE's document-identity ranking for source-constrained questions.

This intentionally measures a narrower task than passage retrieval: given a
question that has already been identified as source-constrained, rank document
metadata (title, filename, source, headings) and choose the source document.
No production answer key is imported by application code.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from app.config import Settings
from app.ingestion import parse_upload
from app.service import ResearchFlowService


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "evals" / "review_rebuttal_answer_cases.json"


def ingest_corpus(service: ResearchFlowService, corpus_root: Path, documents: dict[str, str]) -> None:
    for title, relative_path in documents.items():
        path = corpus_root / relative_path
        if not path.exists():
            raise FileNotFoundError(f"Missing local corpus file: {path}")
        parsed = parse_upload(path.name, path.read_bytes(), source="source-scope-eval")
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


def run(corpus_root: Path, manifest_path: Path, device: str = "auto") -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    base = Settings.from_env()
    with TemporaryDirectory() as directory:
        settings = Settings(
            db_path=str(Path(directory) / "source-scope-eval.db"),
            embedding_provider="hash",
            reranker_provider="bge",
            reranker_model=base.reranker_model,
            reranker_cache_dir=base.reranker_cache_dir,
            reranker_device=device,
        )
        service = ResearchFlowService(settings)
        ingest_corpus(service, corpus_root, manifest["documents"])
        catalog = service.db.document_catalog()
        rows = []
        for case in manifest["cases"]:
            expected = case.get("allowed_documents", [])
            if len(expected) != 1:
                continue
            selected_ids = service.retriever.select_document_scope(case["query"], catalog) or []
            selected_titles = [item["title"] for item in catalog if item["id"] in selected_ids]
            rows.append(
                {
                    "id": case["id"],
                    "expected_document": expected[0],
                    "selected_documents": selected_titles,
                    "correct": selected_titles == expected,
                }
            )
    correct = sum(row["correct"] for row in rows)
    return {
        "protocol": {
            "task": "single-document source identity ranking after a source constraint is detected",
            "candidate_metadata": "title, filename, source and structural headings only",
            "limitation": "Does not evaluate source-restriction detection, multi-document scope, passage retrieval or answer faithfulness.",
        },
        "device": device,
        "cases": len(rows),
        "accuracy": round(correct / len(rows), 4) if rows else None,
        "details": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate BGE document-source ranking on local review/rebuttal files.")
    parser.add_argument("--corpus-root", type=Path, default=ROOT.parent)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--output", type=Path, default=ROOT / "evals" / "results" / "source_scope_rerank_eval.json")
    args = parser.parse_args()
    report = run(args.corpus_root, args.manifest, args.device)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "details"}, ensure_ascii=False, indent=2))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
