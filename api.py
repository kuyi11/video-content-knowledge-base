"""FastAPI interface for structured RAG answers and evidence logs.

Run locally with ``uv run --no-sync uvicorn api:app --reload``.  The embedding
model and persisted index are loaded lazily on the first query or health check.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from config import (
    API_MAX_CONCURRENT_QUERIES,
    API_RATE_LIMIT_PER_MINUTE,
    API_TOKEN,
    LLM_INPUT_COST_PER_1K,
    LLM_OUTPUT_COST_PER_1K,
    BGE_MODEL_PATH,
    INDEX_DIR,
    TEMP_DIR,
)
from modules import agent
from modules.embedding_runtime import SentenceTransformer
from modules.indexer import HybridIndex
from modules.query_engine import QueryEngine
from modules.vault_loader import VaultLoader
from config import EXTERNAL_VAULT_PATHS, VAULT_DIR

logger = logging.getLogger(__name__)


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
                (VAULT_DIR, *EXTERNAL_VAULT_PATHS),
                engine.index.model,
                embed_segments=False,
                temp_dir=TEMP_DIR,
            ).load_all()
            report = engine.index.health_check(documents)
            report["service"] = "ready"
            return report
        except Exception as exc:
            return {"status": "unavailable", "service": "error", "error": str(exc)}


service = RagService()
app = FastAPI(title="Video Content Knowledge Base", version="0.1.0")
MAX_FILTER_FIELDS = 8
MAX_FILTER_VALUES = 32
_query_slots = threading.BoundedSemaphore(API_MAX_CONCURRENT_QUERIES)
_rate_lock = threading.Lock()
_request_times: dict[str, deque[float]] = defaultdict(deque)
_metrics_lock = threading.Lock()
_metrics = {
    "total_queries": 0,
    "successful_queries": 0,
    "failed_queries": 0,
    "blocked_queries": 0,
    "total_latency_ms": 0.0,
    "latencies_ms": deque(maxlen=1000),
    "estimated_input_tokens": 0,
    "estimated_output_tokens": 0,
    "estimated_cost_usd": 0.0,
}


def _estimate_tokens(text: str) -> int:
    """Conservative operational estimate; exact tokenizer usage is unavailable."""
    return max(0, (len(text or "") + 3) // 4)


def _record_query_metrics(*, latency_ms: float, success: bool, blocked: bool,
                          input_text: str, output_text: str) -> None:
    input_tokens = _estimate_tokens(input_text)
    output_tokens = _estimate_tokens(output_text)
    cost = (input_tokens / 1000) * LLM_INPUT_COST_PER_1K
    cost += (output_tokens / 1000) * LLM_OUTPUT_COST_PER_1K
    with _metrics_lock:
        _metrics["total_queries"] += 1
        _metrics["successful_queries"] += int(success)
        _metrics["failed_queries"] += int(not success)
        _metrics["blocked_queries"] += int(blocked)
        _metrics["total_latency_ms"] += latency_ms
        _metrics["latencies_ms"].append(latency_ms)
        _metrics["estimated_input_tokens"] += input_tokens
        _metrics["estimated_output_tokens"] += output_tokens
        _metrics["estimated_cost_usd"] += cost


def _metrics_snapshot() -> dict[str, Any]:
    with _metrics_lock:
        values = list(_metrics["latencies_ms"])
        total = _metrics["total_queries"]
        ordered = sorted(values)
        def percentile(p: float) -> float | None:
            if not ordered:
                return None
            index = min(len(ordered) - 1, int(round((len(ordered) - 1) * p)))
            return round(ordered[index], 2)
        return {
            "total_queries": total,
            "successful_queries": _metrics["successful_queries"],
            "failed_queries": _metrics["failed_queries"],
            "blocked_queries": _metrics["blocked_queries"],
            "latency_ms": {
                "p50": percentile(0.50),
                "p95": percentile(0.95),
                "average": round(_metrics["total_latency_ms"] / total, 2) if total else None,
                "sample_size": len(values),
            },
            "estimated_input_tokens": _metrics["estimated_input_tokens"],
            "estimated_output_tokens": _metrics["estimated_output_tokens"],
            "estimated_cost_usd": round(_metrics["estimated_cost_usd"], 6),
            "cost_rate_configured": bool(LLM_INPUT_COST_PER_1K or LLM_OUTPUT_COST_PER_1K),
            "cost_note": "Token and cost values are estimates based on character length, not provider billing.",
        }


def _check_rate_limit(client_host: str | None) -> None:
    """Apply a small process-local sliding-window limit at the API boundary."""
    if API_RATE_LIMIT_PER_MINUTE <= 0:
        return
    key = client_host or "unknown"
    now = time.monotonic()
    cutoff = now - 60.0
    with _rate_lock:
        recent = _request_times[key]
        while recent and recent[0] <= cutoff:
            recent.popleft()
        if len(recent) >= API_RATE_LIMIT_PER_MINUTE:
            raise HTTPException(
                status_code=429,
                detail="query rate limit exceeded",
                headers={"Retry-After": "60"},
            )
        recent.append(now)


def _check_api_token(authorization: str | None, client_host: str | None) -> None:
    if API_TOKEN and authorization != f"Bearer {API_TOKEN}":
        raise HTTPException(status_code=401, detail="invalid or missing API token")
    if not API_TOKEN and client_host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=401, detail="API_TOKEN is required for non-local clients")


def _public_retrieval(retrieval: dict) -> dict:
    """Remove local paths and source URLs before returning evidence to clients."""
    public_results = []
    for item in retrieval.get("results", []):
        public_item = dict(item)
        public_item.pop("source_path", None)
        public_item.pop("raw_source_path", None)
        public_item.pop("source_url", None)
        public_results.append(public_item)
    return {**retrieval, "results": public_results}


def _validate_query_filters(filters: dict[str, Any]) -> None:
    unknown = sorted(set(filters) - HybridIndex.FILTER_FIELDS)
    if unknown:
        raise HTTPException(status_code=400, detail=f"unsupported metadata filter: {unknown[0]}")
    if len(filters) > MAX_FILTER_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"too many metadata filters (maximum {MAX_FILTER_FIELDS})",
        )
    for field, value in filters.items():
        if isinstance(value, list) and len(value) > MAX_FILTER_VALUES:
            raise HTTPException(
                status_code=400,
                detail=f"too many values for metadata filter: {field}",
            )


@app.get("/health")
def health(request: Request, authorization: str | None = Header(default=None)) -> dict:
    _check_api_token(authorization, request.client.host if request.client else None)
    report = service.health()
    if report.get("service") != "ready":
        raise HTTPException(status_code=503, detail={"status": "unavailable", "service": "error"})
    return report


@app.get("/metrics")
def metrics(request: Request, authorization: str | None = Header(default=None)) -> dict:
    """Return process-local operational metrics without query/evidence content."""
    _check_api_token(authorization, request.client.host if request.client else None)
    return {"service": "ready", "metrics": _metrics_snapshot()}


@app.post("/query")
def query(
    request: QueryRequest,
    raw_request: Request,
    authorization: str | None = Header(default=None),
) -> dict:
    request_id = uuid.uuid4().hex
    client_host = raw_request.client.host if raw_request.client else None
    _check_api_token(authorization, client_host)
    _check_rate_limit(client_host)
    _validate_query_filters(request.filters)
    if not _query_slots.acquire(blocking=False):
        raise HTTPException(status_code=503, detail="query capacity temporarily exhausted")
    started = time.perf_counter()
    success = False
    blocked = False
    answer = ""
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
        success = usage.get("retrieval", {}).get("status") != "error"
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("query service failed request_id=%s", request_id)
        raise HTTPException(status_code=503, detail="query service unavailable") from exc
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        gate = usage.get("output_gate", {}) if "usage" in locals() else {}
        blocked = gate.get("action", gate.get("mode")) == "block"
        _record_query_metrics(
            latency_ms=elapsed_ms,
            success=success,
            blocked=blocked,
            input_text=request.question,
            output_text=answer,
        )
        _query_slots.release()
        logger.info(
            "query completed request_id=%s client=%s latency_ms=%.2f",
            request_id,
            client_host or "unknown",
            elapsed_ms,
        )

    retrieval = usage.get("retrieval", {})
    if retrieval.get("status") == "error":
        raise HTTPException(status_code=503, detail="retrieval service unavailable")
    grounding = usage.get("claim_grounding", {})
    output_gate = usage.get("output_gate") or {}
    evidence_audit = dict(usage.get("evidence_audit") or {})
    evidence_audit.pop("record_path", None)
    cited_ids = set(
        output_gate.get(
            "allowed_cited_chunk_ids",
            grounding.get("valid_cited_chunk_ids", []),
        )
    )
    citations = [
        {
            "chunk_id": item.get("chunk_id"),
            "source_refs": item.get("source_refs", []),
            "video_id": item.get("video_id", ""),
            "start": item.get("start"),
            "end": item.get("end"),
        }
        for item in retrieval.get("results", [])
        if item.get("chunk_id") in cited_ids
    ]
    return {
        "request_id": request_id,
        "answer": answer,
        "citations": citations,
        "claim_grounding": grounding,
        "output_gate": output_gate or None,
        "evidence_audit": evidence_audit or None,
        "safety_gate": usage.get("safety_gate"),
        "answer_confidence": usage.get("answer_confidence"),
        "evidence_coverage": usage.get("evidence_coverage"),
        "evidence_conflicts": usage.get("evidence_conflicts"),
        "retrieval": _public_retrieval(retrieval),
        "query_log_path": None,
    }
