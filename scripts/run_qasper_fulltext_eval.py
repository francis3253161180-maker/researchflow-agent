"""Evaluate long-document evidence retrieval on the public QASPER development set.

QASPER contains questions written from titles/abstracts whose answers must be
located in the corresponding paper's full text.  This evaluator keeps the
benchmark's document-level setting: each raw question is searched against all
paragraph chunks from its associated paper.  It therefore measures long-paper
chunking and evidence retrieval, *not* open-corpus document routing.

The production service never receives an answer or gold evidence during
indexing.  Gold evidence is used only after retrieval to calculate recall.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import tarfile
from pathlib import Path
from statistics import mean
from tempfile import TemporaryDirectory
from time import perf_counter

import httpx

from app.config import Settings
from app.ingestion import ParsedDocument, TextBlock
from app.retrieval import HybridRetriever, build_reranker
from app.service import ResearchFlowService


ROOT = Path(__file__).resolve().parents[1]
QASPER_ARCHIVE_URL = "https://qasper-dataset.s3.us-west-2.amazonaws.com/qasper-train-dev-v0.3.tgz"
DEV_FILENAME = "qasper-dev-v0.3.json"


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def ensure_dataset(dataset_root: Path) -> Path:
    """Download the official train/dev archive once, keeping it off the system drive."""
    dataset_dir = dataset_root / "qasper"
    data_path = dataset_dir / DEV_FILENAME
    if data_path.exists():
        return data_path
    dataset_dir.mkdir(parents=True, exist_ok=True)
    archive = dataset_root / "qasper-train-dev-v0.3.tgz"
    if not archive.exists():
        print(f"Downloading QASPER train/dev archive to {archive}")
        with httpx.stream("GET", QASPER_ARCHIVE_URL, follow_redirects=True, timeout=120) as response:
            response.raise_for_status()
            with archive.open("wb") as handle:
                for block in response.iter_bytes():
                    handle.write(block)
    with tarfile.open(archive, "r:gz") as bundle:
        member = bundle.getmember(DEV_FILENAME)
        bundle.extract(member, dataset_dir, filter="data")
    if not data_path.exists():
        raise FileNotFoundError("QASPER archive did not contain the expected development JSON")
    return data_path


def answerable_evidence(question: dict) -> list[str]:
    """Return distinct human-marked text evidence, excluding unanswerable labels."""
    evidence: list[str] = []
    for annotation in question.get("answers", []):
        answer = annotation.get("answer", {})
        if answer.get("unanswerable"):
            continue
        evidence.extend(answer.get("highlighted_evidence") or answer.get("evidence") or [])
    unique: list[str] = []
    seen: set[str] = set()
    for item in evidence:
        cleaned = normalize(item)
        if len(cleaned) >= 30 and cleaned not in seen:
            seen.add(cleaned)
            unique.append(cleaned)
    return unique


def paper_blocks(paper: dict) -> list[TextBlock]:
    blocks: list[TextBlock] = []
    abstract = str(paper.get("abstract", "")).strip()
    if abstract:
        blocks.append(TextBlock(abstract, section="Abstract"))
    for section in paper.get("full_text", []):
        name = str(section.get("section_name", "Body")).strip() or "Body"
        for paragraph in section.get("paragraphs", []):
            text = str(paragraph).strip()
            if text:
                # Preserve the paper's paragraph boundaries.  The shared
                # chunker may further split exceptionally long paragraphs.
                blocks.append(TextBlock(text, section=name))
    return blocks


def evidence_hit(content: str, gold_evidence: list[str]) -> bool:
    candidate = normalize(content)
    if not candidate:
        return False
    for evidence in gold_evidence:
        if evidence in candidate or candidate in evidence:
            return True
        evidence_tokens = set(re.findall(r"[a-z0-9]+", evidence))
        if len(evidence_tokens) < 8:
            continue
        overlap = len(evidence_tokens & set(re.findall(r"[a-z0-9]+", candidate)))
        # Long evidence can span a chunk boundary.  A high token-coverage
        # threshold credits that split without treating a keyword collision as
        # a hit; the report names this an evidence-recall proxy accordingly.
        if overlap / len(evidence_tokens) >= 0.72:
            return True
    return False


def select_cases(data: dict, max_papers: int, max_queries: int, seed: int) -> list[tuple[str, dict, dict, list[str]]]:
    eligible_papers = [
        (paper_id, paper)
        for paper_id, paper in data.items()
        if any(answerable_evidence(question) for question in paper.get("qas", []))
    ]
    rng = random.Random(seed)
    rng.shuffle(eligible_papers)
    selected: list[tuple[str, dict, dict, list[str]]] = []
    for paper_id, paper in eligible_papers[:max_papers]:
        questions = list(paper.get("qas", []))
        rng.shuffle(questions)
        for question in questions:
            evidence = answerable_evidence(question)
            if evidence:
                selected.append((paper_id, paper, question, evidence))
                if len(selected) >= max_queries:
                    return selected
    return selected


def evaluate(
    data_path: Path,
    provider: str,
    max_papers: int,
    max_queries: int,
    seed: int,
    reranker_provider: str = "none",
    reranker_device: str = "auto",
) -> dict:
    data = json.loads(data_path.read_text(encoding="utf-8"))
    cases = select_cases(data, max_papers, max_queries, seed)
    if not cases:
        raise ValueError("No answerable QASPER cases selected")

    strategies = ["lexical", "dense", "hybrid"]
    if reranker_provider == "bge":
        strategies.append("hybrid_bge_rerank")
    results: dict[str, list[dict]] = {strategy: [] for strategy in strategies}
    grouped: dict[str, list[tuple[str, dict, dict, list[str]]]] = {}
    for case in cases:
        grouped.setdefault(case[0], []).append(case)

    with TemporaryDirectory() as directory:
        # Model construction is deliberately outside the paper loop.  The
        # benchmark needs isolated *documents*, not repeated model downloads
        # or GPU weight loads.  Deleting each paper after its questions keeps
        # candidate passages isolated while preserving realistic warm-model
        # latency for the report.
        base = Settings.from_env()
        settings = Settings(
            db_path=str(Path(directory) / "qasper.db"),
            embedding_provider=provider,
            fastembed_model=base.fastembed_model,
            fastembed_cache_dir=base.fastembed_cache_dir,
            reranker_provider="none",
            reranker_model=base.reranker_model,
            reranker_cache_dir=base.reranker_cache_dir,
            reranker_device=reranker_device,
            reranker_candidates=base.reranker_candidates,
        )
        service = ResearchFlowService(settings)
        baseline_retriever = service.retriever
        reranked_retriever = None
        if reranker_provider == "bge":
            reranker = build_reranker(Settings(
                reranker_provider="bge",
                reranker_model=base.reranker_model,
                reranker_cache_dir=base.reranker_cache_dir,
                reranker_device=reranker_device,
            ))
            reranked_retriever = HybridRetriever(
                service.db,
                service.retriever.embeddings,
                reranker,
                base.reranker_candidates,
            )

        for paper_id, paper_cases in grouped.items():
            paper = paper_cases[0][1]
            # One isolated temporary index per paper is intentional: QASPER's
            # official task provides the current paper and tests evidence
            # retrieval inside it, rather than corpus-level source selection.
            blocks = paper_blocks(paper)
            document_id, _chunk_count = service.ingest_parsed(ParsedDocument(
                title=str(paper.get("title", paper_id)),
                source="qasper-dev",
                filename=f"{paper_id}.json",
                media_type="application/json",
                content="\n\n".join(block.content for block in blocks),
                blocks=blocks,
            ))
            for _paper_id, _paper, question, evidence in paper_cases:
                for strategy in strategies:
                    started = perf_counter()
                    retriever = reranked_retriever if strategy == "hybrid_bge_rerank" else baseline_retriever
                    if retriever is None:  # pragma: no cover - guarded by strategies
                        raise RuntimeError("reranked strategy requested without a reranker")
                    retrieval_strategy = "hybrid" if strategy == "hybrid_bge_rerank" else strategy
                    ranking = retriever.search(question["question"], top_k=4, strategy=retrieval_strategy)
                    elapsed_ms = (perf_counter() - started) * 1000
                    rank = next(
                        (index for index, item in enumerate(ranking, start=1) if evidence_hit(item.content, evidence)),
                        None,
                    )
                    results[strategy].append({
                        "paper_id": paper_id,
                        "question_id": question.get("question_id", ""),
                        "question": question["question"],
                        "rank": rank or 999,
                        "hit_at_1": rank == 1,
                        "hit_at_4": rank is not None,
                        "latency_ms": round(elapsed_ms, 2),
                    })
            if not service.delete_document(document_id):  # pragma: no cover - defensive cleanup
                raise RuntimeError(f"Could not clear QASPER paper {paper_id} from the temporary index")

    def summary(rows: list[dict]) -> dict:
        return {
            "queries": len(rows),
            "evidence_recall_proxy_at_1": round(mean(row["hit_at_1"] for row in rows), 4),
            "evidence_recall_proxy_at_4": round(mean(row["hit_at_4"] for row in rows), 4),
            "mrr_at_4": round(mean(1 / row["rank"] if row["rank"] <= 4 else 0 for row in rows), 4),
            "mean_latency_ms": round(mean(row["latency_ms"] for row in rows), 2),
        }

    return {
        "protocol": {
            "dataset": "QASPER v0.3 development split",
            "source": QASPER_ARCHIVE_URL,
            "subset": f"seed={seed}; up to {max_papers} full papers; {len(cases)} answerable questions",
            "task": "Within-paper retrieval over title, abstract and full-text paragraphs using each original question without a document identifier.",
            "metrics": "evidence_recall_proxy@1/@4 and MRR@4 against human-marked QASPER evidence; a 0.72 token-coverage fallback handles gold evidence spanning a chunk boundary.",
            "warning": "This evaluates long-document evidence retrieval, not cross-document source routing or answer generation.",
        },
        "embedding_provider": provider,
        "reranker": {
            "provider": reranker_provider,
            "device": reranker_device if reranker_provider != "none" else None,
        },
        "summary": {strategy: summary(rows) for strategy, rows in results.items()},
        "details": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate ResearchFlow full-text evidence retrieval on QASPER.")
    parser.add_argument("--dataset-root", type=Path, default=Path("D:/ResearchFlow-runtime/datasets"))
    parser.add_argument("--embedding-provider", choices=["hash", "fastembed"], default="fastembed")
    parser.add_argument("--reranker-provider", choices=["none", "bge"], default="none")
    parser.add_argument("--reranker-device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--max-papers", type=int, default=30)
    parser.add_argument("--max-queries", type=int, default=60)
    parser.add_argument("--seed", type=int, default=20260809)
    parser.add_argument("--output", type=Path, default=ROOT / "evals" / "results" / "qasper_fulltext_fastembed.json")
    args = parser.parse_args()
    data_path = ensure_dataset(args.dataset_root)
    report = evaluate(
        data_path,
        args.embedding_provider,
        args.max_papers,
        args.max_queries,
        args.seed,
        reranker_provider=args.reranker_provider,
        reranker_device=args.reranker_device,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
