import json
from collections import Counter
from pathlib import Path

import pytest

from eval.claim_grounding_eval import (
    PROJECT_ROOT,
    display_path,
    evaluate_claim_cases,
    load_claim_cases,
    write_baseline,
)
from modules.claim_judge import ClaimJudgment


def test_load_claim_cases_validates_jsonl_schema(tmp_path):
    path = tmp_path / "claims.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "one",
                "claim": "结论",
                "evidence": [{"chunk_id": "chunk-1", "text": "证据"}],
                "label": "supported",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    cases = load_claim_cases(path)

    assert cases[0].expected_label == "supported"
    assert cases[0].evidence[0]["chunk_id"] == "chunk-1"
    assert cases[0].annotation_status == "reviewed"


def test_load_claim_cases_rejects_unknown_label(tmp_path):
    path = tmp_path / "claims.jsonl"
    path.write_text(
        '{"id":"one","claim":"结论","evidence":[{"chunk_id":"c","text":"e"}],"label":"maybe"}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unsupported claim label"):
        load_claim_cases(path)


def test_load_claim_cases_preserves_pending_review_status(tmp_path):
    path = tmp_path / "claims.jsonl"
    path.write_text(
        '{"id":"one","claim":"结论","evidence":[{"chunk_id":"c","text":"e"}],'
        '"label":"supported","annotation_status":"pending_human_review"}\n',
        encoding="utf-8",
    )

    cases = load_claim_cases(path)

    assert cases[0].annotation_status == "pending_human_review"


def test_claim_eval_reports_accuracy_f1_grounded_rate_and_review_queue():
    from eval.claim_grounding_eval import ClaimEvalCase

    cases = [
        ClaimEvalCase("one", "支持", ({"chunk_id": "c1", "text": "e1"},), "supported"),
        ClaimEvalCase("two", "冲突", ({"chunk_id": "c2", "text": "e2"},), "contradicted"),
        ClaimEvalCase(
            "three",
            "不足",
            ({"chunk_id": "c3", "text": "e3"},),
            "insufficient_evidence",
        ),
    ]

    def judge(claim, evidence):
        verdict = {"支持": "supported", "冲突": "contradicted", "不足": "supported"}[claim]
        return ClaimJudgment(verdict, "high", "理由", (evidence[0]["chunk_id"],))

    report = evaluate_claim_cases(cases, judge)

    assert report["summary"]["accuracy"] == 0.6667
    assert report["summary"]["macro_f1"] == 0.5556
    assert report["summary"]["claim_grounded_rate"] == 0.6667
    assert report["summary"]["annotation_status_counts"] == {"reviewed": 3}
    assert [row["id"] for row in report["review_queue"]] == ["three"]
    assert report["review_queue"][0]["evidence"][0]["text"] == "e3"


def test_write_baseline_excludes_case_evidence(tmp_path):
    path = tmp_path / "baseline.json"
    report = {
        "run": {"model": "judge"},
        "summary": {"case_count": 1, "accuracy": 1.0},
        "cases": [{"evidence": [{"text": "sensitive full evidence"}]}],
        "review_queue": [],
    }

    write_baseline(report, path)

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "run": {"model": "judge"},
        "summary": {"case_count": 1, "accuracy": 1.0},
    }


def test_display_path_uses_repository_relative_path():
    assert display_path(PROJECT_ROOT / "eval" / "claim_labels.jsonl") == (
        "eval/claim_labels.jsonl"
    )


def test_committed_claim_set_contains_human_reviewed_labels():
    path = Path(__file__).parents[1] / "eval" / "claim_labels.jsonl"

    cases = load_claim_cases(path)

    assert len(cases) == 30
    assert Counter(case.annotation_status for case in cases) == {"reviewed": 30}
    assert Counter(case.expected_label for case in cases) == {
        "supported": 11,
        "contradicted": 11,
        "insufficient_evidence": 8,
    }
