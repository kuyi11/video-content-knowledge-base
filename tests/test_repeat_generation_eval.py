from eval.repeat_generation_eval import aggregate_reports


def _report(citation_rate, semantic_rate, review_count, grounded, supported):
    grounding = {
        "status": "ok",
        "claim_count": 2,
        "grounded_claim_count": grounded,
        "semantic_status": "ok",
        "semantic_claim_count": 2,
        "semantically_supported_claim_count": supported,
    }
    return {
        "summary": {
            "heuristic_grounded_answer_rate": 0.5,
            "abstention_accuracy": 1.0,
            "answer_point_coverage": 0.75,
            "avg_generation_latency_ms": 100.0,
            "claim_citation_grounding_rate": citation_rate,
            "claim_semantic_grounding_rate": semantic_rate,
            "claim_review_count": review_count,
        },
        "cases": [{"usage": {"claim_grounding": grounding}}],
        "claim_review_queue": [{}] * review_count,
    }


def test_aggregate_reports_tracks_variance_and_weighted_claim_rates():
    aggregate = aggregate_reports(
        [
            _report(1.0, 1.0, 0, grounded=2, supported=2),
            _report(0.5, 0.5, 1, grounded=1, supported=1),
        ]
    )

    assert aggregate["run_count"] == 2
    assert aggregate["metrics"]["claim_citation_grounding_rate"] == {
        "mean": 0.75,
        "min": 0.5,
        "max": 1.0,
        "stdev": 0.3536,
    }
    assert aggregate["weighted_claim_metrics"] == {
        "evaluated_claim_count": 4,
        "claim_citation_grounding_rate": 0.75,
        "semantic_evaluated_claim_count": 4,
        "claim_semantic_grounding_rate": 0.75,
        "review_count": 1,
    }
