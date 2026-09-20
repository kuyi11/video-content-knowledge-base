"""Constrained query planning with a deterministic fallback."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import ollama

from config import QUERY_PLANNER_ENABLED, QUERY_PLANNER_MAX_SUBQUERIES, QUERY_PLANNER_MODEL, LLM_CONFIG
from modules.query_rewriter import QueryRewriter


@dataclass(frozen=True)
class QueryPlan:
    original_query: str
    sub_queries: tuple[str, ...]
    intent: str = "fact_lookup"
    domain: str = ""
    confidence: str = "medium"
    used_llm: bool = False
    fallback_reason: str = ""
    applied_rules: tuple[str, ...] = ()
    expansions: tuple[str, ...] = ()

    @property
    def is_complex(self) -> bool:
        return len(self.sub_queries) > 1 or len(self.original_query) > 80


class QueryPlanner:
    MAX_QUERY_LENGTH = 600

    def __init__(
        self,
        rewriter: QueryRewriter,
        *,
        enabled: bool = QUERY_PLANNER_ENABLED,
        model: str = QUERY_PLANNER_MODEL,
        max_subqueries: int = QUERY_PLANNER_MAX_SUBQUERIES,
        client=None,
    ):
        self.rewriter = rewriter
        self.enabled = enabled
        self.model = model
        self.max_subqueries = max(1, min(5, max_subqueries))
        self.client = client or ollama.Client(
            host=LLM_CONFIG["host"], timeout=LLM_CONFIG["timeout_seconds"]
        )

    def plan(self, question: str, *, rewrite: bool = True) -> QueryPlan:
        original = re.sub(r"\s+", " ", question).strip()
        deterministic = self._deterministic_plan(original, rewrite=rewrite)
        if not self.enabled or not deterministic.is_complex:
            return deterministic
        try:
            payload = self._llm_plan(original, deterministic.sub_queries)
            return self._validate_payload(original, payload)
        except Exception as exc:
            return QueryPlan(
                **{**deterministic.__dict__, "fallback_reason": str(exc)}
            )

    def next_hop_query(
        self,
        original_query: str,
        missing_query: str,
        attempted_queries: list[str],
    ) -> str | None:
        """Produce one constrained retry query for an uncovered evidence need."""
        if not self.enabled:
            return None
        response = self.client.chat(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是检索迭代规划器。当前子问题没有召回证据，请生成一个更明确的替代查询。"
                        "不得增加原问题没有的实体、条件或结论。只输出 JSON："
                        '{"query":"..."}。如果无法安全改写，输出 {"query":""}。'
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "original_query": original_query,
                            "missing_query": missing_query,
                            "attempted_queries": attempted_queries,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            format="json",
            options={"temperature": 0},
        )
        payload = json.loads(response["message"]["content"])
        query = re.sub(r"\s+", " ", str(payload.get("query", ""))).strip()
        if not query:
            return None
        if len(query) > self.MAX_QUERY_LENGTH:
            raise ValueError("next-hop query is too long")
        if query in attempted_queries:
            raise ValueError("next-hop query repeats an attempted query")
        if not self._traceable(original_query, query):
            raise ValueError("next-hop query is not traceable to the original question")
        return query

    def judge_evidence_coverage(self, query: str, results: list) -> dict:
        """Judge one evidence need against raw retrieval results."""
        evidence = [
            {"chunk_id": item.chunk_id, "text": item.content[:1200]}
            for item in results[:4]
        ]
        if not evidence:
            return {
                "status": "unsupported",
                "confidence": "high",
                "rationale": "没有检索到可用证据。",
            }
        response = self.client.chat(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你只判断证据是否完整覆盖问题。只输出 JSON："
                        '{"status":"covered|partial|unsupported",'
                        '"confidence":"high|medium|low","rationale":"简短理由"}。'
                        "不能引入证据中没有的事实。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"query": query, "evidence": evidence}, ensure_ascii=False
                    ),
                },
            ],
            format="json",
            options={"temperature": 0},
        )
        payload = json.loads(response["message"]["content"])
        status = str(payload.get("status", "")).lower()
        confidence = str(payload.get("confidence", "")).lower()
        rationale = str(payload.get("rationale", "")).strip()
        if status not in {"covered", "partial", "unsupported"}:
            raise ValueError("invalid coverage status")
        if confidence not in {"high", "medium", "low"} or not rationale:
            raise ValueError("invalid coverage confidence or rationale")
        return {"status": status, "confidence": confidence, "rationale": rationale}

    def _deterministic_plan(self, question: str, *, rewrite: bool) -> QueryPlan:
        parts = re.split(r"[?？；;。]|(?:以及|并且|还有|同时|分别)", question)
        parts = [part.strip(" ，,、") for part in parts if part.strip(" ，,、")]
        parts = parts[: self.max_subqueries] or [question]
        rewrite_results = []
        if rewrite:
            rewrite_results = [self.rewriter.rewrite(part) for part in parts]
            parts = [result.rewritten for result in rewrite_results]
        return QueryPlan(
            original_query=question,
            sub_queries=tuple(parts),
            confidence="high" if len(parts) == 1 else "medium",
            applied_rules=tuple(sorted({
                rule for result in rewrite_results for rule in result.applied_rules
            })),
            expansions=tuple(sorted({
                expansion for result in rewrite_results for expansion in result.expansions
            })),
        )

    def _llm_plan(self, question: str, fallback_queries: list[str]) -> dict:
        response = self.client.chat(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是检索规划器，只负责把复杂用户问题拆成最多 3 个可检索子问题。"
                        "不得添加原问题没有的实体或事实。只输出 JSON："
                        '{"sub_queries":["..."],"intent":"fact_lookup",'
                        '"domain":"","confidence":"high|medium|low"}'
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"original_query": question, "deterministic_candidates": fallback_queries},
                        ensure_ascii=False,
                    ),
                },
            ],
            format="json",
            options={"temperature": 0},
        )
        return json.loads(response["message"]["content"])

    def _validate_payload(self, question: str, payload: dict) -> QueryPlan:
        if not isinstance(payload, dict):
            raise ValueError("query plan must be an object")
        raw_queries = payload.get("sub_queries")
        if not isinstance(raw_queries, list) or not raw_queries:
            raise ValueError("query plan needs non-empty sub_queries")
        if len(raw_queries) > self.max_subqueries:
            raise ValueError("query plan has too many sub_queries")
        queries = []
        for raw_query in raw_queries:
            query = re.sub(r"\s+", " ", str(raw_query)).strip()
            if not query or len(query) > self.MAX_QUERY_LENGTH:
                raise ValueError("query plan contains invalid sub_query")
            if not self._traceable(question, query):
                raise ValueError("sub_query is not traceable to the original question")
            queries.append(query)
        confidence = str(payload.get("confidence", "medium")).lower()
        if confidence not in {"high", "medium", "low"}:
            raise ValueError("invalid query plan confidence")
        return QueryPlan(
            original_query=question,
            sub_queries=tuple(dict.fromkeys(queries)),
            intent=str(payload.get("intent", "fact_lookup"))[:80],
            domain=str(payload.get("domain", ""))[:80],
            confidence=confidence,
            used_llm=True,
        )

    @staticmethod
    def _traceable(original: str, query: str) -> bool:
        original_folded = original.casefold()
        query_folded = query.casefold()
        if any(token and token in original_folded for token in re.findall(r"[A-Za-z0-9_]{2,}", query_folded)):
            return True
        chars = {char for char in original_folded if not char.isspace() and char not in "，。？！；;,.!?"}
        query_chars = {char for char in query_folded if not char.isspace() and char not in "，。？！；;,.!?"}
        return bool(query_chars) and len(chars & query_chars) / len(query_chars) >= 0.2


def evaluate_subquery_coverage(
    sub_queries: list[str],
    results: list[dict],
    *,
    client,
    model: str,
) -> dict:
    """Judge whether retrieved evidence answers each sub-query."""
    coverage = []
    for query in sub_queries:
        evidence = [
            item for item in results
            if item.get("text")
            and (not item.get("matched_sub_queries") or query in item["matched_sub_queries"])
        ][:4]
        if not evidence:
            coverage.append({
                "query": query,
                "status": "unsupported",
                "confidence": "high",
                "rationale": "没有检索到可用证据。",
            })
            continue
        payload = {
            "query": query,
            "evidence": [
                {"chunk_id": item.get("chunk_id", ""), "text": item.get("text", "")}
                for item in evidence
            ],
        }
        try:
            response = client.chat(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你只判断证据是否覆盖问题。只输出 JSON："
                            '{"status":"covered|partial|unsupported",'
                            '"confidence":"high|medium|low","rationale":"简短理由"}。'
                            "不能引入证据中没有的事实。"
                        ),
                    },
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                format="json",
                options={"temperature": 0},
            )
            judged = json.loads(response["message"]["content"])
            status = str(judged.get("status", "")).lower()
            confidence = str(judged.get("confidence", "")).lower()
            rationale = str(judged.get("rationale", "")).strip()
            if status not in {"covered", "partial", "unsupported"}:
                raise ValueError("invalid coverage status")
            if confidence not in {"high", "medium", "low"} or not rationale:
                raise ValueError("invalid coverage confidence or rationale")
            coverage.append({
                "query": query,
                "status": status,
                "confidence": confidence,
                "rationale": rationale,
            })
        except Exception as exc:
            coverage.append({
                "query": query,
                "status": "partial",
                "confidence": "low",
                "rationale": "语义覆盖检查失败，需人工复核。",
                "error": str(exc),
            })
    statuses = [item["status"] for item in coverage]
    overall = "covered" if statuses and all(status == "covered" for status in statuses) else (
        "unsupported" if statuses and all(status == "unsupported" for status in statuses) else "partial"
    )
    return {"status": overall, "sub_queries": coverage, "used_llm": True}


def detect_evidence_conflicts(results: list[dict], *, client, model: str) -> dict:
    """Detect direct contradictions among retrieved evidence chunks."""
    evidence = [
        {"chunk_id": item.get("chunk_id", ""), "text": item.get("text", "")}
        for item in results
        if item.get("chunk_id") and item.get("text")
    ][:8]
    if len(evidence) < 2:
        return {"status": "none", "conflicts": [], "used_llm": False}
    try:
        response = client.chat(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你只判断给定证据之间是否存在直接冲突。不要判断哪一方真实。"
                        "只输出 JSON："
                        '{"status":"none|conflict|uncertain","conflicts":['
                        '{"chunk_ids":["实际chunk_id"],"rationale":"简短理由"}]}'
                    ),
                },
                {"role": "user", "content": json.dumps({"evidence": evidence}, ensure_ascii=False)},
            ],
            format="json",
            options={"temperature": 0},
        )
        payload = json.loads(response["message"]["content"])
        status = str(payload.get("status", "")).lower()
        if status not in {"none", "conflict", "uncertain"}:
            raise ValueError("invalid conflict status")
        raw_conflicts = payload.get("conflicts", [])
        if not isinstance(raw_conflicts, list):
            raise ValueError("conflicts must be a list")
        allowed = {item["chunk_id"] for item in evidence}
        conflicts = []
        for item in raw_conflicts[:5]:
            if not isinstance(item, dict):
                raise ValueError("conflict item must be an object")
            chunk_ids = [str(value) for value in item.get("chunk_ids", [])]
            rationale = str(item.get("rationale", "")).strip()
            if len(chunk_ids) < 2 or not set(chunk_ids) <= allowed or not rationale:
                raise ValueError("invalid conflict item")
            conflicts.append({"chunk_ids": list(dict.fromkeys(chunk_ids)), "rationale": rationale})
        if status == "conflict" and not conflicts:
            raise ValueError("conflict status needs conflict items")
        return {"status": status, "conflicts": conflicts, "used_llm": True}
    except Exception as exc:
        return {
            "status": "uncertain",
            "conflicts": [],
            "used_llm": True,
            "error": str(exc),
        }
