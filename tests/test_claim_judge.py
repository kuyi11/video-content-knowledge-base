import json

import pytest

from modules.claim_judge import OllamaClaimJudge, parse_judgment


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return {"message": {"content": json.dumps(self.payload, ensure_ascii=False)}}


class SequenceClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.payloads.pop(0)
        return {"message": {"content": json.dumps(payload, ensure_ascii=False)}}


def test_ollama_claim_judge_returns_validated_structured_result():
    client = FakeClient(
        {
            "verdict": "supported",
            "confidence": "high",
            "rationale": "证据直接支持。",
            "evidence_chunk_ids": ["chunk-1"],
        }
    )
    judge = OllamaClaimJudge(client, "local-model")

    result = judge("一个结论", [{"chunk_id": "chunk-1", "text": "证据", "source_refs": []}])

    assert result.verdict == "supported"
    assert result.evidence_chunk_ids == ("chunk-1",)
    assert client.calls[0]["format"] == "json"
    assert client.calls[0]["options"]["temperature"] == 0


def test_parse_judgment_rejects_hallucinated_chunk_id():
    with pytest.raises(ValueError, match="unavailable chunks"):
        parse_judgment(
            {
                "verdict": "supported",
                "confidence": "high",
                "rationale": "理由",
                "evidence_chunk_ids": ["unknown"],
            },
            {"chunk-1"},
        )


def test_parse_judgment_accepts_fenced_json():
    result = parse_judgment(
        """```json
{"verdict":"insufficient_evidence","confidence":"medium","rationale":"证据不足","evidence_chunk_ids":[]}
```""",
        {"chunk-1"},
    )

    assert result.verdict == "insufficient_evidence"


def test_ollama_claim_judge_retries_invalid_chunk_ids_once():
    client = SequenceClient(
        [
            {
                "verdict": "supported",
                "confidence": "high",
                "rationale": "理由",
                "evidence_chunk_ids": ["S0001"],
            },
            {
                "verdict": "supported",
                "confidence": "high",
                "rationale": "证据直接支持。",
                "evidence_chunk_ids": ["chunk-1"],
            },
        ]
    )

    result = OllamaClaimJudge(client, "local-model")(
        "一个结论",
        [{"chunk_id": "chunk-1", "text": "证据", "source_refs": ["S0001"]}],
    )

    assert result.evidence_chunk_ids == ("chunk-1",)
    assert len(client.calls) == 2
    assert "Sxxxx 是 source_refs" in client.calls[1]["messages"][-1]["content"]
