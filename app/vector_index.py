from __future__ import annotations

from threading import RLock
from typing import Any

import faiss
import numpy as np


class FaissVectorIndex:
    """In-memory cosine index rebuilt from SQLite's persisted chunk vectors.

    SQLite remains the source of truth for document/chunk metadata and vectors.
    FAISS owns only the disposable search structure and its row-to-chunk mapping.
    """

    def __init__(self) -> None:
        self._index: faiss.IndexFlatIP | None = None
        self._chunk_ids: list[str] = []
        self._dimension = 0
        self._lock = RLock()

    @property
    def size(self) -> int:
        return len(self._chunk_ids)

    @property
    def dimension(self) -> int:
        return self._dimension

    def rebuild(self, chunks: list[dict[str, Any]]) -> None:
        with self._lock:
            if not chunks:
                self._index = None
                self._chunk_ids = []
                self._dimension = 0
                return
            vectors = np.asarray([chunk["embedding"] for chunk in chunks], dtype="float32")
            if vectors.ndim != 2 or vectors.shape[1] == 0:
                raise ValueError("chunk embeddings must form a non-empty two-dimensional matrix")
            faiss.normalize_L2(vectors)
            index = faiss.IndexFlatIP(int(vectors.shape[1]))
            index.add(vectors)
            self._index = index
            self._chunk_ids = [str(chunk["id"]) for chunk in chunks]
            self._dimension = int(vectors.shape[1])

    def search(self, query_vector: list[float], top_k: int | None = None) -> list[tuple[str, float]]:
        with self._lock:
            if self._index is None or not self._chunk_ids:
                return []
            query = np.asarray([query_vector], dtype="float32")
            if query.ndim != 2 or query.shape[1] != self._dimension:
                raise ValueError(
                    f"query embedding dimension {query.shape[1] if query.ndim == 2 else 0} "
                    f"does not match FAISS index dimension {self._dimension}"
                )
            faiss.normalize_L2(query)
            limit = min(max(1, top_k or self.size), self.size)
            scores, indices = self._index.search(query, limit)
            return [
                (self._chunk_ids[int(index)], float(score))
                for index, score in zip(indices[0], scores[0])
                if index >= 0
            ]
