"""Optional, generic cross-encoder reranking for retrieved text chunks."""

from __future__ import annotations

from typing import Protocol


class Reranker(Protocol):
    def score(self, query: str, passages: list[str]) -> list[float]: ...


class BGEReranker:
    """CPU cross-encoder backed by a Hugging Face BGE multilingual reranker.

    This component knows nothing about file types, document titles, reviewer
    labels, or user questions. It only scores generic (query, passage) pairs
    after first-stage retrieval has selected a bounded candidate set.
    """

    def __init__(self, model_name: str, cache_dir: str):
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as exc:  # pragma: no cover - configuration path
            raise RuntimeError(
                "BGE reranking requires the optional 'rerank' dependencies. "
                "Install with: pip install -e '.[rerank]'"
            ) from exc
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(model_name, cache_dir=cache_dir)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_name, cache_dir=cache_dir)
        self.model.eval()

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        inputs = self.tokenizer(
            [query] * len(passages),
            passages,
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        )
        with self.torch.no_grad():
            logits = self.model(**inputs, return_dict=True).logits.view(-1).float()
        return [float(value) for value in logits.tolist()]
