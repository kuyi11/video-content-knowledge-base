from eval.compare_reranker import baseline_summary


def test_baseline_summary_preserves_corpus_and_comparison_metrics():
    report = {
        "run": {
            "created_at": "2026-09-18T00:00:00+08:00",
            "top_k": 5,
            "candidate_k": 20,
            "reranker_model": "local/reranker",
            "index_document_count": 33,
            "index_chunk_count": 533,
        },
        "rrf": {
            "summary": {
                "case_count": 30,
                "answerable_case_count": 28,
                "recall_at_k": 0.78,
                "mrr": 0.67,
                "source_coverage": 0.78,
                "avg_total_retrieval_latency_ms": 45.0,
            }
        },
        "rrf_reranker": {
            "summary": {
                "recall_at_k": 0.89,
                "mrr": 0.78,
                "source_coverage": 0.875,
                "avg_retrieval_latency_ms": 596.0,
                "avg_rerank_latency_ms": 575.0,
                "avg_total_retrieval_latency_ms": 1171.0,
            }
        },
    }

    summary = baseline_summary(report)

    assert summary["corpus"] == {"document_count": 33, "chunk_count": 533}
    assert summary["rrf"]["recall_at_5"] == 0.78
    assert summary["rrf_reranker"]["recall_at_5"] == 0.89
    assert summary["rrf_reranker"]["model"] == "local/reranker"


def test_baseline_summary_labels_recall_with_configured_top_k():
    report = {
        "run": {
            "created_at": "2026-09-18T00:00:00+08:00",
            "top_k": 3,
            "candidate_k": 10,
            "reranker_model": "local/reranker",
            "index_document_count": 1,
            "index_chunk_count": 2,
        },
        "rrf": {
            "summary": {
                "case_count": 1,
                "answerable_case_count": 1,
                "recall_at_k": 0.5,
                "mrr": 0.5,
                "source_coverage": 0.5,
                "avg_total_retrieval_latency_ms": 1.0,
            }
        },
        "rrf_reranker": {
            "summary": {
                "recall_at_k": 1.0,
                "mrr": 1.0,
                "source_coverage": 1.0,
                "avg_retrieval_latency_ms": 1.0,
                "avg_rerank_latency_ms": 1.0,
                "avg_total_retrieval_latency_ms": 2.0,
            }
        },
    }

    summary = baseline_summary(report)

    assert summary["rrf"]["recall_at_3"] == 0.5
    assert "recall_at_5" not in summary["rrf"]
