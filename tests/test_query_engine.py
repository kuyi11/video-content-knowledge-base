from modules.indexer import SearchResult
from modules.query_engine import QueryEngine
from modules.query_rewriter import QueryRewriter


class FakeIndex:
    chunks = {}

    def __init__(self):
        self.calls = []

    def query(self, text, top_k=5, filters=None):
        self.calls.append({"text": text, "top_k": top_k, "filters": filters})
        return [
            SearchResult(
                rank=1,
                chunk_id="chunk-1",
                video_id="BV001",
                profile="raw",
                source_url="",
                content="raw evidence",
                section="Transcript",
                start=1.0,
                end=2.0,
                score=1.0,
                chunk_type="raw_transcript",
                domain="medical",
                quality="raw_unverified",
                source_refs=["S0001"],
            )
        ]


def test_query_engine_records_rewrite_and_forwards_metadata_filters():
    index = FakeIndex()
    rewriter = QueryRewriter(
        [{"id": "brain", "aliases": ["脑梗"], "expansions": ["脑卒中"]}],
        version="test-v1",
    )
    engine = QueryEngine(index, rewriter=rewriter)

    response = engine.query(
        "脑梗怎么办",
        filters={"chunk_type": "raw_transcript"},
        rewrite=True,
    )

    assert index.calls == [
        {
            "text": "脑梗怎么办 脑卒中",
            "top_k": 5,
            "filters": {"chunk_type": "raw_transcript"},
        }
    ]
    assert response["meta"]["query_version"] == "test-v1"
    assert response["meta"]["applied_rewrite_rules"] == ["brain"]
    assert response["meta"]["filters"] == {"chunk_type": "raw_transcript"}


def test_query_engine_can_disable_rewrite():
    index = FakeIndex()
    engine = QueryEngine(index, rewriter=QueryRewriter([], version="test-v1"))

    response = engine.query("  raw query  ", rewrite=False)

    assert index.calls[0]["text"] == "raw query"
    assert response["meta"]["query_version"] == "raw-v1"
