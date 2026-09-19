"""Run reproducible retrieval and optional answer-generation evaluations.

The runner intentionally evaluates complete chunks from HybridIndex rather than
QueryEngine's display-truncated text. Generated-answer checks are heuristic:
they require manually annotated answer points and citations to retrieved videos;
they are not a substitute for expert medical review.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SOURCE_REF_RE = re.compile(r"\bS\d{4}\b")
VIDEO_ID_RE = re.compile(r"\bBV[A-Za-z0-9]+(?=[_\s\]|])")
CHUNK_CITATION_RE = re.compile(r"\[\s*([A-Za-z0-9_-]+)\s*\|")
ABSTENTION_MARKERS = (
    "不知道", "无法回答", "暂无相关", "没有相关", "无法从", "证据不足", "待人工核查",
)


@dataclass(frozen=True)
class EvalCase:
    id: str
    question: str
    category: str
    expected_video_ids: tuple[str, ...]
    expected_source_refs: tuple[str, ...]
    expected_sources: tuple[tuple[str, str], ...]
    answer_points: tuple[str, ...]
    risk_level: str
    expected_answer: bool


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Evaluation file not found: {path}")
    rows = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {path}:{line_no}: {exc.msg}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"Expected an object in {path}:{line_no}")
        rows.append(value)
    return rows


def load_cases(questions_path: Path, expected_path: Path) -> list[EvalCase]:
    questions = _read_jsonl(questions_path)
    expected = _read_jsonl(expected_path)
    question_by_id = {str(row.get("id", "")): row for row in questions}
    expected_by_id = {str(row.get("id", "")): row for row in expected}
    if "" in question_by_id or "" in expected_by_id:
        raise ValueError("Every evaluation row needs a non-empty id")
    if len(question_by_id) != len(questions) or len(expected_by_id) != len(expected):
        raise ValueError("Evaluation ids must be unique within each JSONL file")
    if question_by_id.keys() != expected_by_id.keys():
        missing_labels = sorted(question_by_id.keys() - expected_by_id.keys())
        missing_questions = sorted(expected_by_id.keys() - question_by_id.keys())
        raise ValueError(
            f"Question/expected ids differ; unlabeled={missing_labels}, orphan_labels={missing_questions}"
        )

    cases = []
    for case_id, question_row in question_by_id.items():
        label = expected_by_id[case_id]
        question = str(question_row.get("question", "")).strip()
        if not question:
            raise ValueError(f"Evaluation question {case_id} is empty")
        expected_answer = bool(label.get("expected_answer", True))
        source_refs = tuple(str(item) for item in label.get("expected_source_refs", []))
        video_ids = tuple(str(item) for item in label.get("expected_video_ids", []))
        explicit_sources = label.get("expected_sources")
        if expected_answer and (not source_refs or not video_ids):
            raise ValueError(f"Answerable case {case_id} needs expected video ids and source refs")
        if not expected_answer and (source_refs or video_ids):
            raise ValueError(f"Unanswerable case {case_id} cannot have expected sources")
        if explicit_sources is None:
            if len(video_ids) > 1:
                raise ValueError(
                    f"Multi-video case {case_id} needs expected_sources with video_id/source_ref pairs"
                )
            source_pairs = tuple((video_ids[0], ref) for ref in source_refs) if video_ids else ()
        else:
            if not isinstance(explicit_sources, list):
                raise ValueError(f"expected_sources for {case_id} must be a list")
            source_pairs = tuple(
                (str(item.get("video_id", "")), str(item.get("source_ref", "")))
                for item in explicit_sources
                if isinstance(item, dict)
            )
            if len(source_pairs) != len(explicit_sources) or any(not all(pair) for pair in source_pairs):
                raise ValueError(f"expected_sources for {case_id} must contain video_id and source_ref")
            if {ref for _, ref in source_pairs} != set(source_refs):
                raise ValueError(f"expected_sources for {case_id} must match expected_source_refs")
        cases.append(
            EvalCase(
                id=case_id,
                question=question,
                category=str(question_row.get("category", "other")),
                expected_video_ids=video_ids,
                expected_source_refs=source_refs,
                expected_sources=source_pairs,
                answer_points=tuple(str(item) for item in label.get("answer_points", [])),
                risk_level=str(label.get("risk_level", "low")),
                expected_answer=expected_answer,
            )
        )
    return cases


def _refs(content: str) -> set[str]:
    return set(SOURCE_REF_RE.findall(content))


def _result_payload(result) -> dict:
    return {
        "rank": result.rank,
        "chunk_id": result.chunk_id,
        "video_id": result.video_id,
        "profile": result.profile,
        "section": result.section,
        "source_refs": sorted(_refs(result.content)),
        "start": result.start if result.has_timestamp else None,
        "end": result.end if result.has_timestamp else None,
        "score": result.score,
    }


def _is_relevant(case: EvalCase, result) -> bool:
    return any((result.video_id, ref) in set(case.expected_sources) for ref in _refs(result.content))


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _answer_metrics(
    case: EvalCase,
    answer: str,
    retrieved_video_ids: set[str],
    retrieved_chunk_ids: set[str],
) -> dict:
    if not case.expected_answer:
        abstained = any(marker in answer for marker in ABSTENTION_MARKERS)
        return {"abstained": abstained, "answer_point_coverage": None, "grounded": abstained}

    normalized_answer = _normalized(answer)
    points_found = sum(_normalized(point) in normalized_answer for point in case.answer_points)
    coverage = points_found / len(case.answer_points) if case.answer_points else None
    cited_videos = set(VIDEO_ID_RE.findall(answer))
    cited_chunks = set(CHUNK_CITATION_RE.findall(answer))
    citations_valid = (
        bool(cited_chunks)
        and cited_chunks <= retrieved_chunk_ids
        and cited_videos <= retrieved_video_ids
    )
    # This is deliberately conservative and heuristic, not an entailment judgment.
    grounded = coverage == 1.0 and citations_valid
    return {
        "abstained": False,
        "answer_point_coverage": coverage,
        "cited_video_ids": sorted(cited_videos),
        "cited_chunk_ids": sorted(cited_chunks),
        "citations_valid": citations_valid,
        "grounded": grounded,
    }


def _claim_grounding_metric(usage: dict) -> float | None:
    """Read agent-side citation integrity without treating safety blocks as failures."""
    grounding = usage.get("claim_grounding")
    if not isinstance(grounding, dict) or grounding.get("status") != "ok":
        return None
    rate = grounding.get("claim_citation_grounding_rate")
    return float(rate) if isinstance(rate, (int, float)) else None


def _claim_semantic_grounding_metric(usage: dict) -> float | None:
    """Read model-judged claim support without counting disabled/safety-gated cases."""
    grounding = usage.get("claim_grounding")
    if not isinstance(grounding, dict) or grounding.get("semantic_status") not in {
        "ok", "partial_failure"
    }:
        return None
    rate = grounding.get("claim_semantic_grounding_rate")
    return float(rate) if isinstance(rate, (int, float)) else None


def _aggregate_claim_metrics(details: Sequence[dict]) -> dict:
    reports = [
        row.get("usage", {}).get("claim_grounding", {})
        for row in details
        if isinstance(row.get("usage"), dict)
    ]
    citation_reports = [report for report in reports if report.get("status") == "ok"]
    semantic_reports = [
        report
        for report in citation_reports
        if report.get("semantic_status") in {"ok", "partial_failure"}
    ]
    claim_count = sum(int(report.get("claim_count", 0)) for report in citation_reports)
    citation_grounded = sum(
        int(report.get("grounded_claim_count", 0)) for report in citation_reports
    )
    semantic_count = sum(
        int(report.get("semantic_claim_count", 0)) for report in semantic_reports
    )
    semantic_supported = sum(
        int(report.get("semantically_supported_claim_count", 0))
        for report in semantic_reports
    )
    return {
        "evaluated_claim_count": claim_count,
        "claim_citation_grounding_rate": (
            round(citation_grounded / claim_count, 4) if claim_count else None
        ),
        "semantic_evaluated_claim_count": semantic_count,
        "claim_semantic_grounding_rate": (
            round(semantic_supported / semantic_count, 4) if semantic_count else None
        ),
    }


def evaluate_cases(
    cases: Sequence[EvalCase],
    retrieve: Callable[[str, int], Sequence],
    *,
    top_k: int = 5,
    answer: Callable[[str], tuple[str, dict]] | None = None,
    rerank: Callable[[str, Sequence, int], Sequence] | None = None,
) -> dict:
    if top_k < 1:
        raise ValueError("top_k must be >= 1")
    details = []
    retrieval_latencies = []
    rerank_latencies = []
    generation_latencies = []
    prompt_tokens = []
    completion_tokens = []

    for case in cases:
        started = time.perf_counter()
        results = list(retrieve(case.question, top_k))
        rerank_ms = 0.0
        if rerank is not None:
            rerank_started = time.perf_counter()
            results = list(rerank(case.question, results, top_k))
            rerank_ms = (time.perf_counter() - rerank_started) * 1000
            for rank, result in enumerate(results, start=1):
                result.rank = rank
            rerank_latencies.append(rerank_ms)
        retrieval_ms = (time.perf_counter() - started) * 1000
        retrieval_latencies.append(retrieval_ms)
        relevant_ranks = [result.rank for result in results if _is_relevant(case, result)]
        retrieved_refs = {
            (result.video_id, ref)
            for result in results
            for ref in _refs(result.content)
        }
        expected_refs = set(case.expected_sources)
        source_coverage = (
            len(retrieved_refs & expected_refs) / len(expected_refs)
            if case.expected_answer else None
        )
        detail = {
            "id": case.id,
            "question": case.question,
            "category": case.category,
            "risk_level": case.risk_level,
            "expected_answer": case.expected_answer,
            "expected_video_ids": list(case.expected_video_ids),
            "expected_source_refs": list(case.expected_source_refs),
            "expected_sources": [
                {"video_id": video_id, "source_ref": source_ref}
                for video_id, source_ref in case.expected_sources
            ],
            "hit_at_k": bool(relevant_ranks) if case.expected_answer else None,
            "first_relevant_rank": min(relevant_ranks) if relevant_ranks else None,
            "source_coverage": source_coverage,
            "retrieval_latency_ms": round(retrieval_ms, 2),
            "rerank_latency_ms": round(rerank_ms, 2),
            "retrieved": [_result_payload(result) for result in results],
        }
        if answer is not None:
            started = time.perf_counter()
            generated, usage = answer(case.question)
            generation_ms = (time.perf_counter() - started) * 1000
            generation_latencies.append(generation_ms)
            usage = usage or {}
            if isinstance(usage.get("prompt_eval_count"), int):
                prompt_tokens.append(usage["prompt_eval_count"])
            if isinstance(usage.get("eval_count"), int):
                completion_tokens.append(usage["eval_count"])
            detail.update(
                {
                    "answer": generated,
                    "generation_latency_ms": round(generation_ms, 2),
                    "usage": usage,
                    **_answer_metrics(
                        case,
                        generated,
                        {r.video_id for r in results},
                        {r.chunk_id for r in results},
                    ),
                }
            )
            detail["claim_citation_grounding_rate"] = _claim_grounding_metric(usage)
            detail["claim_semantic_grounding_rate"] = _claim_semantic_grounding_metric(usage)
        details.append(detail)

    answerable = [row for row in details if row["expected_answer"]]
    unanswerable = [row for row in details if not row["expected_answer"]]
    summary = {
        "case_count": len(details),
        "answerable_case_count": len(answerable),
        "unanswerable_case_count": len(unanswerable),
        "recall_at_k": _mean(row["hit_at_k"] for row in answerable),
        "mrr": _mean(1 / row["first_relevant_rank"] if row["first_relevant_rank"] else 0 for row in answerable),
        "source_coverage": _mean(row["source_coverage"] for row in answerable),
        "avg_retrieval_latency_ms": _mean(retrieval_latencies),
        "avg_rerank_latency_ms": _mean(rerank_latencies),
        "avg_total_retrieval_latency_ms": _mean(
            retrieval + rerank
            for retrieval, rerank in zip(
                retrieval_latencies,
                rerank_latencies or [0.0] * len(retrieval_latencies),
            )
        ),
        "reranker_enabled": rerank is not None,
    }
    if answer is not None:
        claim_metrics = _aggregate_claim_metrics(answerable)
        summary.update(
            {
                "heuristic_grounded_answer_rate": _mean(row["grounded"] for row in answerable),
                "abstention_accuracy": _mean(row["abstained"] for row in unanswerable),
                "answer_point_coverage": _mean(row["answer_point_coverage"] for row in answerable),
                "avg_generation_latency_ms": _mean(generation_latencies),
                "avg_prompt_tokens": _mean(prompt_tokens),
                "avg_completion_tokens": _mean(completion_tokens),
                "token_metering_available": bool(prompt_tokens or completion_tokens),
                **claim_metrics,
            }
        )
    review_queue = []
    for row in details:
        grounding = row.get("usage", {}).get("claim_grounding", {})
        for claim in grounding.get("review_claims", []):
            review_queue.append(
                {
                    "case_id": row["id"],
                    "question": row["question"],
                    "answer": row.get("answer", ""),
                    **claim,
                }
            )
    if answer is not None:
        summary["claim_review_count"] = len(review_queue)
    return {
        "summary": summary,
        "cases": details,
        "by_category": _by_category(details),
        "claim_review_queue": review_queue,
    }


def _mean(values: Iterable[float | bool | None]) -> float | None:
    values = [float(value) for value in values if value is not None]
    return round(statistics.fmean(values), 4) if values else None


def _by_category(details: Sequence[dict]) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in details:
        groups[row["category"]].append(row)
    return {
        category: {
            "case_count": len(rows),
            "recall_at_k": _mean(row["hit_at_k"] for row in rows if row["expected_answer"]),
            "mrr": _mean(
                1 / row["first_relevant_rank"] if row["first_relevant_rank"] else 0
                for row in rows if row["expected_answer"]
            ),
            "source_coverage": _mean(row["source_coverage"] for row in rows if row["expected_answer"]),
        }
        for category, rows in sorted(groups.items())
    }


def _markdown_report(report: dict) -> str:
    summary = report["summary"]
    lines = ["# RAG Evaluation Report", "", "## Summary", ""]
    for key, value in summary.items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## By Category", "", "| Category | Cases | Recall@k | MRR | Source coverage |", "| --- | ---: | ---: | ---: | ---: |"])
    for category, metrics in report["by_category"].items():
        lines.append(
            f"| {category} | {metrics['case_count']} | {metrics['recall_at_k']} | "
            f"{metrics['mrr']} | {metrics['source_coverage']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate VideoContentKnowledgeBase retrieval and answers")
    parser.add_argument("--questions", type=Path, default=PROJECT_ROOT / "eval" / "questions.jsonl")
    parser.add_argument("--expected", type=Path, default=PROJECT_ROOT / "eval" / "expected.jsonl")
    parser.add_argument("--index-dir", type=Path, default=PROJECT_ROOT / "index")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--with-generation", action="store_true", help="Call Ollama and evaluate answer heuristics")
    parser.add_argument(
        "--claim-semantic-grounding",
        action="store_true",
        help="Use Ollama to classify each generated claim against its cited chunks",
    )
    parser.add_argument("--query-rewrite", action="store_true", help="Evaluate deterministic query expansion")
    parser.add_argument("--reranker-model", type=str, default="", help="Local CrossEncoder model path")
    parser.add_argument("--reranker-candidate-k", type=int, default=20, help="Candidate count before reranking")
    parser.add_argument("--report-dir", type=Path, default=PROJECT_ROOT / "eval" / "reports")
    args = parser.parse_args()
    if args.claim_semantic_grounding and not args.with_generation:
        parser.error("--claim-semantic-grounding requires --with-generation")
    if args.reranker_candidate_k < args.top_k:
        parser.error("--reranker-candidate-k must be >= --top-k")
    if args.with_generation and args.reranker_model:
        parser.error("--with-generation cannot be combined with --reranker-model yet")

    from config import BGE_MODEL_PATH
    from modules.embedding_runtime import SentenceTransformer
    from modules.indexer import HybridIndex

    cases = load_cases(args.questions, args.expected)
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = SentenceTransformer(BGE_MODEL_PATH, device=device)
    index = HybridIndex(args.index_dir, model=model)
    if index.faiss_index is None:
        raise RuntimeError("No usable index. Run the pipeline or rebuild the index before evaluation.")

    answer_fn = None
    if args.with_generation:
        from modules import agent
        from modules.query_engine import QueryEngine

        engine = QueryEngine(index)
        answer_fn = lambda question: agent.answer_question_with_usage(
            engine,
            question,
            semantic_grounding=args.claim_semantic_grounding,
        )

    retrieve = lambda question, top_k: index.query(
        question,
        top_k=max(top_k, args.reranker_candidate_k) if args.reranker_model else top_k,
    )
    rewrite_version = "raw-v1"
    if args.query_rewrite:
        from config import QUERY_REWRITE_PATH
        from modules.query_rewriter import QueryRewriter

        rewriter = QueryRewriter.from_path(QUERY_REWRITE_PATH)
        retrieve = lambda question, top_k: index.query(
            rewriter.rewrite(question).rewritten,
            top_k=max(top_k, args.reranker_candidate_k) if args.reranker_model else top_k,
        )
        rewrite_version = rewriter.version

    rerank_fn = None
    if args.reranker_model:
        from modules.reranker import CrossEncoderReranker

        reranker = CrossEncoderReranker(args.reranker_model, device=device)
        rerank_fn = reranker.rerank

    report = evaluate_cases(
        cases,
        retrieve,
        top_k=args.top_k,
        answer=answer_fn,
        rerank=rerank_fn,
    )
    report["run"] = {
        "created_at": datetime.now().astimezone().isoformat(),
        "top_k": args.top_k,
        "with_generation": args.with_generation,
        "claim_semantic_grounding": args.claim_semantic_grounding,
        "query_rewrite": args.query_rewrite,
        "query_version": rewrite_version,
        "reranker_model": args.reranker_model or None,
        "reranker_candidate_k": args.reranker_candidate_k if args.reranker_model else None,
        "questions": str(args.questions),
        "expected": str(args.expected),
    }
    args.report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = args.report_dir / f"rag-eval-{stamp}.json"
    markdown_path = args.report_dir / f"rag-eval-{stamp}.md"
    review_path = args.report_dir / f"rag-eval-{stamp}-claim-review.jsonl"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(_markdown_report(report), encoding="utf-8")
    review_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False) + "\n"
            for row in report["claim_review_queue"]
        ),
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Report: {json_path}")
    if report["claim_review_queue"]:
        print(f"Claim review queue: {review_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
