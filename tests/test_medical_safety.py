from modules.medical_safety import evaluate_medical_safety, parse_review_checklist


CHECKLIST = """## 医疗逐字稿与总结
### 消化系统
- `〔疑似转写错误：胃尿保护剂、番茄印〕`：需要回听。
- `〔术语边界不清：反流〕`：需说明症状或疾病。

## 仍需人工确认
- `偏头痛等位征`：待专家确认。
"""


def test_parse_review_checklist_assigns_block_and_warning_severity():
    rules = parse_review_checklist(CHECKLIST)

    assert [(rule.issue_type, rule.severity) for rule in rules] == [
        ("疑似转写错误", "block"),
        ("术语边界不清", "warn"),
        ("人工确认", "block"),
    ]
    assert rules[0].terms == ("胃尿保护剂", "番茄印")


def test_pending_medical_term_blocks_answer():
    rules = parse_review_checklist(CHECKLIST)
    decision = evaluate_medical_safety(
        "胃痛怎么治疗？",
        [
            {
                "text": "可以使用胃尿保护剂",
                "domain": "medical",
                "risk_level": "high",
                "chunk_type": "raw_transcript",
                "quality": "raw_unverified",
            }
        ],
        rules,
    )

    assert decision.action == "block"
    assert decision.matched_terms == ("胃尿保护剂",)


def test_high_risk_question_requires_raw_or_reviewed_evidence():
    decision = evaluate_medical_safety(
        "这个药应该用多少剂量？",
        [
            {
                "text": "二次总结",
                "domain": "medical",
                "risk_level": "high",
                "chunk_type": "structured_summary",
                "quality": "derived_pending_review",
            }
        ],
        [],
    )

    assert decision.action == "block"
    assert "lacks raw or human-reviewed evidence" in decision.reasons[0]


def test_high_risk_question_rejects_summary_marked_as_raw_evidence_required():
    decision = evaluate_medical_safety(
        "这个药应该用多少剂量？",
        [
            {
                "text": "人工整理的总结",
                "domain": "medical",
                "risk_level": "high",
                "chunk_type": "structured_summary",
                "quality": "human_reviewed",
                "source_of_truth": False,
                "answer_policy": "summary_requires_raw_evidence",
            }
        ],
        [],
    )

    assert decision.action == "block"
    assert "requires raw evidence" in decision.reasons[0]


def test_unreviewed_raw_medical_evidence_is_allowed_with_warning():
    decision = evaluate_medical_safety(
        "胸痛应该如何处理？",
        [
            {
                "text": "原始逐字稿片段",
                "domain": "medical",
                "risk_level": "high",
                "chunk_type": "raw_transcript",
                "quality": "raw_unverified",
            }
        ],
        [],
    )

    assert decision.action == "warn"


def test_non_medical_evidence_is_not_gated():
    decision = evaluate_medical_safety(
        "视频讲了什么？",
        [{"text": "普通内容", "domain": "fitness", "risk_level": "low"}],
        [],
    )

    assert decision.action == "allow"
