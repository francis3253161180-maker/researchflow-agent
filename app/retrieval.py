from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.config import Settings
from app.db import Database
from app.ingestion import TextBlock
from app.reranking import BGEReranker, Reranker


TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    return TOKEN_PATTERN.findall(text.lower())


def chunk_text(text: str, max_chars: int = 620, overlap: int = 80) -> list[str]:
    normalized = re.sub(r"\r\n?", "\n", text).strip()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", normalized) if p.strip()]

    def split_long_unit(unit: str) -> list[str]:
        """Prefer line and sentence boundaries before a character sliding window.

        PDF extraction often produces a page as one long single-newline block;
        blindly slicing it can split a claim from its metric. This remains
        format-agnostic and falls back to overlapping character windows only
        when no usable textual boundary exists.
        """
        boundaries = [part.strip() for part in re.split(r"\n+|(?<=[.!?。！？])\s+", unit) if part.strip()]
        if len(boundaries) <= 1:
            boundaries = [unit]
        pieces: list[str] = []
        current_piece = ""
        for boundary in boundaries:
            if len(boundary) > max_chars:
                if current_piece:
                    pieces.append(current_piece)
                    current_piece = ""
                start = 0
                while start < len(boundary):
                    end = min(len(boundary), start + max_chars)
                    pieces.append(boundary[start:end])
                    if end == len(boundary):
                        break
                    start = max(0, end - overlap)
                continue
            candidate = f"{current_piece}\n{boundary}".strip()
            if current_piece and len(candidate) > max_chars:
                pieces.append(current_piece)
                current_piece = boundary
            else:
                current_piece = candidate
        if current_piece:
            pieces.append(current_piece)
        return pieces

    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 1 <= max_chars:
            current = f"{current}\n{paragraph}".strip()
            continue
        if current:
            chunks.append(current)
        if len(paragraph) <= max_chars:
            current = paragraph
            continue
        chunks.extend(split_long_unit(paragraph))
        current = ""
    if current:
        chunks.append(current)
    return chunks or [normalized]


