from types import SimpleNamespace

from modules.reranker import CrossEncoderReranker


class FakeCrossEncoder:
    def __init__(self, scores):
        self.scores = scores

    def predict(self, pairs, show_progress_bar=False):
        assert show_progress_bar is False
        return self.scores


def _result(chunk_id, refs, content="content", video_id="BV1"):
    return SimpleNamespace(
        chunk_id=chunk_id,
        video_id=video_id,
        source_refs=refs,
        content=content,
        rank=0,
        score=0.0,
    )


def test_rerank_deduplicates_same_video_and_source_refs_after_scoring():
    reranker = CrossEncoderReranker.__new__(CrossEncoderReranker)
    reranker.model = FakeCrossEncoder([0.99, 0.98, 0.7, 0.6])
    results = [
        _result("duplicate-a", ["S0001", "S0002"]),
        _result("duplicate-b", ["S0002", "S0001"]),
        _result("evidence-b", ["S0003"]),
        _result("evidence-c", ["S0004"]),
    ]

    ranked = reranker.rerank("query", results, top_k=3)

    assert [result.chunk_id for result in ranked] == [
        "duplicate-a",
        "evidence-b",
        "evidence-c",
    ]
    assert [result.rank for result in ranked] == [1, 2, 3]


def test_rerank_keeps_same_refs_from_different_videos():
    reranker = CrossEncoderReranker.__new__(CrossEncoderReranker)
    reranker.model = FakeCrossEncoder([0.9, 0.8])
    results = [
        _result("video-a", ["S0001"], video_id="BV1"),
        _result("video-b", ["S0001"], video_id="BV2"),
    ]

    ranked = reranker.rerank("query", results, top_k=2)

    assert [result.chunk_id for result in ranked] == ["video-a", "video-b"]
