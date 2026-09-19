from modules.claim_grounding import is_abstention_answer, not_applicable, validate_claims
from modules.claim_judge import ClaimJudgment


RESULTS = [
    {"chunk_id": "BVmedical_raw_1", "source_refs": ["S0001", "S0002"]},
    {"chunk_id": "BVmedical_summary_1", "source_refs": []},
]


def test_claim_grounding_checks_chunk_and_source_ref_membership():
    report = validate_claims(
        """【结论】
胃痛需要由专业人员复核 [BVmedical_raw_1 | S0001]
这一句没有引用
错误来源 [unknown_chunk | S0009]
摘要事实 [BVmedical_summary_1 | N/A]
""",
        RESULTS,
    )

    assert report["claim_count"] == 4
    assert report["grounded_claim_count"] == 2
    assert report["invalid_claim_count"] == 2
    assert report["valid_cited_chunk_ids"] == ["BVmedical_raw_1", "BVmedical_summary_1"]
    assert report["claims"][1]["errors"] == ["missing_citation"]
    assert report["claims"][2]["errors"] == ["chunk_not_in_retrieval"]


def test_claim_grounding_rejects_ref_not_belonging_to_cited_chunk():
    report = validate_claims("内容 [BVmedical_raw_1 | S0009]", RESULTS)

    assert report["grounded_claim_count"] == 0
    assert report["claims"][0]["errors"] == ["source_refs_not_in_chunk"]


def test_not_applicable_keeps_safety_blocks_out_of_grounding_rate():
    report = not_applicable("medical_safety_block")

    assert report["status"] == "not_applicable"
    assert report["claim_citation_grounding_rate"] is None
    assert report["claim_semantic_grounding_rate"] is None


def test_semantic_grounding_classifies_valid_claims_and_reviews_failures():
    def judge(claim, evidence):
        assert evidence[0]["text"] == "原始证据说明需要复核"
        return ClaimJudgment("supported", "high", "证据直接支持。", ("BVmedical_raw_1",))

    report = validate_claims(
        "需要复核 [BVmedical_raw_1 | S0001]\n这一句没有引用",
        [{**RESULTS[0], "text": "原始证据说明需要复核"}],
        semantic_judge=judge,
    )

    assert report["semantic_status"] == "ok"
    assert report["semantic_claim_count"] == 2
    assert report["semantically_supported_claim_count"] == 1
    assert report["insufficient_evidence_claim_count"] == 1
    assert report["claim_semantic_grounding_rate"] == 0.5
    assert report["claims"][0]["semantic_verdict"] == "supported"
    assert report["claims"][1]["semantic_verdict"] == "insufficient_evidence"
    assert [item["claim"] for item in report["review_claims"]] == ["这一句没有引用"]


def test_semantic_judge_failure_degrades_to_review_instead_of_failing_answer():
    def broken_judge(claim, evidence):
        raise RuntimeError("judge unavailable")

    report = validate_claims(
        "需要复核 [BVmedical_raw_1 | S0001]",
        [{**RESULTS[0], "text": "原始证据"}],
        semantic_judge=broken_judge,
    )

    assert report["semantic_status"] == "partial_failure"
    assert report["claims"][0]["semantic_error"] == "judge unavailable"
    assert report["claims"][0]["needs_review"] is True


def test_heading_prefix_is_not_part_of_claim_and_abstention_is_detected():
    report = validate_claims(
        "【结论】内容 [BVmedical_raw_1 | S0001]",
        [{**RESULTS[0], "text": "内容"}],
    )

    assert report["claims"][0]["claim"] == "内容"
    assert is_abstention_answer("【结论】不知道。\n\n【依据】") is True
    assert is_abstention_answer("1. 不知道。[chunk-1 | N/A]") is True
    assert is_abstention_answer("不知道。【推测】材料可能涉及其他疾病。") is True
    assert is_abstention_answer("【结论】证据不足但仍有结论") is False


def test_empty_source_ref_is_valid_for_chunk_without_source_refs():
    report = validate_claims(
        "内容 [BVmedical_summary_1 | ]",
        RESULTS,
    )

    assert report["grounded_claim_count"] == 1
    assert report["claims"][0]["errors"] == []
