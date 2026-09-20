from pathlib import Path
import json


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "docker_demo.ps1"
PUBLIC_EVIDENCE = Path(__file__).resolve().parents[1] / "docs" / "demo-evidence" / "docker-demo-latest.json"


def test_demo_defaults_are_grounded_in_known_sample_videos():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "脑梗发生后多久内需要尽快就医" not in text
    assert 'NormalVideoId = "BV1hN8z6MEYS"' in text
    assert 'BlockedVideoId = "BV1H4UbBMExm"' in text
    assert 'domain = "wellness"' in text
    assert 'domain = "medical"' in text
    assert 'chunk_type = "raw_transcript"' in text


def test_demo_validates_retrieval_and_both_safety_layers():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "evidenceMatched" in text
    assert "expected_safety_gate" in text
    assert "actual_safety_gate" in text
    assert "expected_output_gate" in text
    assert "actual_output_gate" in text
    assert "answer_nonempty" in text
    assert "citation_count" in text
    assert '"docs\\demo-evidence"' in text
    assert 'throw "Docker demo validation failed.' in text


def test_public_demo_evidence_records_a_passing_real_run():
    report = json.loads(PUBLIC_EVIDENCE.read_text(encoding="utf-8"))

    assert report["status"] == "passed"
    assert report["index"]["status"] == "ok"
    assert all(case["evidence_matched"] and case["passed"] for case in report["cases"])
    normal = next(case for case in report["cases"] if case["name"] == "normal_answer")
    assert normal["answer_nonempty"] is True
    assert normal["citation_count"] > 0
