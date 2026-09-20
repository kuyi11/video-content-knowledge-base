import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

import api


class FakeEngine:
    index = type("Index", (), {"model": object()})()


def test_query_returns_structured_answer_and_citations(monkeypatch):
    monkeypatch.setattr(api, "API_TOKEN", "test-token")
    monkeypatch.setattr(api.service, "engine", lambda: FakeEngine())
    monkeypatch.setattr(
        api.agent,
        "answer_question_with_usage",
        lambda *args, **kwargs: (
            "结论 [BV1_raw_1 | S0001]",
            {
                "claim_grounding": {
                    "valid_cited_chunk_ids": ["BV1_raw_1", "BV1_blocked_2"],
                    "valid_cited_source_refs": ["S0001"],
                },
                "safety_gate": {"action": "allow"},
                "output_gate": {
                    "action": "allow",
                    "allowed_cited_chunk_ids": ["BV1_raw_1"],
                },
                "evidence_audit": {
                    "audit_id": "audit-1",
                    "persisted": True,
                    "index_generation_id": "generation-1",
                    "record_path": "C:/private/audit.json",
                },
                "retrieval_log_path": "index/query_logs/test.json",
                "retrieval": {
                    "results": [
                        {
                            "chunk_id": "BV1_raw_1",
                            "source_refs": ["S0001"],
                            "source_path": "raw.md",
                            "source_url": "https://example.invalid/video/BV1?vd_source=secret",
                            "video_id": "BV1",
                            "start": 1.0,
                            "end": 2.0,
                        },
                        {
                            "chunk_id": "BV1_blocked_2",
                            "source_refs": ["S0002"],
                            "video_id": "BV1",
                            "start": 3.0,
                            "end": 4.0,
                        }
                    ],
                    "meta": {"result_count": 1},
                },
            },
        ),
    )

    response = TestClient(api.app).post(
        "/query", json={"question": "问题"}, headers={"Authorization": "Bearer test-token"}
    )

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["request_id"], str) and len(body["request_id"]) == 32
    assert body["answer"].startswith("结论")
    assert body["citations"][0]["chunk_id"] == "BV1_raw_1"
    assert len(body["citations"]) == 1
    assert "source_path" not in body["citations"][0]
    assert "source_path" not in body["retrieval"]["results"][0]
    assert "source_url" not in body["retrieval"]["results"][0]
    assert body["claim_grounding"]["valid_cited_source_refs"] == ["S0001"]
    assert body["output_gate"]["action"] == "allow"
    assert body["evidence_audit"]["audit_id"] == "audit-1"
    assert "record_path" not in body["evidence_audit"]


def test_query_rejects_empty_question(monkeypatch):
    monkeypatch.setattr(api, "API_TOKEN", "test-token")
    response = TestClient(api.app).post(
        "/query", json={"question": ""}, headers={"Authorization": "Bearer test-token"}
    )

    assert response.status_code == 422


def test_query_requires_bearer_token_when_configured(monkeypatch):
    monkeypatch.setattr(api, "API_TOKEN", "test-token")

    denied = TestClient(api.app).post("/query", json={"question": "问题"})
    assert denied.status_code == 401

    monkeypatch.setattr(api.service, "engine", lambda: FakeEngine())
    monkeypatch.setattr(
        api.agent,
        "answer_question_with_usage",
        lambda *args, **kwargs: ("答案", {"claim_grounding": {}, "retrieval": {"results": []}}),
    )
    allowed = TestClient(api.app).post(
        "/query", json={"question": "问题"}, headers={"Authorization": "Bearer test-token"}
    )
    assert allowed.status_code == 200


def test_query_rejects_non_local_client_without_configured_token():
    response = TestClient(api.app).post("/query", json={"question": "问题"})
    assert response.status_code == 401


def test_query_returns_503_for_retrieval_error(monkeypatch):
    monkeypatch.setattr(api, "API_TOKEN", "test-token")
    monkeypatch.setattr(api.service, "engine", lambda: FakeEngine())
    monkeypatch.setattr(
        api.agent,
        "answer_question_with_usage",
        lambda *args, **kwargs: ("检索系统异常", {
            "claim_grounding": {},
            "retrieval": {"status": "error", "results": []},
        }),
    )
    response = TestClient(api.app).post(
        "/query", json={"question": "问题"}, headers={"Authorization": "Bearer test-token"}
    )
    assert response.status_code == 503


def test_query_rejects_unknown_metadata_filter(monkeypatch):
    monkeypatch.setattr(api, "API_TOKEN", "test-token")
    response = TestClient(api.app).post(
        "/query",
        json={"question": "问题", "filters": {"private_field": "secret"}},
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 400


def test_query_enforces_per_client_rate_limit(monkeypatch):
    monkeypatch.setattr(api, "API_TOKEN", "test-token")
    monkeypatch.setattr(api, "API_RATE_LIMIT_PER_MINUTE", 1)
    api._request_times.clear()
    monkeypatch.setattr(api.service, "engine", lambda: FakeEngine())
    monkeypatch.setattr(
        api.agent,
        "answer_question_with_usage",
        lambda *args, **kwargs: ("答案", {"claim_grounding": {}, "retrieval": {"results": []}}),
    )
    client = TestClient(api.app)
    headers = {"Authorization": "Bearer test-token"}

    assert client.post("/query", json={"question": "问题"}, headers=headers).status_code == 200
    limited = client.post("/query", json={"question": "问题"}, headers=headers)

    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"


def test_metrics_returns_aggregates_without_query_content(monkeypatch):
    monkeypatch.setattr(api, "API_TOKEN", "test-token")
    with api._metrics_lock:
        api._metrics["total_queries"] = 0
        api._metrics["successful_queries"] = 0
        api._metrics["failed_queries"] = 0
        api._metrics["blocked_queries"] = 0
        api._metrics["total_latency_ms"] = 0.0
        api._metrics["latencies_ms"].clear()
        api._metrics["estimated_input_tokens"] = 0
        api._metrics["estimated_output_tokens"] = 0
        api._metrics["estimated_cost_usd"] = 0.0
    api._record_query_metrics(
        latency_ms=12.5,
        success=True,
        blocked=False,
        input_text="私密问题",
        output_text="私密答案",
    )
    response = TestClient(api.app).get(
        "/metrics", headers={"Authorization": "Bearer test-token"}
    )
    assert response.status_code == 200
    body = response.json()["metrics"]
    assert body["total_queries"] == 1
    assert body["latency_ms"]["p50"] == 12.5
    assert "私密问题" not in response.text
    assert "私密答案" not in response.text
