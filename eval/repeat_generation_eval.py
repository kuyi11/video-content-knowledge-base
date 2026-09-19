"""Repeat stochastic answer-generation evaluation and summarize metric variance."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eval.run_eval import evaluate_cases, load_cases


TRACKED_METRICS = (
    "heuristic_grounded_answer_rate",
    "abstention_accuracy",
    "answer_point_coverage",
    "avg_generation_latency_ms",
    "claim_citation_grounding_rate",
    "claim_semantic_grounding_rate",
    "claim_review_count",
)


def aggregate_reports(reports: list[dict]) -> dict:
    if not reports:
        raise ValueError("at least one report is required")
    metric_distribution = {}
    for metric in TRACKED_METRICS:
        values = [
            float(report["summary"][metric])
            for report in reports
            if isinstance(report["summary"].get(metric), (int, float))
        ]
        if values:
            metric_distribution[metric] = {
                "mean": round(statistics.fmean(values), 4),
                "min": round(min(values), 4),
                "max": round(max(values), 4),
                "stdev": round(statistics.stdev(values), 4) if len(values) > 1 else 0.0,
            }

    claim_count = citation_grounded = semantic_count = semantic_supported = 0
    review_count = 0
    for report in reports:
        review_count += len(report.get("claim_review_queue", []))
        for case in report.get("cases", []):
            grounding = case.get("usage", {}).get("claim_grounding", {})
            if grounding.get("status") != "ok":
                continue
            count = int(grounding.get("claim_count", 0))
            claim_count += count
            citation_grounded += int(grounding.get("grounded_claim_count", 0))
            if grounding.get("semantic_status") in {"ok", "partial_failure"}:
                semantic_count += int(grounding.get("semantic_claim_count", 0))
                semantic_supported += int(
                    grounding.get("semantically_supported_claim_count", 0)
                )
    return {
        "run_count": len(reports),
        "metrics": metric_distribution,
        "weighted_claim_metrics": {
            "evaluated_claim_count": claim_count,
            "claim_citation_grounding_rate": (
                round(citation_grounded / claim_count, 4) if claim_count else None
            ),
            "semantic_evaluated_claim_count": semantic_count,
            "claim_semantic_grounding_rate": (
                round(semantic_supported / semantic_count, 4) if semantic_count else None
            ),
            "review_count": review_count,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--claim-semantic-grounding",
        action="store_true",
        help="Judge every generated claim against its cited evidence",
    )
    parser.add_argument(
        "--questions",
        type=Path,
        default=PROJECT_ROOT / "eval" / "questions.jsonl",
    )
    parser.add_argument(
        "--expected",
        type=Path,
        default=PROJECT_ROOT / "eval" / "expected.jsonl",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=PROJECT_ROOT / "eval" / "reports",
    )
    parser.add_argument("--baseline-output", type=Path)
    args = parser.parse_args()
    if args.runs < 2:
        parser.error("--runs must be >= 2")
    if args.top_k < 1:
        parser.error("--top-k must be >= 1")

    import torch

    from config import BGE_MODEL_PATH, INDEX_DIR, LLM_CONFIG
    from modules import agent
    from modules.chunk_splitter import CHUNKING_VERSION
    from modules.embedding_runtime import SentenceTransformer
    from modules.indexer import HybridIndex
    from modules.query_engine import QueryEngine

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = SentenceTransformer(BGE_MODEL_PATH, device=device)
    index = HybridIndex(INDEX_DIR, model=model)
    if index.faiss_index is None:
        raise RuntimeError("No usable index. Run tools/rebuild_index.py first.")
    engine = QueryEngine(index)
    cases = load_cases(args.questions, args.expected)
    reports = []
    for run_number in range(1, args.runs + 1):
        print(f"Starting generation run {run_number}/{args.runs}", flush=True)
        reports.append(
            evaluate_cases(
                cases,
                index.query,
                top_k=args.top_k,
                answer=lambda question: agent.answer_question_with_usage(
                    engine,
                    question,
                    top_k=args.top_k,
                    semantic_grounding=args.claim_semantic_grounding,
                ),
            )
        )

    payload = {
        "run": {
            "created_at": datetime.now().astimezone().isoformat(),
            "model": LLM_CONFIG["model"],
            "judge_model": LLM_CONFIG["model"] if args.claim_semantic_grounding else None,
            "runs": args.runs,
            "top_k": args.top_k,
            "question_count": len(cases),
            "index_document_count": len(
                {chunk.document_id for chunk in index.chunks.values()}
            ),
            "index_chunk_count": len(index.chunks),
            "chunking_version": CHUNKING_VERSION,
            "same_model_judge": args.claim_semantic_grounding,
        },
        "aggregate": aggregate_reports(reports),
        "runs": [report["summary"] for report in reports],
    }
    args.report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = args.report_dir / f"generation-repeat-{stamp}.json"
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.baseline_output:
        args.baseline_output.parent.mkdir(parents=True, exist_ok=True)
        args.baseline_output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(payload["aggregate"], ensure_ascii=False, indent=2))
    print(f"Report: {report_path}")
    if args.baseline_output:
        print(f"Baseline: {args.baseline_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
