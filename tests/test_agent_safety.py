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
            "meta": {
                "top_k": top_k,
                "result_count": 1,
                "index_generation_id": "generation-test",
            },
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
    assert usage["answer_confidence"]["level"] == "medium"
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
    assert usage["output_gate"]["action"] == "not_applicable"


def test_output_gate_removes_unadmitted_claim_but_keeps_admitted_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "QUERY_LOGGING_ENABLED", True)
    monkeypatch.setattr(agent, "INDEX_DIR", tmp_path / "index")
    checklist = tmp_path / "review.md"
    checklist.write_text("# no pending terms\n", encoding="utf-8")
    monkeypatch.setattr(agent, "TERMINOLOGY_REVIEW_PATH", checklist)
    monkeypatch.setattr(
        agent,
        "ask_with_usage",
        lambda *args, **kwargs: (
            "【结论】\n"
            "1. 有证据的结论 [BVmedical_raw_1 | S0001]\n"
            "2. 无证据的结论 [missing_chunk | S9999]",
            {},
        ),
    )

    answer, usage = agent.answer_question_with_usage(FakeEngine(), "视频讲了什么？")

    assert "有证据的结论" in answer
    assert "无证据的结论" not in answer
    assert "已省略" in answer
    assert usage["output_gate"]["action"] == "redact"
    assert usage["output_gate"]["allowed_cited_chunk_ids"] == ["BVmedical_raw_1"]
    assert usage["output_gate"]["blocked_claims"] == [
        {
            "claim_id": "C0002",
            "line": 3,
            "reasons": ["chunk_not_in_retrieval"],
        }
    ]
    assert usage["answer_confidence"]["level"] == "low"
    log = json.loads(Path(usage["retrieval_log_path"]).read_text(encoding="utf-8"))
    assert log["answer"] == answer
    assert log["used_chunk_ids"] == ["BVmedical_raw_1"]
    assert log["output_gate"]["action"] == "redact"


def test_claim_evidence_audit_can_be_persisted_independently_of_query_log(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(agent, "QUERY_LOGGING_ENABLED", False)
    monkeypatch.setattr(agent, "CLAIM_EVIDENCE_AUDIT_ENABLED", True)
    monkeypatch.setattr(agent, "INDEX_DIR", tmp_path / "index")
    checklist = tmp_path / "review.md"
    checklist.write_text("# no pending terms\n", encoding="utf-8")
    monkeypatch.setattr(agent, "TERMINOLOGY_REVIEW_PATH", checklist)
    monkeypatch.setattr(
        agent,
        "ask_with_usage",
        lambda *args, **kwargs: ("结论 [BVmedical_raw_1 | S0001]", {}),
    )

    _, usage = agent.answer_question_with_usage(FakeEngine(), "视频讲了什么？")

    audit = usage["evidence_audit"]
    assert usage["retrieval_log_path"] is None
    assert audit["persisted"] is True
    assert audit["index_generation_id"] == "generation-test"
    record = json.loads(Path(audit["record_path"]).read_text(encoding="utf-8"))
    assert record["claims"][0]["claim_id"] == "C0001"
    assert len(record["evidence_units"][0]["evidence_fingerprint"]) == 64


def test_output_gate_blocks_answer_when_no_claim_is_admitted(tmp_path, monkeypatch):
    checklist = tmp_path / "review.md"
    checklist.write_text("# no pending terms\n", encoding="utf-8")
    monkeypatch.setattr(agent, "TERMINOLOGY_REVIEW_PATH", checklist)
    monkeypatch.setattr(
        agent,
        "ask_with_usage",
        lambda *args, **kwargs: ("没有引用的结论", {}),
    )

    answer, usage = agent.answer_question_with_usage(FakeEngine(), "视频讲了什么？")

    assert answer == agent.OUTPUT_GATE_BLOCK_MESSAGE
    assert usage["output_gate"]["action"] == "block"
    assert usage["output_gate"]["blocked_claims"][0]["reasons"] == [
        "missing_citation"
    ]
    assert usage["output_gate"]["allowed_cited_chunk_ids"] == []


def test_multi_query_coverage_is_checked_with_optional_semantic_judge(monkeypatch):
    engine = FakeEngine()
    original_query = engine.query

    def multi_query(question, top_k=5):
        result = original_query(question, top_k)
        result["meta"]["sub_queries"] = ["问题一", "问题二"]
        result["meta"]["coverage"] = [
            {"query": "问题一", "status": "covered", "result_count": 1},
            {"query": "问题二", "status": "covered", "result_count": 1},
        ]
        return result

    engine.query = multi_query
    calls = []

    class Client:
        def chat(self, **kwargs):
            calls.append(kwargs)
            return {"message": {"content": '{"status":"covered","confidence":"high","rationale":"证据覆盖问题。"}'}}

    monkeypatch.setattr(agent, "_ollama_client", Client())
    monkeypatch.setattr(agent, "ask_with_usage", lambda *args, **kwargs: ("不知道。", {}))

    _, usage = agent.answer_question_with_usage(
        engine, "问题一和问题二", semantic_grounding=True
    )

    assert usage["evidence_coverage"]["status"] == "covered"
    assert len(calls) == 2


def test_conflicting_evidence_lowers_answer_confidence(monkeypatch):
    class Client:
        def chat(self, **kwargs):
            return {"message": {"content": '{"status":"conflict","conflicts":[{"chunk_ids":["a","b"],"rationale":"结论相反"}]}'}}

    monkeypatch.setattr(agent, "_ollama_client", Client())
    monkeypatch.setattr(agent, "ask_with_usage", lambda *args, **kwargs: ("不知道。", {}))
    engine = FakeEngine()
    original_query = engine.query

    def conflicting_query(question, top_k=5):
        result = original_query(question, top_k)
        result["results"].append({**result["results"][0], "chunk_id": "b", "text": "相反证据"})
        result["results"][0]["chunk_id"] = "a"
        return result

    engine.query = conflicting_query
    _, usage = agent.answer_question_with_usage(engine, "问题", semantic_grounding=True)

    assert usage["evidence_conflicts"]["status"] == "conflict"
    assert usage["answer_confidence"]["level"] == "low"
