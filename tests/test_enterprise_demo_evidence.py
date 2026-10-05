import json
from pathlib import Path


EVIDENCE = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "demo-evidence"
    / "enterprise-demo-latest.json"
)


def test_enterprise_demo_evidence_is_public_and_passes_quality_gate():
    report = json.loads(EVIDENCE.read_text(encoding="utf-8"))

    assert report["schema_version"] == "enterprise-demo-evidence-v1"
    assert report["status"] == "passed"
    assert report["health"]["status"] == "ok"
    assert report["health"]["indexed_document_count"] == 16
    assert report["health"]["indexed_chunk_count"] == 45
    assert report["health"]["missing_from_index"] == []
    assert report["health"]["extra_in_index"] == []
    assert report["health"]["changed_sources"] == []
    assert report["health"]["metadata_errors"] == []

    cases = report["cases"]
    assert len(cases) == report["acceptance"]["case_count"] == 5
    assert sum(case["passed"] for case in cases) == report["acceptance"]["passed_count"] == 5
    for case in cases:
        assert case["passed"] is True
        assert case["answer_call_completed"] is True
        assert case["confidence_level"] == report["acceptance"]["required_confidence"]
        assert case["claim_retention_ratio"] >= report["acceptance"]["minimum_claim_retention_ratio"]
        assert case["blocked_claim_count"] == 0
        assert case["citation_count"] > 0
        assert case["output_gate_action"] == "allow"
