import json
from types import SimpleNamespace

import pytest

from modules.asr.review import (
    PROTOCOL_VERSION,
    build_review_request,
    build_review_segments,
    run_review_subprocess,
    validate_review_request,
)


def test_build_review_request_validates_segment_bounds(tmp_path):
    request = build_review_request(
        tmp_path / "audio.wav",
        [{"start": 10, "end": 20, "context": "term"}],
        request_id="review-1",
    )

    assert request["protocol_version"] == PROTOCOL_VERSION
    assert request["request_id"] == "review-1"
    assert request["segments"][0]["start"] == 10

    request["segments"][0]["end"] = 5
    with pytest.raises(ValueError, match="invalid bounds"):
        validate_review_request(request)


def test_build_review_segments_expands_and_merges_timestamped_corrections():
    segments = build_review_segments(
        [
            {
                "segment_start": 100,
                "segment_end": 104,
                "original": "鸡糖原",
                "candidate": "肌糖原",
                "review_needed": True,
            },
            {
                "segment_start": 110,
                "segment_end": 112,
                "original": "肌糖缘",
                "candidate": "肌糖原",
                "review_needed": True,
            },
            {
                "segment_start": 200,
                "segment_end": 202,
                "review_needed": False,
            },
        ],
        padding_seconds=10,
    )

    assert segments == [
        {
            "start": 90.0,
            "end": 122.0,
            "context": "鸡糖原=>肌糖原; 肌糖缘=>肌糖原",
        }
    ]

def test_run_review_subprocess_requires_matching_json_response(monkeypatch, tmp_path):
    request = build_review_request(
        tmp_path / "audio.wav",
        [{"start": 0, "end": 5}],
        request_id="review-2",
    )
    response = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": "review-2",
        "status": "ok",
        "results": [{"start": 0, "end": 5, "text": "result"}],
        "error": None,
    }
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(response),
            stderr="model logs",
        )

    monkeypatch.setattr("modules.asr.review.subprocess.run", fake_run)
    result = run_review_subprocess(
        request,
        python_executable="python.exe",
        worker_path="worker.py",
        timeout_seconds=30,
    )

    assert result["results"][0]["text"] == "result"
    assert calls[0][1]["capture_output"] is True
    assert calls[0][1]["timeout"] == 30


def test_run_review_subprocess_rejects_invalid_stdout(monkeypatch, tmp_path):
    request = build_review_request(
        tmp_path / "audio.wav",
        [{"start": 0, "end": 5}],
        request_id="review-3",
    )
    monkeypatch.setattr(
        "modules.asr.review.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="log leaked to stdout",
            stderr="failure",
        ),
    )

    with pytest.raises(RuntimeError, match="invalid JSON"):
        run_review_subprocess(
            request,
            python_executable="python.exe",
            worker_path="worker.py",
        )