class EmbeddingProvider(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class HashEmbedding:
    """Deterministic offline embedding used for local development and tests."""

    def __init__(self, dimensions: int = 384):
        self.dimensions = dimensions

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for token in tokenize(text):
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
                value = int.from_bytes(digest, "big")
                index = value % self.dimensions
                sign = 1.0 if (value >> 8) & 1 else -1.0
                vector[index] += sign
            norm = math.sqrt(sum(v * v for v in vector)) or 1.0
            vectors.append([v / norm for v in vector])
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


class OpenAICompatibleEmbedding:
    def __init__(self, base_url: str, api_key: str, model: str):
        self.url = f"{base_url.rstrip('/')}/embeddings"
        self.api_key = api_key
        self.model = model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        response = httpx.post(
            self.url,
            headers=headers,
            json={"model": self.model, "input": texts},
            timeout=45,
        )
        response.raise_for_status()
        data = sorted(response.json()["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in data]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


class FastEmbedEmbedding:
    """CPU-first ONNX embedding provider. The model downloads only when explicitly enabled."""

    def __init__(self, model_name: str, cache_dir: str):
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:  # pragma: no cover - configuration error path
            raise RuntimeError("fastembed is not installed; reinstall project dependencies") from exc
        self.model = TextEmbedding(model_name=model_name, cache_dir=cache_dir)

    @staticmethod
    def _to_list(vectors) -> list[list[float]]:
        return [[float(value) for value in vector] for vector in vectors]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._to_list(self.model.passage_embed(texts))

    def embed_query(self, text: str) -> list[float]:
        return self._to_list(self.model.query_embed(text))[0]


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_base_url and settings.embedding_model:
        return OpenAICompatibleEmbedding(
            settings.embedding_base_url,
            settings.embedding_api_key,
            settings.embedding_model,
        )
    if settings.embedding_provider == "fastembed":
        return FastEmbedEmbedding(settings.fastembed_model, settings.fastembed_cache_dir)
    return HashEmbedding()


def build_reranker(settings: Settings) -> Reranker | None:
    if settings.reranker_provider == "bge":
        return BGEReranker(settings.reranker_model, settings.reranker_cache_dir)
    return None


def cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


@dataclass
class SearchResult:
    chunk_id: str
    document_id: str
    title: str
    source: str
    content: str
    score: float
    page: int | None = None
    section: str | None = None
    filename: str = ""

    def as_dict(self) -> dict[str, str | float]:
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "title": self.title,
            "source": self.source,
            "content": self.content,
            "score": round(self.score, 6),
            "page": self.page,
            "section": self.section,
            "filename": self.filename,
        }


@dataclass(frozen=True)
class ChunkRecord:
    content: str
    page: int | None = None
    section: str | None = None


def chunk_blocks(blocks: list[TextBlock]) -> list[ChunkRecord]:
    records: list[ChunkRecord] = []
    for block in blocks:
        for content in chunk_text(block.content):
            if content:
                # A long Markdown section (for example, an OpenReview review) is
                # split into several chunks. Repeating its heading preserves the
                # reviewer / experiment / method identity for every chunk, instead
                # of making only the first chunk retrievable by that identity.
                contextual_content = (
                    f"[Section: {block.section}]\n{content}" if block.section else content
                )
                records.append(ChunkRecord(content=contextual_content, page=block.page, section=block.section))
    return records


class HybridRetriever:
    def __init__(self, db: Database, embeddings: EmbeddingProvider, reranker: Reranker | None = None, reranker_candidates: int = 20):
        self.db = db
        self.embeddings = embeddings
        self.reranker = reranker
        self.reranker_candidates = max(1, reranker_candidates)

    def ingest(
        self,
        title: str,
        source: str,
        content: str,
        blocks: list[TextBlock] | None = None,
        filename: str = "",
        media_type: str = "text/plain",
    ) -> tuple[str, int]:
        document_id = self.db.add_document(title, source, content, filename=filename, media_type=media_type)
        records = chunk_blocks(blocks or [TextBlock(content)])
        vectors = self.embeddings.embed_documents([record.content for record in records])
        count = self.db.add_chunks(
            document_id,
            (
                (position, record.content, vector, record.page, record.section)
                for position, (record, vector) in enumerate(zip(records, vectors))
            ),
        )
        return document_id, count

    def search(self, query: str, top_k: int = 4, strategy: str = "hybrid") -> list[SearchResult]:
        """Search with lexical, dense, or reciprocal-rank-fused ranking.

        ``strategy`` exists primarily to make evaluation comparisons honest:
        the production default remains ``hybrid`` and no caller silently swaps
        algorithms under the same metric.
        """
        if strategy not in {"lexical", "dense", "hybrid"}:
            raise ValueError("strategy must be lexical, dense, or hybrid")
        chunks = self.db.list_chunks()
        if not chunks:
            return []
        query_tokens = tokenize(query)
        query_counts = Counter(query_tokens)
        tokenized = [tokenize(chunk["content"]) for chunk in chunks]
        document_frequency = Counter()
        for tokens in tokenized:
            document_frequency.update(set(tokens))
        average_length = sum(map(len, tokenized)) / max(len(tokenized), 1)
        n_docs = len(chunks)
        lexical_scores: list[float] = []
        for tokens in tokenized:
            counts = Counter(tokens)
            score = 0.0
            for token, qtf in query_counts.items():
                df = document_frequency.get(token, 0)
                idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
                tf = counts.get(token, 0)
                denominator = tf + 1.5 * (1 - 0.75 + 0.75 * len(tokens) / max(average_length, 1))
                score += qtf * idf * (tf * 2.5 / denominator if denominator else 0.0)
            lexical_scores.append(score)

        query_vector = self.embeddings.embed_query(query)
        vector_scores = [cosine(query_vector, chunk["embedding"]) for chunk in chunks]
        lexical_rank = sorted(range(n_docs), key=lambda i: lexical_scores[i], reverse=True)
        vector_rank = sorted(range(n_docs), key=lambda i: vector_scores[i], reverse=True)
        lexical_position = {index: rank for rank, index in enumerate(lexical_rank, start=1)}
        vector_position = {index: rank for rank, index in enumerate(vector_rank, start=1)}
        fused = [
            1 / (60 + lexical_position[i]) + 1 / (60 + vector_position[i])
            for i in range(n_docs)
        ]
        scores = {
            "lexical": lexical_scores,
            "dense": vector_scores,
            "hybrid": fused,
        }[strategy]
        ranked = sorted(range(n_docs), key=lambda i: scores[i], reverse=True)

        if self.reranker and strategy == "hybrid":
            candidate_indices = ranked[: max(top_k, self.reranker_candidates)]
            reranker_scores = self.reranker.score(query, [chunks[i]["content"] for i in candidate_indices])
            ranked = [
                index
                for _, index in sorted(
                    zip(reranker_scores, candidate_indices), key=lambda pair: pair[0], reverse=True
                )
            ]
            scores = [reranker_scores[candidate_indices.index(index)] for index in ranked]
            return [
                SearchResult(
                    chunk_id=chunks[index]["id"], document_id=chunks[index]["document_id"],
                    title=chunks[index]["title"], source=chunks[index]["source"], content=chunks[index]["content"],
                    score=scores[position], page=chunks[index].get("page"), section=chunks[index].get("section"),
                    filename=chunks[index].get("filename", ""),
                )
                for position, index in enumerate(ranked[:top_k])
            ]
        ranked = ranked[:top_k]
        return [
            SearchResult(
                chunk_id=chunks[i]["id"],
                document_id=chunks[i]["document_id"],
                title=chunks[i]["title"],
                source=chunks[i]["source"],
                content=chunks[i]["content"],
                score=scores[i],
                page=chunks[i].get("page"),
                section=chunks[i].get("section"),
                filename=chunks[i].get("filename", ""),
            )
            for i in ranked
        ]
