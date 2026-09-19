import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

import api


class FakeEngine:
    index = type("Index", (), {"model": object()})()


def test_query_returns_structured_answer_and_citations(monkeypatch):
    monkeypatch.setattr(api.service, "engine", lambda: FakeEngine())
    monkeypatch.setattr(
        api.agent,
        "answer_question_with_usage",
        lambda *args, **kwargs: (
            "结论 [BV1_raw_1 | S0001]",
            {
                "claim_grounding": {
                    "valid_cited_chunk_ids": ["BV1_raw_1"],
                    "valid_cited_source_refs": ["S0001"],
                },
                "safety_gate": {"action": "allow"},
                "retrieval_log_path": "index/query_logs/test.json",
                "retrieval": {
                    "results": [
                        {
                            "chunk_id": "BV1_raw_1",
                            "source_refs": ["S0001"],
                            "source_path": "raw.md",
                            "video_id": "BV1",
                            "start": 1.0,
                            "end": 2.0,
                        }
                    ],
                    "meta": {"result_count": 1},
                },
            },
        ),
    )

    response = TestClient(api.app).post("/query", json={"question": "问题"})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"].startswith("结论")
    assert body["citations"][0]["chunk_id"] == "BV1_raw_1"
    assert body["claim_grounding"]["valid_cited_source_refs"] == ["S0001"]


def test_query_rejects_empty_question():
    response = TestClient(api.app).post("/query", json={"question": ""})

    assert response.status_code == 422
