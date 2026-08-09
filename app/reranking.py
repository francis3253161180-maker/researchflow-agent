"""Optional, generic cross-encoder reranking for retrieved text chunks."""

from __future__ import annotations

import os
from typing import Protocol


# Some Hugging Face mirrors route large model files through Xet. Disabling it
# keeps this optional local component on the normal HTTP cache path; cache_dir
# still controls where the model is stored.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")


class Reranker(Protocol):
    def score(self, query: str, passages: list[str]) -> list[float]: ...


class BGEReranker:
    """Device-configurable cross-encoder backed by a BGE multilingual reranker.

    This component knows nothing about file types, document titles, reviewer
    labels, or user questions. It only scores generic (query, passage) pairs
    after first-stage retrieval has selected a bounded candidate set.
    """

    def __init__(self, model_name: str, cache_dir: str, device: str = "auto"):
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as exc:  # pragma: no cover - configuration path
            raise RuntimeError(
                "BGE reranking requires the optional 'rerank' dependencies. "
                "Install with: pip install -e '.[rerank]'"
            ) from exc
        self.torch = torch
        self.device = "cuda" if device == "auto" and torch.cuda.is_available() else device
        if self.device not in {"cpu", "cuda"}:
            raise ValueError("reranker device must be auto, cpu, or cuda")
        if self.device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("RERANKER_DEVICE=cuda requested but CUDA is unavailable")
        self.tokenizer = AutoTokenizer.from_pretrained(model_name, cache_dir=cache_dir)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_name, cache_dir=cache_dir)
        self.model.to(self.device)
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
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        with self.torch.no_grad():
            logits = self.model(**inputs, return_dict=True).logits.view(-1).float()
        return [float(value) for value in logits.tolist()]
