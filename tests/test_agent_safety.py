import json
from pathlib import Path

import modules.agent as agent


class FakeEngine:
    def query(self, question, top_k=5):
        return {
            "question": question,
            "status": "ok",
            "results": [
                {
                    "rank": 1,
                    "text": "视频转录中出现胃尿保护剂",
                    "score": 1.0,
                    "video_id": "BVmedical",
                    "profile": "medical",
                    "source_url": "",
                    "start": 10.0,
                    "end": 20.0,
                    "has_timestamp": True,
                    "chunk_id": "BVmedical_raw_1",
                    "document_id": "BVmedical__raw",
                    "source_path": "raw.md",
                    "chunk_type": "raw_transcript",
                    "domain": "medical",
                    "quality": "raw_unverified",
                    "review_status": "pending",
                    "source_of_truth": True,
                    "risk_level": "high",
                    "answer_policy": "retrieval_only_unreviewed",
                    "source_refs": ["S0001"],
                }
            ],
            "meta": {"top_k": top_k, "result_count": 1},
        }


def test_answer_gate_blocks_pending_term_without_calling_llm(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "QUERY_LOGGING_ENABLED", True)
    checklist = tmp_path / "review.md"
    checklist.write_text(
        "## 消化系统\n- `〔疑似转写错误：胃尿保护剂〕`：需要回听。\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(agent, "TERMINOLOGY_REVIEW_PATH", checklist)
    monkeypatch.setattr(agent, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(
        agent,
        "ask_with_usage",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("LLM must not be called")),
    )

    answer, usage = agent.answer_question_with_usage(FakeEngine(), "胃痛怎么治疗？")

    assert "待人工核查" in answer
    assert usage["safety_gate"]["action"] == "block"
    log = json.loads((tmp_path / "index" / "query_logs" / next(
        (tmp_path / "index" / "query_logs").iterdir()
    ).name).read_text(encoding="utf-8"))
    assert log["safety_gate"]["matched_terms"] == ["胃尿保护剂"]
    assert log["used_chunk_ids"] == []
    assert log["claim_grounding"]["status"] == "not_applicable"


def test_context_uses_exact_chunk_id_without_numbered_fragment_label():
    context = agent._build_context(FakeEngine().query("question")["results"])

    assert "chunk_id: BVmedical_raw_1" in context
    assert "citation: [BVmedical_raw_1 | S0001]" in context
    assert "[片段1]" not in context


def test_answer_log_records_validated_claims(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "QUERY_LOGGING_ENABLED", True)
    checklist = tmp_path / "review.md"
    checklist.write_text("# no pending terms\n", encoding="utf-8")
    monkeypatch.setattr(agent, "TERMINOLOGY_REVIEW_PATH", checklist)
    monkeypatch.setattr(agent, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(
        agent,
        "ask_with_usage",
        lambda *args, **kwargs: ("结论 [BVmedical_raw_1 | S0001]", {"eval_count": 1}),
    )

    def judge(claim, evidence):
        return {
            "verdict": "supported",
            "confidence": "high",
            "rationale": "证据直接支持。",
            "evidence_chunk_ids": ["BVmedical_raw_1"],
        }

    _, usage = agent.answer_question_with_usage(
        FakeEngine(),
        "视频讲了什么？",
        semantic_grounding=True,
        semantic_judge=judge,
    )

    assert usage["claim_grounding"]["claim_citation_grounding_rate"] == 1.0
    assert usage["claim_grounding"]["claim_semantic_grounding_rate"] == 1.0
    log_path = Path(usage["retrieval_log_path"])
    log = json.loads(log_path.read_text(encoding="utf-8"))
    assert log["used_chunk_ids"] == ["BVmedical_raw_1"]


def test_model_abstention_is_excluded_from_claim_grounding(tmp_path, monkeypatch):
    checklist = tmp_path / "review.md"
    checklist.write_text("# no pending terms\n", encoding="utf-8")
    monkeypatch.setattr(agent, "TERMINOLOGY_REVIEW_PATH", checklist)
    monkeypatch.setattr(agent, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(agent, "ask_with_usage", lambda *args, **kwargs: ("不知道。", {}))

    _, usage = agent.answer_question_with_usage(
        FakeEngine(),
        "视频是否回答了这个问题？",
        semantic_grounding=True,
        semantic_judge=lambda *args: (_ for _ in ()).throw(AssertionError("must not judge")),
    )

    assert usage["claim_grounding"]["status"] == "not_applicable"
    assert usage["claim_grounding"]["reason"] == "model_abstention"
