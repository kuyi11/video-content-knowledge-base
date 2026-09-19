from pathlib import Path

import pytest

from eval.run_eval import EvalCase, evaluate_cases, load_cases
from modules.indexer import SearchResult


def _result(rank, video_id, content):
    return SearchResult(
        rank=rank,
        chunk_id=f"chunk-{rank}",
        video_id=video_id,
        profile="medical",
        source_url="https://example.com",
        content=content,
        section="Test",
        start=1.0,
        end=2.0,
        score=1.0,
    )


def test_load_cases_rejects_unpaired_ids(tmp_path):
    questions = tmp_path / "questions.jsonl"
    expected = tmp_path / "expected.jsonl"
    questions.write_text('{"id":"one","question":"question"}\n', encoding="utf-8")
    expected.write_text('{"id":"two","expected_video_ids":["BV1"],"expected_source_refs":["S0001"]}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="ids differ"):
        load_cases(questions, expected)


def test_retrieval_metrics_and_source_coverage():
    case = EvalCase(
        id="one",
        question="question",
        category="fact",
        expected_video_ids=("BV1",),
        expected_source_refs=("S0001", "S0002"),
        expected_sources=(("BV1", "S0001"), ("BV1", "S0002")),
        answer_points=("first",),
        risk_level="low",
        expected_answer=True,
    )

    report = evaluate_cases(
        [case],
        lambda question, top_k: [_result(1, "BV2", "S0003"), _result(2, "BV1", "S0001")],
    )

    assert report["summary"]["recall_at_k"] == 1.0
    assert report["summary"]["mrr"] == 0.5
    assert report["summary"]["source_coverage"] == 0.5


def test_generation_metrics_distinguish_abstention_and_grounding():
    answerable = EvalCase(
        id="answerable",
        question="answerable",
        category="fact",
        expected_video_ids=("BV1",),
        expected_source_refs=("S0001",),
        expected_sources=(("BV1", "S0001"),),
        answer_points=("needed point",),
        risk_level="low",
        expected_answer=True,
    )
    unanswerable = EvalCase(
        id="negative",
        question="negative",
        category="unanswerable",
        expected_video_ids=(),
        expected_source_refs=(),
        expected_sources=(),
        answer_points=(),
        risk_level="low",
        expected_answer=False,
    )

    def retrieve(question, top_k):
        return [_result(1, "BV1", "S0001")]

    def answer(question):
        if question == "negative":
            return "不知道。", {"prompt_eval_count": 10, "eval_count": 4}
        return "needed point [chunk-1 | S0001]", {"prompt_eval_count": 10, "eval_count": 4}

    report = evaluate_cases([answerable, unanswerable], retrieve, answer=answer)

    assert report["summary"]["heuristic_grounded_answer_rate"] == 1.0
    assert report["summary"]["abstention_accuracy"] == 1.0
    assert report["summary"]["avg_prompt_tokens"] == 10.0


def test_generation_metrics_extract_video_id_from_chunk_citation():
    case = EvalCase(
        id="chunk-citation",
        question="question",
        category="fact",
        expected_video_ids=("BV1ABC123456",),
        expected_source_refs=("S0001",),
        expected_sources=(("BV1ABC123456", "S0001"),),
        answer_points=("needed point",),
        risk_level="low",
        expected_answer=True,
    )

    result = _result(1, "BV1ABC123456", "S0001")
    result.chunk_id = "BV1ABC123456__medical_a1b2"
    report = evaluate_cases(
        [case],
        lambda question, top_k: [result],
        answer=lambda question: (
            "needed point [BV1ABC123456__medical_a1b2 | S0001]",
            {},
        ),
    )

    assert report["summary"]["heuristic_grounded_answer_rate"] == 1.0


def test_reranker_changes_order_and_reports_latency():
    case = EvalCase(
        id="rerank",
        question="question",
        category="fact",
        expected_video_ids=("BV1",),
        expected_source_refs=("S0001",),
        expected_sources=(("BV1", "S0001"),),
        answer_points=(),
        risk_level="low",
        expected_answer=True,
    )
    results = [_result(1, "BV2", "S0002"), _result(2, "BV1", "S0001")]

    def rerank(question, candidates, top_k):
        return list(reversed(candidates))

    report = evaluate_cases([case], lambda question, top_k: results, rerank=rerank)

    assert report["summary"]["reranker_enabled"] is True
    assert report["summary"]["recall_at_k"] == 1.0
    assert report["summary"]["mrr"] == 1.0
    assert report["summary"]["avg_rerank_latency_ms"] is not None
    assert report["summary"]["avg_total_retrieval_latency_ms"] is not None
    assert report["cases"][0]["rerank_latency_ms"] >= 0


def test_claim_metrics_use_micro_average_and_export_review_queue():
    cases = [
        EvalCase(
            id=case_id,
            question=case_id,
            category="fact",
            expected_video_ids=("BV1",),
            expected_source_refs=("S0001",),
            expected_sources=(("BV1", "S0001"),),
            answer_points=(),
            risk_level="low",
            expected_answer=True,
        )
        for case_id in ("one", "two")
    ]

    def answer(question):
        if question == "one":
            claim_count, supported, review = 1, 1, []
        else:
            claim_count, supported = 3, 1
            review = [{"claim": "待复核", "needs_review": True}]
        return "answer [chunk-1 | S0001]", {
            "claim_grounding": {
                "status": "ok",
                "claim_count": claim_count,
                "grounded_claim_count": supported,
                "semantic_status": "ok",
                "semantic_claim_count": claim_count,
                "semantically_supported_claim_count": supported,
                "claim_citation_grounding_rate": supported / claim_count,
                "claim_semantic_grounding_rate": supported / claim_count,
                "review_claims": review,
            }
        }

    report = evaluate_cases(
        cases,
        lambda question, top_k: [_result(1, "BV1", "S0001")],
        answer=answer,
    )

    assert report["summary"]["evaluated_claim_count"] == 4
    assert report["summary"]["claim_citation_grounding_rate"] == 0.5
    assert report["summary"]["claim_semantic_grounding_rate"] == 0.5
    assert report["summary"]["claim_review_count"] == 1
    assert report["claim_review_queue"][0]["case_id"] == "two"
