import json

from modules.indexer import SearchResult
from modules.evidence_model import build_evidence_units
from modules.query_engine import QueryEngine
from modules.query_rewriter import QueryRewriter
from modules.query_planner import QueryPlanner


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


def test_query_engine_reuses_cached_response_without_requerying_index():
    index = FakeIndex()
    engine = QueryEngine(index, rewriter=QueryRewriter([]))

    first = engine.query("cached query", rewrite=False)
    second = engine.query("cached query", rewrite=False)

    assert len(index.calls) == 1
    assert second["results"] == first["results"]
    assert second["meta"]["cache_hit"] is True


def test_query_engine_exposes_index_generation(tmp_path):
    index = FakeIndex()
    index.vector_dir = tmp_path / "vector"
    index.vector_dir.mkdir()
    (index.vector_dir / "manifest.json").write_text(
        json.dumps({"generation_id": "generation-test"}), encoding="utf-8"
    )

    response = QueryEngine(index, rewriter=QueryRewriter([])).query(
        "raw query", rewrite=False
    )

    assert response["meta"]["index_generation_id"] == "generation-test"


def test_evidence_fingerprint_uses_full_content_before_response_truncation():
    index = FakeIndex()
    long_text = "证据" * 400
    original_query = index.query

    def query(text, top_k=5, filters=None):
        result = original_query(text, top_k=top_k, filters=filters)
        result[0].content = long_text
        return result

    index.query = query
    response = QueryEngine(index, rewriter=QueryRewriter([])).query(
        "raw query", rewrite=False
    )
    item = response["results"][0]

    assert len(item["text"]) < len(long_text)
    assert item["evidence_fingerprint"] == build_evidence_units(
        [{**item, "text": long_text}]
    )["chunk-1"].evidence_fingerprint


class FakeReranker:
    def __init__(self):
        self.calls = []

    def rerank(self, query, results, top_k):
        self.calls.append((query, len(results), top_k))
        return list(reversed(results))[:top_k]


def test_query_engine_uses_optional_reranker_candidate_pool():
    index = FakeIndex()
    reranker = FakeReranker()
    engine = QueryEngine(index, reranker=reranker, reranker_candidate_k=8)

    response = engine.query("raw query", top_k=1, rewrite=False)

    assert index.calls[0]["top_k"] == 8
    assert reranker.calls == [("raw query", 1, 1)]
    assert response["meta"]["reranker"] is True
    assert response["meta"]["candidate_k"] == 8


def test_query_engine_backtraces_raw_chunks_for_derived_summary():
    from modules.chunk_splitter import Chunk

    index = FakeIndex()
    summary = SearchResult(
        rank=1,
        chunk_id="summary-1",
        video_id="BV001",
        profile="default",
        source_url="",
        content="derived answer",
        section="Summary",
        start=0.0,
        end=0.0,
        score=1.0,
        chunk_type="structured_summary",
        answer_policy="summary_requires_raw_evidence",
        source_refs=["S0001"],
    )
    index.query = lambda text, top_k=5, filters=None: [summary]
    index.chunks = {
        "raw-1": Chunk(
            chunk_id="raw-1", video_id="BV001", source_url="", content="raw evidence",
            section="Transcript", start=1.0, end=2.0, chunk_type="raw_transcript",
            source_refs=["S0001"],
        )
    }
    response = QueryEngine(index, rewriter=QueryRewriter([])).query(
        "question", rewrite=False
    )

    assert [item["chunk_id"] for item in response["results"]] == ["summary-1", "raw-1"]
    assert response["results"][1]["retrieval_role"] == "raw_backtrace"
    assert response["meta"]["backtraced_source_count"] == 1


def test_query_engine_splits_parallel_questions_and_reports_coverage():
    index = FakeIndex()
    engine = QueryEngine(index, rewriter=QueryRewriter([]))

    response = engine.query("定义是什么？有哪些风险？", rewrite=False)

    assert [call["text"] for call in index.calls] == ["定义是什么", "有哪些风险"]
    assert response["meta"]["sub_queries"] == ["定义是什么", "有哪些风险"]
    assert all(item["status"] == "covered" for item in response["meta"]["coverage"])


