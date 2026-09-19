"""Compare two claim-judge reports and produce a disagreement review queue."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def load_report(path: Path) -> dict[str, dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("cases")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"Report has no cases: {path}")
    result = {}
    for row in rows:
        case_id = str(row.get("id", "")).strip()
        if not case_id or case_id in result:
            raise ValueError(f"Report has empty or duplicate case id: {path}")
        result[case_id] = row
    return result


def compare_reports(qwen_path: Path, glm_path: Path) -> dict:
    qwen = load_report(qwen_path)
    glm = load_report(glm_path)
    shared_ids = sorted(set(qwen) & set(glm))
    if not shared_ids:
        raise ValueError("The two reports have no shared case IDs")

    rows = []
    pair_counts: Counter[tuple[str, str]] = Counter()
    disagreements = []
    for case_id in shared_ids:
        left, right = qwen[case_id], glm[case_id]
        expected = left.get("expected_label", right.get("expected_label"))
        if right.get("expected_label", expected) != expected:
            raise ValueError(f"Expected labels differ for {case_id}")
        qwen_label = str(left.get("predicted_label", ""))
        glm_label = str(right.get("predicted_label", ""))
        pair_counts[(qwen_label, glm_label)] += 1
        needs_review = (
            qwen_label != glm_label
            or not row_is_high_confidence(left)
            or not row_is_high_confidence(right)
            or qwen_label == "insufficient_evidence"
            or glm_label == "insufficient_evidence"
        )
        row = {
            "id": case_id,
            "claim": left.get("claim", right.get("claim", "")),
            "expected_label": expected,
            "qwen_label": qwen_label,
            "glm_label": glm_label,
            "agreement": qwen_label == glm_label,
            "qwen_correct": qwen_label == expected,
            "glm_correct": glm_label == expected,
            "qwen_confidence": left.get("confidence"),
            "glm_confidence": right.get("confidence"),
            "qwen_rationale": left.get("rationale", ""),
            "glm_rationale": right.get("rationale", ""),
            "needs_review": needs_review,
        }
        rows.append(row)
        if not row["agreement"]:
            disagreements.append(row)

    qwen_correct = sum(row["qwen_correct"] for row in rows)
    glm_correct = sum(row["glm_correct"] for row in rows)
    total = len(rows)
    summary = {
        "case_count": total,
        "agreement_count": total - len(disagreements),
        "agreement_rate": round((total - len(disagreements)) / total, 4),
        "disagreement_count": len(disagreements),
        "qwen_accuracy": round(qwen_correct / total, 4),
        "glm_accuracy": round(glm_correct / total, 4),
        "pair_counts": {
            f"{qwen_label}->{glm_label}": count
            for (qwen_label, glm_label), count in sorted(pair_counts.items())
        },
        "review_count": sum(row["needs_review"] for row in rows),
    }
    return {
        "run": {
            "qwen_report": qwen_path.as_posix(),
            "glm_report": glm_path.as_posix(),
            "comparison_version": "claim-judge-dual-v1",
        },
        "summary": summary,
        "cases": rows,
        "disagreements": disagreements,
        "review_queue": [row for row in rows if row["needs_review"]],
    }


def row_is_high_confidence(row: dict) -> bool:
    return str(row.get("confidence", "")).lower() == "high" and not row.get("error")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qwen-report", type=Path, required=True)
    parser.add_argument("--glm-report", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "eval" / "reports" / "claim-judge-dual.json",
    )
    parser.add_argument("--baseline-output", type=Path)
    args = parser.parse_args()

    report = compare_reports(args.qwen_report, args.glm_report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.baseline_output:
        args.baseline_output.parent.mkdir(parents=True, exist_ok=True)
        args.baseline_output.write_text(
            json.dumps({"run": report["run"], "summary": report["summary"]}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
