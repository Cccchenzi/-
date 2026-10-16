"""Dense semantic retrieval, offline TF-IDF and a vector-store extension point.

Model-enabled modes use DenseVectorStore; explicit local mode uses
LocalTfidfStore. A future Chroma adapter must implement VectorStore.search and
return the same RankedChunk values. No Chroma implementation is bundled.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence

from . import providers


STOP_WORDS = {
    "the", "a", "an", "and", "or", "is", "are", "to", "of", "in", "for",
    "what", "how", "why", "can", "does", "do", "please", "with", "this", "that",
    "什么", "怎么", "如何", "哪些", "为什么", "多少", "请问", "一下", "可以",
    "这个", "那个", "我们", "你们", "他们", "是否", "有关", "关于", "介绍",
}


def tokenize(text: str) -> list[str]:
    """English words + Chinese bigrams preserve useful bilingual search terms."""
    normalized = text.lower()
    words = [word for word in re.findall(r"[a-z0-9_]+", normalized) if word not in STOP_WORDS]
    for segment in re.findall(r"[\u3400-\u9fff]+", normalized):
        if len(segment) == 1:
            if segment not in "的了吗呢是在与和或请我你它":
                words.append(segment)
        else:
            words.extend(segment[i:i + 2] for i in range(len(segment) - 1)
                         if segment[i:i + 2] not in STOP_WORDS)
    return words


def encode_token_counts(text: str) -> str:
    """Persist a sparse term-frequency vector at ingestion, without a model."""
    return json.dumps(Counter(tokenize(text)), ensure_ascii=False, separators=(",", ":"))


def _stored_counts(chunk: Mapping) -> Counter:
    """Read saved vectors; pre-migration rows still work without re-uploading."""
    raw = chunk.get("token_counts_json", "{}")
    try:
        counts = json.loads(raw)
        if isinstance(counts, dict) and counts and all(
            isinstance(term, str) and isinstance(count, int) and not isinstance(count, bool) and count > 0
            for term, count in counts.items()
        ):
            return Counter(counts)
    except (ValueError, TypeError):
        pass
    return Counter(tokenize(str(chunk["content"])))


@dataclass(frozen=True)
class RankedChunk:
    chunk: Mapping
    score: float


class VectorStore(Protocol):
    def search(self, query: str, chunks: Sequence[Mapping], limit: int = 5) -> list[RankedChunk]:
        """Return relevant chunks, best first; never return cross-library rows."""
        ...


class LocalTfidfStore:
    """Actual normalized TF-IDF vectors and cosine similarity, entirely local.

    Sparse term-frequency vectors are stored at upload. The corpus IDF weights
    are recomputed per request so uploads/deletions take effect without a stale
    index. This deliberately favors simplicity for a classroom dataset.
    """

    def search(self, query: str, chunks: Sequence[Mapping], limit: int = 5) -> list[RankedChunk]:
        if not chunks:
            return []
        terms = tokenize(query)
        if not terms:
            return []
        documents = [_stored_counts(chunk) for chunk in chunks]
        document_frequency: Counter = Counter()
        for document in documents:
            document_frequency.update(document.keys())
        vocabulary = set(document_frequency)
        query_terms = Counter(term for term in terms if term in vocabulary)
        if not query_terms:
            return []
        # Questions with only accidental overlap should not retrieve arbitrary text.
        query_coverage = len(query_terms) / max(1, len(set(terms)))
        if query_coverage < 0.12:
            return []
        count = len(documents)
        idf = {term: math.log((1 + count) / (1 + frequency)) + 1
               for term, frequency in document_frequency.items()}
        query_vector = {term: (1 + math.log(frequency)) * idf[term]
                        for term, frequency in query_terms.items()}
        query_norm = math.sqrt(sum(value * value for value in query_vector.values()))
        ranked = []
        for chunk, term_counts in zip(chunks, documents):
            common = set(query_terms) & set(term_counts)
            if not common:
                continue
            vector = {term: (1 + math.log(frequency)) * idf[term]
                      for term, frequency in term_counts.items()}
            norm = math.sqrt(sum(value * value for value in vector.values()))
            score = sum(query_vector[term] * vector[term] for term in common) / (query_norm * norm)
            if score >= 0.07:
                ranked.append(RankedChunk(chunk=chunk, score=score))
        ranked.sort(key=lambda item: item.score, reverse=True)
        if not ranked:
            return []
        # Relative cutoff limits unrelated low-scoring tails in long libraries.
        return [item for item in ranked if item.score >= max(0.07, ranked[0].score * 0.35)][:limit]


class DenseVectorStore:
    """Persisted semantic embeddings with owner-scoped cosine search.

    The caller supplies ONLY rows of the selected user's library. A Chroma
    adapter can replace this object via VECTOR_STORE while retaining the same
    contract and must enforce equivalent owner/library metadata filters.
    """
    def __init__(self, config):
        self.config = config

    def search(self, query: str, chunks: Sequence[Mapping], limit: int = 6) -> list[RankedChunk]:
        if not chunks:
            return []
        fingerprint = providers.embedding_fingerprint(self.config)
        ready = [chunk for chunk in chunks if chunk.get("embedding_status") == "ready"
                 and chunk.get("embedding_fingerprint") == fingerprint]
        if not ready:
            raise providers.ProviderError("当前知识库没有可用的语义索引，请配置嵌入模型后重建索引。")
        model = providers.embedding_settings(self.config)["model"].lower()
        embedding_query = ("Instruct: Given a user question, retrieve relevant personal documents.\nQuery: " + query
                           if "qwen3-embedding" in model else query)
        query_vector = providers.normalize_vectors(providers.embed_texts([embedding_query], self.config), 1)[0]
        ranked = []
        invalid = 0
        for chunk in ready:
            try:
                vector = providers.normalize_vectors([json.loads(chunk["embedding_json"])], 1)[0]
                if len(vector) != len(query_vector):
                    raise providers.ProviderError("嵌入维度已变化，请重建索引。")
            except (providers.ProviderError, ValueError, TypeError, KeyError):
                invalid += 1
                continue
            score = max(-1.0, min(1.0, sum(left * right for left, right in zip(query_vector, vector))))
            if score >= float(self.config.get("MIN_EMBEDDING_SCORE", 0.2)):
                ranked.append(RankedChunk(chunk, score))
        if invalid == len(ready):
            raise providers.ProviderError("已保存的语义向量无效或维度改变，请重建索引。")
        ranked.sort(key=lambda result: result.score, reverse=True)
        # A modest per-document cap leaves room for evidence across documents.
        selected, per_document = [], Counter()
        for match in ranked:
            if per_document[match.chunk["document_id"]] >= 3:
                continue
            selected.append(match)
            per_document[match.chunk["document_id"]] += 1
            if len(selected) >= limit:
                break
        return selected
