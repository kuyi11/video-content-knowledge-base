"""Optional cross-encoder reranking for retrieved SearchResult objects."""

from __future__ import annotations

import re
from typing import Sequence


class CrossEncoderReranker:
    """Lazy wrapper around sentence-transformers CrossEncoder.

    The model is intentionally supplied by an explicit local path so an
    evaluation run cannot silently download a large model or change the RRF
    baseline.
    """

    def __init__(self, model_path: str, *, device: str | None = None):
        if not model_path:
            raise ValueError("reranker model path must be non-empty")
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            raise RuntimeError("sentence-transformers is required for reranking") from exc
        kwargs = {"device": device} if device else {}
        self.model_path = model_path
        self.model = CrossEncoder(model_path, **kwargs)

    def rerank(self, query: str, results: Sequence, top_k: int) -> list:
        if not results:
            return []
        pairs = [(query, result.content) for result in results]
        scores = self.model.predict(pairs, show_progress_bar=False)
        ranked = sorted(
            zip(scores, results),
            key=lambda item: float(item[0]),
            reverse=True,
        )
        reranked = []
        seen_evidence = set()
        for score, result in ranked:
            evidence_key = self._evidence_key(result)
            if evidence_key in seen_evidence:
                continue
            seen_evidence.add(evidence_key)
            # SearchResult is mutable, but preserve the original object type
            # and metadata while exposing the reranker score in the report.
            result.rank = len(reranked) + 1
            result.score = float(score)
            reranked.append(result)
            if len(reranked) >= top_k:
                break
        return reranked

    @staticmethod
    def _evidence_key(result) -> tuple:
        refs = tuple(sorted(set(result.source_refs or [])))
        if refs:
            return result.video_id, refs
        normalized = re.sub(r"\s+", " ", result.content).strip().casefold()
        return result.video_id, normalized
