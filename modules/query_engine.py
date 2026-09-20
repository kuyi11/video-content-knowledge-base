import time
import re
import copy
from collections import OrderedDict
import json

from config import (
    QUERY_REWRITE_ENABLED,
    QUERY_REWRITE_PATH,
    RERANKER_CANDIDATE_K,
    RERANKER_DEVICE,
    RERANKER_MODEL_PATH,
    QUERY_CACHE_ENABLED,
    QUERY_CACHE_SIZE,
    QUERY_MULTI_HOP_ENABLED,
    QUERY_MULTI_HOP_MAX_HOPS,
)
from modules.indexer import HybridIndex
from modules.indexer import SearchResult
from modules.evidence_model import evidence_fingerprint
from modules.query_rewriter import QueryRewriter
from modules.query_planner import QueryPlanner
from modules.reranker import CrossEncoderReranker


class QueryEngine:
    MAX_TEXT_LEN = 600
    MAX_TOP_K = 10
    MAX_SUBQUERIES = 3

    def __init__(
        self,
        index: HybridIndex,
        rewriter: QueryRewriter | None = None,
        reranker=None,
        reranker_candidate_k: int | None = None,
        planner: QueryPlanner | None = None,
        multi_hop_enabled: bool = QUERY_MULTI_HOP_ENABLED,
        max_hops: int = QUERY_MULTI_HOP_MAX_HOPS,
    ):
        self.index = index
        self.rewriter = rewriter or QueryRewriter.from_path(QUERY_REWRITE_PATH)
        if reranker is None and RERANKER_MODEL_PATH:
            reranker = CrossEncoderReranker(RERANKER_MODEL_PATH, device=RERANKER_DEVICE)
        self.reranker = reranker
        self.reranker_candidate_k = max(
            5, reranker_candidate_k or RERANKER_CANDIDATE_K
        )
        self.planner = planner or QueryPlanner(self.rewriter)
        self.multi_hop_enabled = multi_hop_enabled
        self.max_hops = max(1, min(3, max_hops))
        self.cache_enabled = QUERY_CACHE_ENABLED and QUERY_CACHE_SIZE > 0
        self._cache: OrderedDict[tuple, dict] = OrderedDict()

    def query(
        self,
        question: str,
        top_k: int = 5,
        *,
        filters: dict | None = None,
        rewrite: bool | None = None,
        semantic_grounding: bool = False,
    ) -> dict:
        question = question.strip()
        generation = self._index_generation_id()
        if not question:
            return {
                "question": question,
                "status": "empty",
                "results": [],
                "meta": {
                    "top_k": top_k,
                    "result_count": 0,
                    "error": "Empty question",
                    "index_generation_id": generation,
                },
            }
        if top_k < 1:
            raise ValueError(f"top_k must be >= 1, got {top_k}")
        top_k = min(top_k, self.MAX_TOP_K)

        cache_key = self._cache_key(question, top_k, filters, rewrite, semantic_grounding)
        if self.cache_enabled and cache_key in self._cache:
            cached = copy.deepcopy(self._cache.pop(cache_key))
            self._cache[cache_key] = copy.deepcopy(cached)
            cached.setdefault("meta", {})["cache_hit"] = True
            return cached

        started = time.perf_counter()
        try:
            rewrite = QUERY_REWRITE_ENABLED if rewrite is None else rewrite
            plan = self.planner.plan(question, rewrite=rewrite is not False)
            sub_queries = list(plan.sub_queries)
            retrieval_queries = sub_queries
            retrieval_query = " || ".join(retrieval_queries)
            candidate_k = self.reranker_candidate_k if self.reranker else top_k
            candidate_results = []
            coverage = []
            hop_trace = []
            matched_sub_queries = {}
            evidence_by_query = {}
            semantic_multi_hop = (
                semantic_grounding
                and self.multi_hop_enabled
                and bool(getattr(self.planner, "enabled", False))
            )
            for sub_query, retrieval_sub_query in zip(sub_queries, retrieval_queries):
                sub_results = self.index.query(
                    retrieval_sub_query, top_k=candidate_k, filters=filters
                )
                candidate_results.extend(sub_results)
                evidence_by_query[sub_query] = list(sub_results)
                for sub_result in sub_results:
                    matched_sub_queries.setdefault(sub_result.chunk_id, set()).add(sub_query)
                coverage.append({
                    "query": sub_query,
                    "retrieval_query": retrieval_sub_query,
                    "status": "covered" if sub_results else "missing",
                    "result_count": len(sub_results),
                })
                if sub_results and semantic_multi_hop:
                    try:
                        coverage[-1].update(
                            self.planner.judge_evidence_coverage(sub_query, sub_results)
                        )
                        coverage[-1]["semantic"] = True
                    except Exception as exc:
                        coverage[-1]["semantic_error"] = str(exc)
                hop_trace.append({
                    "hop": 1,
                    "query": retrieval_sub_query,
                    "evidence_need": sub_query,
                    "result_count": len(sub_results),
                })
            attempted_queries = list(retrieval_queries)
            if self.multi_hop_enabled and self.max_hops > 1:
                for hop in range(2, self.max_hops + 1):
                    missing = [item for item in coverage if item["status"] != "covered"]
                    if not missing:
                        break
                    made_progress = False
                    for item in missing:
                        try:
                            follow_up = self.planner.next_hop_query(
                                question, item["query"], attempted_queries
                            )
                        except Exception as exc:
                            hop_trace.append({
                                "hop": hop,
                                "evidence_need": item["query"],
                                "status": "planner_rejected",
                                "reason": str(exc),
                            })
                            continue
                        if not follow_up:
                            hop_trace.append({
                                "hop": hop,
                                "evidence_need": item["query"],
                                "status": "no_safe_follow_up",
                            })
                            continue
                        attempted_queries.append(follow_up)
                        sub_results = self.index.query(
                            follow_up, top_k=candidate_k, filters=filters
                        )
                        candidate_results.extend(sub_results)
                        evidence_by_query[item["query"]].extend(sub_results)
                        for sub_result in sub_results:
                            matched_sub_queries.setdefault(sub_result.chunk_id, set()).add(item["query"])
                        hop_trace.append({
                            "hop": hop,
                            "query": follow_up,
                            "evidence_need": item["query"],
                            "result_count": len(sub_results),
                        })
                        if sub_results:
                            previous_status = item["status"]
                            item["status"] = "covered"
                            item["retrieval_query"] = follow_up
                            item["result_count"] = len(sub_results)
                            item["resolved_at_hop"] = hop
                            if semantic_multi_hop:
                                try:
                                    item.update(self.planner.judge_evidence_coverage(
                                        item["query"], evidence_by_query[item["query"]]
                                    ))
                                    item["semantic"] = True
                                except Exception as exc:
                                    item["status"] = previous_status
                                    item["semantic_error"] = str(exc)
                            made_progress = True
                    if not made_progress:
                        break
            retrieval_query = " || ".join(attempted_queries)
            semantic_coverage = None
            if semantic_multi_hop:
                statuses = [item["status"] for item in coverage]
                overall = "covered" if statuses and all(value == "covered" for value in statuses) else (
                    "unsupported" if statuses and all(value in {"missing", "unsupported"} for value in statuses)
                    else "partial"
                )
                semantic_coverage = {
                    "status": overall,
                    "sub_queries": copy.deepcopy(coverage),
                    "used_llm": any(item.get("semantic") for item in coverage),
                }
            by_chunk = {}
            for result in candidate_results:
                existing = by_chunk.get(result.chunk_id)
                if existing is None or result.score > existing.score:
                    by_chunk[result.chunk_id] = result
            results = list(by_chunk.values())
            rerank_started = time.perf_counter()
            if self.reranker:
                results = self.reranker.rerank(question, results, top_k=top_k)
            else:
                results = sorted(results, key=lambda item: item.score, reverse=True)[:top_k]
            results, backtraced_ids = self._backtrace_raw_evidence(results)
            rerank_latency_ms = (time.perf_counter() - rerank_started) * 1000
        except Exception as e:
            response = {
                "question": question,
                "status": "error",
                "results": [],
                "meta": {
                    "top_k": top_k, "result_count": 0, "error": str(e),
                    "filters": filters or {},
                    "reranker": bool(self.reranker),
                    "backtraced_source_count": 0,
                    "sub_queries": [question],
                    "index_generation_id": generation,
                },
            }
            return response

        if not results:
            response = {
                "question": question,
                "status": "empty",
                "results": [],
                "meta": {
                    "top_k": top_k,
                    "result_count": 0,
                    "retrieval_query": retrieval_query,
                    "query_version": self.rewriter.version if rewrite else "raw-v1",
                    "applied_rewrite_rules": list(plan.applied_rules),
                    "sub_queries": sub_queries,
                    "coverage": coverage,
                    "hop_trace": hop_trace,
                    "multi_hop": {"enabled": self.multi_hop_enabled, "max_hops": self.max_hops},
                    "semantic_coverage": semantic_coverage,
                    "planner": {"used_llm": plan.used_llm, "confidence": plan.confidence},
                    "filters": filters or {},
                    "reranker": bool(self.reranker),
                    "rerank_latency_ms": 0.0,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    "index_generation_id": generation,
                },
            }
            self._store_cache(cache_key, response)
            return response

        formatted = []
        backtraced_set = set(backtraced_ids)
        for r in results:
            text = r.content[:self.MAX_TEXT_LEN]
            if len(r.content) > self.MAX_TEXT_LEN:
                text += "..."
            document_id = getattr(self.index.chunks.get(r.chunk_id), "document_id", "")

            formatted.append(
                {
                    "rank": r.rank,
                    "text": text,
                    "score": float(round(r.score, 4)),
                    "video_id": r.video_id,
                    "profile": r.profile,
                    "source_url": r.source_url,
                    "start": r.start,
                    "end": r.end,
                    "has_timestamp": r.has_timestamp,
                    "chunk_id": r.chunk_id,
                    "document_id": document_id,
                    "source_path": r.source_path,
                    "chunk_type": r.chunk_type,
                    "domain": r.domain,
                    "quality": r.quality,
                    "review_status": r.review_status,
                    "source_of_truth": r.source_of_truth,
                    "risk_level": r.risk_level,
                    "answer_policy": r.answer_policy,
                    "source_refs": list(r.source_refs or []),
                    "matched_sub_queries": sorted(matched_sub_queries.get(r.chunk_id, {question})),
                    "retrieval_role": "raw_backtrace" if r.chunk_id in backtraced_set else (
                        "derived_summary" if r.answer_policy == "summary_requires_raw_evidence" else "primary"
                    ),
                    "evidence_fingerprint": evidence_fingerprint({
                        "document_id": document_id,
                        "chunk_id": r.chunk_id,
                        "video_id": r.video_id,
                        "source_refs": list(r.source_refs or []),
                        "text": r.content,
                        "chunk_type": r.chunk_type,
                        "risk_level": r.risk_level,
                        "review_status": r.review_status,
                        "source_of_truth": r.source_of_truth,
                        "answer_policy": r.answer_policy,
                    }),
                }
            )

        response = {
            "question": question,
            "status": "ok",
            "results": formatted,
            "meta": {
                "top_k": top_k,
                "result_count": len(formatted),
                "retrieval_query": retrieval_query,
                "query_version": self.rewriter.version if rewrite else "raw-v1",
                "applied_rewrite_rules": list(plan.applied_rules),
                "query_expansions": list(plan.expansions),
                "sub_queries": sub_queries,
                "coverage": coverage,
                "hop_trace": hop_trace,
                "multi_hop": {"enabled": self.multi_hop_enabled, "max_hops": self.max_hops},
                "semantic_coverage": semantic_coverage,
                "planner": {
                    "used_llm": plan.used_llm,
                    "confidence": plan.confidence,
                    "intent": plan.intent,
                    "domain": plan.domain,
                    "fallback_reason": plan.fallback_reason,
                },
                "filters": filters or {},
                "reranker": bool(self.reranker),
                "candidate_k": self.reranker_candidate_k if self.reranker else top_k,
                "rerank_latency_ms": round(rerank_latency_ms, 2),
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "backtraced_source_count": len(backtraced_ids),
                "index_generation_id": generation,
            },
        }
        self._store_cache(cache_key, response)
        return response

    def _cache_key(
        self,
        question: str,
        top_k: int,
        filters: dict | None,
        rewrite: bool | None,
        semantic_grounding: bool,
    ) -> tuple:
        generation = self._index_generation_id()
        return (
            generation,
            question,
            top_k,
            json.dumps(filters or {}, ensure_ascii=False, sort_keys=True, default=str),
            rewrite,
            semantic_grounding,
            bool(self.reranker),
            self.reranker_candidate_k,
        )

    def _index_generation_id(self) -> str:
        generation = ""
        manifest_path = getattr(self.index, "vector_dir", None)
        if manifest_path is not None:
            try:
                manifest = json.loads((manifest_path / "manifest.json").read_text(encoding="utf-8"))
                generation = str(manifest.get("generation_id", ""))
            except (OSError, json.JSONDecodeError, TypeError, AttributeError):
                generation = ""
        return generation

    def _store_cache(self, key: tuple, response: dict) -> None:
        if not self.cache_enabled or response.get("status") == "error":
            return
        self._cache[key] = copy.deepcopy(response)
        self._cache.move_to_end(key)
        while len(self._cache) > QUERY_CACHE_SIZE:
            self._cache.popitem(last=False)


    def _backtrace_raw_evidence(self, results: list) -> tuple[list, list[str]]:
        """Attach raw transcript chunks referenced by derived summaries."""
        summary_results = [
            result
            for result in results
            if result.answer_policy == "summary_requires_raw_evidence" and result.source_refs
        ]
        if not summary_results or not hasattr(self.index, "chunks"):
            return results, []

        requested = {
            (result.video_id, ref)
            for result in summary_results
            for ref in result.source_refs
        }
        existing_ids = {result.chunk_id for result in results}
        backtraced = []
        for chunk in self.index.chunks.values():
            if chunk.chunk_id in existing_ids or chunk.chunk_type != "raw_transcript":
                continue
            if not any((chunk.video_id, ref) in requested for ref in chunk.source_refs):
                continue
            backtraced.append(
                SearchResult(
                    rank=len(results) + len(backtraced) + 1,
                    chunk_id=chunk.chunk_id,
                    video_id=chunk.video_id,
                    profile=chunk.profile,
                    source_url=chunk.source_url,
                    content=chunk.content,
                    section=chunk.section,
                    start=chunk.start,
                    end=chunk.end,
                    score=0.0,
                    has_timestamp=chunk.has_timestamp,
                    source_path=chunk.source_path,
                    chunk_type=chunk.chunk_type,
                    domain=chunk.domain,
                    quality=chunk.quality,
                    review_status=chunk.review_status,
                    source_of_truth=chunk.source_of_truth,
                    risk_level=chunk.risk_level,
                    answer_policy=chunk.answer_policy,
                    source_refs=list(chunk.source_refs or []),
                )
            )
        return results + backtraced, [result.chunk_id for result in backtraced]