def test_query_planner_rejects_untraceable_llm_output():
    class Client:
        def chat(self, **kwargs):
            return {"message": {"content": '{"sub_queries":["完全无关主题"],"confidence":"high"}'}}

    planner = QueryPlanner(
        QueryRewriter([]), enabled=True, client=Client(), max_subqueries=3
    )
    plan = planner.plan("脑梗有哪些症状？应该如何处理？", rewrite=False)

    assert plan.used_llm is False
    assert plan.sub_queries == ("脑梗有哪些症状", "应该如何处理")
    assert plan.fallback_reason


def test_query_engine_runs_bounded_second_hop_for_missing_evidence():
    class MissingThenFoundIndex(FakeIndex):
        def query(self, text, top_k=5, filters=None):
            self.calls.append({"text": text, "top_k": top_k, "filters": filters})
            if text == "脑梗处理方法":
                return super().query(text, top_k=top_k, filters=filters)
            return []

    class Client:
        def chat(self, **kwargs):
            return {"message": {"content": '{"query":"脑梗处理方法"}'}}

    index = MissingThenFoundIndex()
    planner = QueryPlanner(QueryRewriter([]), enabled=True, client=Client())
    response = QueryEngine(
        index,
        planner=planner,
        multi_hop_enabled=True,
        max_hops=2,
    ).query("脑梗处理", rewrite=False)

    assert response["status"] == "ok"
    assert response["meta"]["coverage"][0]["resolved_at_hop"] == 2
    assert response["meta"]["hop_trace"][-1]["query"] == "脑梗处理方法"
    assert response["meta"]["multi_hop"] == {"enabled": True, "max_hops": 2}


def test_query_planner_rejects_untraceable_next_hop_query():
    class Client:
        def chat(self, **kwargs):
            return {"message": {"content": '{"query":"完全无关主题"}'}}

    planner = QueryPlanner(QueryRewriter([]), enabled=True, client=Client())

    try:
        planner.next_hop_query("脑梗如何处理", "脑梗如何处理", ["脑梗如何处理"])
    except ValueError as exc:
        assert "not traceable" in str(exc)
    else:
        raise AssertionError("untraceable next-hop query was accepted")


def test_semantic_partial_coverage_triggers_second_hop():
    class SemanticIndex(FakeIndex):
        def query(self, text, top_k=5, filters=None):
            self.calls.append({"text": text, "top_k": top_k, "filters": filters})
            chunk_id = "chunk-2" if text == "脑梗处理急救步骤" else "chunk-1"
            return [
                SearchResult(
                    rank=1,
                    chunk_id=chunk_id,
                    video_id="BV001",
                    profile="raw",
                    source_url="",
                    content="补充证据" if chunk_id == "chunk-2" else "部分证据",
                    section="Transcript",
                    start=1.0,
                    end=2.0,
                    score=1.0,
                    chunk_type="raw_transcript",
                    source_refs=["S0002" if chunk_id == "chunk-2" else "S0001"],
                )
            ]

    class Client:
        def __init__(self):
            self.calls = 0

        def chat(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                content = '{"status":"partial","confidence":"high","rationale":"缺少急救步骤"}'
            elif self.calls == 2:
                content = '{"query":"脑梗处理急救步骤"}'
            else:
                content = '{"status":"covered","confidence":"high","rationale":"证据已完整覆盖"}'
            return {"message": {"content": content}}

    client = Client()
    planner = QueryPlanner(QueryRewriter([]), enabled=True, client=client)
    response = QueryEngine(
        SemanticIndex(), planner=planner, multi_hop_enabled=True, max_hops=2
    ).query("脑梗处理", rewrite=False, semantic_grounding=True)

    assert response["meta"]["coverage"][0]["status"] == "covered"
    assert response["meta"]["coverage"][0]["resolved_at_hop"] == 2
    assert response["meta"]["semantic_coverage"]["status"] == "covered"
    assert client.calls == 3
