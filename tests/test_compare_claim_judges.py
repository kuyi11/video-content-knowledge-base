import json

from tools.compare_claim_judges import compare_reports


def write_report(path, rows):
    path.write_text(json.dumps({"cases": rows}, ensure_ascii=False), encoding="utf-8")


def test_compare_reports_marks_disagreement_and_low_confidence(tmp_path):
    qwen = tmp_path / "qwen.json"
    glm = tmp_path / "glm.json"
    base = {
        "id": "x",
        "claim": "结论",
        "expected_label": "supported",
        "confidence": "high",
        "rationale": "理由",
    }
    write_report(qwen, [{**base, "predicted_label": "supported"}, {**base, "id": "y", "predicted_label": "supported"}])
    write_report(glm, [{**base, "predicted_label": "supported"}, {**base, "id": "y", "predicted_label": "insufficient_evidence", "confidence": "low"}])

    report = compare_reports(qwen, glm)

    assert report["summary"]["agreement_rate"] == 0.5
    assert report["summary"]["disagreement_count"] == 1
    assert report["disagreements"][0]["id"] == "y"
    assert {row["id"] for row in report["review_queue"]} == {"y"}
