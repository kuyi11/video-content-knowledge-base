"""Evaluate claim/evidence semantic judgments against JSONL gold labels."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.claim_judge import SEMANTIC_VERDICTS, ClaimJudgment, parse_judgment


@dataclass(frozen=True)
class ClaimEvalCase:
    id: str
    claim: str
    evidence: tuple[dict, ...]
    expected_label: str
    notes: str = ""
    annotation_status: str = "reviewed"


def load_claim_cases(path: Path) -> list[ClaimEvalCase]:
    if not path.exists():
        raise FileNotFoundError(f"Claim label file not found: {path}")
    cases = []
    seen_ids = set()
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {path}:{line_no}: {exc.msg}") from exc
        case_id = str(row.get("id", "")).strip()
        claim = str(row.get("claim", "")).strip()
        label = str(row.get("label", "")).strip().lower()
        evidence = row.get("evidence")
        annotation_status = str(row.get("annotation_status", "reviewed")).strip()
        if not case_id or case_id in seen_ids:
            raise ValueError(f"Claim case id is empty or duplicated at {path}:{line_no}")
        if not claim:
            raise ValueError(f"Claim is empty at {path}:{line_no}")
        if label not in SEMANTIC_VERDICTS:
            raise ValueError(f"Unsupported claim label at {path}:{line_no}: {label}")
        if annotation_status not in {"reviewed", "pending_human_review"}:
            raise ValueError(
                f"Unsupported annotation_status at {path}:{line_no}: {annotation_status}"
            )
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f"Evidence must be a non-empty list at {path}:{line_no}")
        chunk_ids = []
        for item in evidence:
            if not isinstance(item, dict) or not item.get("chunk_id") or not item.get("text"):
                raise ValueError(
                    f"Each evidence item needs chunk_id and text at {path}:{line_no}"
                )
            chunk_ids.append(str(item["chunk_id"]))
        if len(set(chunk_ids)) != len(chunk_ids):
            raise ValueError(f"Evidence chunk ids must be unique at {path}:{line_no}")
        seen_ids.add(case_id)
        cases.append(
            ClaimEvalCase(
                id=case_id,
                claim=claim,
                evidence=tuple(evidence),
                expected_label=label,
                notes=str(row.get("notes", "")),
                annotation_status=annotation_status,
            )
        )
    if not cases:
        raise ValueError(f"No claim cases found in {path}")
    return cases


def _f1(expected: Sequence[str], predicted: Sequence[str], label: str) -> float:
    true_positive = sum(e == label and p == label for e, p in zip(expected, predicted))
    false_positive = sum(e != label and p == label for e, p in zip(expected, predicted))
    false_negative = sum(e == label and p != label for e, p in zip(expected, predicted))
    denominator = 2 * true_positive + false_positive + false_negative
    return 2 * true_positive / denominator if denominator else 0.0


def evaluate_claim_cases(
    cases: Sequence[ClaimEvalCase],
    judge: Callable[[str, Sequence[dict]], ClaimJudgment | dict],
) -> dict:
    details = []
    latencies = []
    for case in cases:
        started = time.perf_counter()
        error = None
        try:
            raw = judge(case.claim, case.evidence)
            judgment = raw if isinstance(raw, ClaimJudgment) else parse_judgment(
                raw,
                {str(item["chunk_id"]) for item in case.evidence},
            )
        except Exception as exc:
            error = str(exc)
            judgment = ClaimJudgment(
                "insufficient_evidence",
                "low",
                "裁判执行失败，已降级为人工复核。",
                (),
            )
        latency_ms = (time.perf_counter() - started) * 1000
        latencies.append(latency_ms)
        correct = judgment.verdict == case.expected_label
        needs_review = (
            not correct
            or judgment.confidence != "high"
            or judgment.verdict == "insufficient_evidence"
            or error is not None
        )
        details.append(
            {
                "id": case.id,
                "claim": case.claim,
                "evidence": list(case.evidence),
                "expected_label": case.expected_label,
                "predicted_label": judgment.verdict,
                "confidence": judgment.confidence,
                "rationale": judgment.rationale,
                "evidence_chunk_ids": list(judgment.evidence_chunk_ids),
                "correct": correct,
                "needs_review": needs_review,
                "error": error,
                "latency_ms": round(latency_ms, 2),
                "notes": case.notes,
                "annotation_status": case.annotation_status,
            }
        )

    expected = [row["expected_label"] for row in details]
    predicted = [row["predicted_label"] for row in details]
    per_label_f1 = {label: round(_f1(expected, predicted, label), 4) for label in sorted(SEMANTIC_VERDICTS)}
    review_queue = [row for row in details if row["needs_review"]]
    summary = {
        "case_count": len(details),
        "accuracy": round(sum(row["correct"] for row in details) / len(details), 4),
        "macro_f1": round(statistics.fmean(per_label_f1.values()), 4),
        "per_label_f1": per_label_f1,
        "expected_label_counts": dict(sorted(Counter(expected).items())),
        "predicted_label_counts": dict(sorted(Counter(predicted).items())),
        "claim_grounded_rate": round(predicted.count("supported") / len(predicted), 4),
        "review_count": len(review_queue),
        "avg_latency_ms": round(statistics.fmean(latencies), 2),
        "annotation_status_counts": dict(
            sorted(Counter(case.annotation_status for case in cases).items())
        ),
    }
    return {"summary": summary, "cases": details, "review_queue": review_queue}


def _markdown_report(report: dict) -> str:
    summary = report["summary"]
    lines = ["# Claim Grounding Evaluation", "", "## Summary", ""]
    for key, value in summary.items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(
        [
            "",
            "## Cases",
            "",
            "| ID | Expected | Predicted | Confidence | Correct | Review |",
            "| --- | --- | --- | --- | ---: | ---: |",
        ]
    )
    for row in report["cases"]:
        lines.append(
            f"| {row['id']} | {row['expected_label']} | {row['predicted_label']} | "
            f"{row['confidence']} | {row['correct']} | {row['needs_review']} |"
        )
    return "\n".join(lines) + "\n"


def write_report(report: dict, report_dir: Path, prefix: str) -> tuple[Path, Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / f"{prefix}.json"
    markdown_path = report_dir / f"{prefix}.md"
    review_path = report_dir / f"{prefix}-review.jsonl"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(_markdown_report(report), encoding="utf-8")
    review_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in report["review_queue"]),
        encoding="utf-8",
    )
    return json_path, markdown_path, review_path


def write_baseline(report: dict, path: Path) -> Path:
    """Write only reproducible run metadata and aggregate metrics to Git."""
    path.parent.mkdir(parents=True, exist_ok=True)
    baseline = {
        "run": report["run"],
        "summary": report["summary"],
    }
    path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--labels",
        type=Path,
        default=PROJECT_ROOT / "eval" / "claim_labels.jsonl",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=PROJECT_ROOT / "eval" / "reports",
    )
    parser.add_argument("--model", default="")
    parser.add_argument("--output-prefix", default="")
    parser.add_argument(
        "--baseline-output",
        type=Path,
        help="Optional compact JSON baseline containing run metadata and summary only",
    )
    parser.add_argument(
        "--reviewed-only",
        action="store_true",
        help="Evaluate only labels whose annotation_status is reviewed",
    )
    args = parser.parse_args()

    import ollama
    from config import LLM_CONFIG
    from modules.claim_judge import OllamaClaimJudge

    cases = load_claim_cases(args.labels)
    if args.reviewed_only:
        cases = [case for case in cases if case.annotation_status == "reviewed"]
        if not cases:
            raise ValueError("No reviewed claim labels found")
    model = args.model or LLM_CONFIG["model"]
    judge = OllamaClaimJudge(ollama.Client(host=LLM_CONFIG["host"]), model)
    report = evaluate_claim_cases(cases, judge)
    report["run"] = {
        "created_at": datetime.now().astimezone().isoformat(),
        "model": model,
        "labels": display_path(args.labels),
        "judge_version": "claim-entailment-v1",
        "reviewed_only": args.reviewed_only,
    }
    prefix = args.output_prefix or f"claim-grounding-{datetime.now():%Y%m%d-%H%M%S}"
    paths = write_report(report, args.report_dir, prefix)
    baseline_path = write_baseline(report, args.baseline_output) if args.baseline_output else None
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Report: {paths[0]}")
    print(f"Review queue: {paths[2]}")
    if baseline_path:
        print(f"Baseline: {baseline_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
