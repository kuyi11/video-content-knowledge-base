"""FastAPI interface for structured RAG answers and evidence logs.

Run locally with ``uv run --no-sync uvicorn api:app --reload``.  The embedding
model and persisted index are loaded lazily on the first query or health check.
"""

from __future__ import annotations

import threading
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from config import BGE_MODEL_PATH, INDEX_DIR
from modules import agent
from modules.embedding_runtime import SentenceTransformer
from modules.indexer import HybridIndex
from modules.query_engine import QueryEngine
from modules.vault_loader import VaultLoader
from config import EXTERNAL_VAULT_PATHS, VAULT_DIR


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=10)
    filters: dict[str, Any] = Field(default_factory=dict)
    rewrite: bool | None = None
    semantic_grounding: bool = False


class RagService:
    def __init__(self) -> None:
        self._engine: QueryEngine | None = None
        self._error: str | None = None
        self._lock = threading.Lock()

    def engine(self) -> QueryEngine:
        if self._engine is not None:
            return self._engine
        with self._lock:
            if self._engine is not None:
                return self._engine
            try:
                import torch

                device = "cuda" if torch.cuda.is_available() else "cpu"
                model = SentenceTransformer(BGE_MODEL_PATH, device=device)
                index = HybridIndex(INDEX_DIR, model=model)
                if index.faiss_index is None:
                    raise RuntimeError("persisted index is unavailable; run tools/rebuild_index.py")
                self._engine = QueryEngine(index)
                self._error = None
            except Exception as exc:
                self._error = str(exc)
                raise
        return self._engine

    def health(self) -> dict:
        try:
            engine = self.engine()
            documents = VaultLoader(
                (VAULT_DIR, *EXTERNAL_VAULT_PATHS), engine.index.model, embed_segments=False
            ).load_all()
            report = engine.index.health_check(documents)
            report["service"] = "ready"
            return report
        except Exception as exc:
            return {"status": "unavailable", "service": "error", "error": str(exc)}


service = RagService()
app = FastAPI(title="Video Content Knowledge Base", version="0.1.0")


@app.get("/health")
def health() -> dict:
    report = service.health()
    if report.get("service") != "ready":
        raise HTTPException(status_code=503, detail=report)
    return report


@app.post("/query")
def query(request: QueryRequest) -> dict:
    try:
        answer, usage = agent.answer_question_with_usage(
            service.engine(),
            request.question,
            top_k=request.top_k,
            filters=request.filters or None,
            rewrite=request.rewrite,
            include_retrieval=True,
            semantic_grounding=request.semantic_grounding,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    retrieval = usage.get("retrieval", {})
    grounding = usage.get("claim_grounding", {})
    cited_ids = set(grounding.get("valid_cited_chunk_ids", []))
    citations = [
        {
            "chunk_id": item.get("chunk_id"),
            "source_refs": item.get("source_refs", []),
            "source_path": item.get("source_path", ""),
            "video_id": item.get("video_id", ""),
            "start": item.get("start"),
            "end": item.get("end"),
        }
        for item in retrieval.get("results", [])
        if item.get("chunk_id") in cited_ids
    ]
    return {
        "answer": answer,
        "citations": citations,
        "claim_grounding": grounding,
        "safety_gate": usage.get("safety_gate"),
        "retrieval": retrieval,
        "query_log_path": usage.get("retrieval_log_path"),
    }
