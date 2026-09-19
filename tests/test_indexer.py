import json
import tempfile
from pathlib import Path

import pytest
import modules.indexer as indexer_module
from modules.indexer import HybridIndex
from modules.chunk_splitter import CHUNKING_VERSION, Chunk


class FakeModel:
    def encode(self, texts, normalize_embeddings=True, batch_size=32):
        import numpy as np
        return np.random.rand(len(texts), 128)


class CountingModel:
    def __init__(self):
        self.calls = []

    def encode(self, texts, normalize_embeddings=True, batch_size=32):
        import numpy as np

        texts = list(texts)
        self.calls.append(texts)
        embs = np.zeros((len(texts), 128), dtype="float32")
        for i, text in enumerate(texts):
            embs[i, i % 128] = 1.0
            embs[i, 0] = (sum(ord(ch) for ch in text) % 1000) / 1000.0
        return embs


class DimensionModel:
    def __init__(self, dimension):
        self.dimension = dimension

    def get_sentence_embedding_dimension(self):
        return self.dimension

    def encode(self, texts, normalize_embeddings=True, batch_size=32):
        import numpy as np

        texts = list(texts)
        return np.ones((len(texts), self.dimension), dtype="float32")


class TestHybridIndex:
    @pytest.fixture
    def index(self, tmp_path):
        yield HybridIndex(tmp_path / "index", model=FakeModel())

    def test_build_and_query(self, index):
        from modules.vault_loader import Document
        docs = [
            Document(video_id="BV001", source_url="http://example.com/BV001",
                     content="## Topic\n这是第一个视频的内容。它讨论了重要的概念。"),
        ]
        index.build(docs, force=True)
        assert index.faiss_index is not None
        assert len(index.chunks) > 0

    def test_add_documents(self, index):
        from modules.vault_loader import Document
        doc1 = Document(video_id="BV001", source_url="http://example.com/BV001",
                        content="## Topic\n第一个视频内容。")
        index.build([doc1], force=True)
        count_before = len(index.chunks)

        doc2 = Document(video_id="BV002", source_url="http://example.com/BV002",
                        content="## Topic\n第二个视频内容。")
        index.add_documents([doc2])
        assert len(index.chunks) == count_before + 1
        assert index.faiss_index.ntotal == len(index.chunks)

    def test_id_map_updated(self, index):
        from modules.vault_loader import Document
        doc1 = Document(video_id="BV001", source_url="http://example.com/BV001",
                        content="## Topic\n内容。")
        index.build([doc1], force=True)
        doc2 = Document(video_id="BV002", source_url="http://example.com/BV002",
                        content="## Topic\n更多内容。")
        index.add_documents([doc2])
        all_ids = set(index.id_map.values())
        for cid in index.chunks:
            assert cid in all_ids
        assert isinstance(index.faiss_index, indexer_module.faiss.IndexIDMap2)

    def test_rrf_dedup(self, index):
        vec_hits = [("a", 0.9), ("b", 0.8), ("a", 0.7)]
        kw_hits = [("b", 0.8), ("c", 0.7)]
        merged = index._rrf_fusion(vec_hits, kw_hits, k=60)
        seen = set()
        for cid, _ in merged:
            assert cid not in seen
            seen.add(cid)
        assert len(merged) == 3

    def test_result_dedup_collapses_duplicate_evidence_across_profiles(self, index):
        duplicate_a = Chunk(
            chunk_id="a",
            video_id="BV001",
            profile="medical",
            source_url="",
            content="same evidence",
            section="Facts",
            start=0,
            end=1,
            source_refs=["S0001"],
        )
        duplicate_b = Chunk(
            chunk_id="b",
            video_id="BV001",
            profile="detailed",
            source_url="",
            content="same evidence rewritten",
            section="Facts",
            start=0,
            end=1,
            source_refs=["S0001"],
        )
        distinct = Chunk(
            chunk_id="c",
            video_id="BV001",
            profile="medical",
            source_url="",
            content="different evidence",
            section="Facts",
            start=1,
            end=2,
            source_refs=["S0002"],
        )
        index.chunks = {chunk.chunk_id: chunk for chunk in (duplicate_a, duplicate_b, distinct)}

        unique = index._deduplicate([("a", 1.0), ("b", 0.9), ("c", 0.8)], top_k=5)

        assert unique == [("a", 1.0), ("c", 0.8)]

    def test_metadata_filter_matches_scalar_and_multiple_values(self, index):
        chunk = Chunk(
            chunk_id="medical-raw",
            video_id="BV001",
            profile="raw",
            source_url="",
            content="evidence",
            section="Transcript",
            start=0,
            end=1,
            chunk_type="raw_transcript",
            domain="medical",
            quality="raw_unverified",
            source_of_truth=True,
        )

        assert index._matches_filters(chunk, {"domain": "medical", "source_of_truth": True})
        assert index._matches_filters(chunk, {"quality": ["human_reviewed", "raw_unverified"]})
        assert not index._matches_filters(chunk, {"chunk_type": "structured_summary"})
        with pytest.raises(ValueError, match="unsupported metadata filter"):
            index._validate_filters({"unknown": "value"})

    def test_manifest_consistency(self, index):
        from modules.vault_loader import Document
        doc = Document(video_id="BV001", source_url="http://example.com/BV001",
                       content="## T\n视频内容。")
        index.build([doc], force=True)
        vm = json.loads((index.vector_dir / "manifest.json").read_text())
        km = json.loads((index.keyword_dir / "manifest.json").read_text())
        assert vm["chunk_count"] == km["chunk_count"]
        assert vm["chunk_count"] == len(index.chunks)
        assert vm["generation_id"] == km["generation_id"]
        assert vm["version"] == index.INDEX_VERSION
        assert vm["chunking_version"] == CHUNKING_VERSION
        assert km["chunking_version"] == CHUNKING_VERSION
        assert vm["model_id"] == km["model_id"]
        assert vm["embedding_dim"] == index.faiss_index.d
        disk_id_map = json.loads((index.vector_dir / "id_map.json").read_text(encoding="utf-8"))
        assert all(isinstance(k, str) for k in disk_id_map)
        assert all(isinstance(k, int) for k in index.id_map)

        reloaded = HybridIndex(index.index_dir, model=FakeModel())
        assert reloaded.faiss_index is not None
        assert len(reloaded.chunks) == len(index.chunks)
        assert all(isinstance(k, int) for k in reloaded.id_map)

    def test_health_check_reports_changed_missing_and_extra_sources(self, index):
        from modules.vault_loader import Document

        original = Document(
            video_id="BV001", source_url="", content="## T\noriginal",
            source_path="vault/one.md", content_hash="original",
        )
        removed = Document(
            video_id="BV002", source_url="", content="## T\nremoved",
            source_path="vault/two.md", content_hash="removed",
        )
        index.build([original, removed], force=True)
        mounted_path = Document(
            video_id="BV001", source_url="", content="## T\noriginal",
            source_path="/vault/external/one.md", content_hash="original",
        )
        mounted_report = index.health_check([mounted_path])
        assert mounted_report["changed_sources"] == []

        modified = Document(
            video_id="BV001", source_url="", content="## T\nmodified",
            source_path="vault/one.md", content_hash="modified",
        )
        added = Document(
            video_id="BV003", source_url="", content="## T\nadded",
            source_path="vault/three.md", content_hash="added",
        )

        report = index.health_check([modified, added])

        assert report["status"] == "stale"
        assert report["changed_sources"] == ["BV001__default"]
        assert report["missing_from_index"] == ["BV003__default"]
        assert report["extra_in_index"] == ["BV002__default"]

    def test_inventory_health_detects_missing_artifacts_without_loading_index(self, index):
        from modules.vault_loader import Document

        document = Document(
            video_id="BV001", source_url="", content="## T\noriginal",
            source_path="vault/one.md", content_hash="original",
        )
        index.build([document], force=True)
        inventory_index = HybridIndex(index.index_dir, model=FakeModel(), load_index=False)
        assert inventory_index.health_check([document])["status"] == "ok"

        (index.vector_dir / "faiss.index").unlink()
        report = inventory_index.health_check([document])

        assert report["status"] == "stale"
        assert "missing index artifacts" in report["metadata_errors"]

    def test_add_documents_replaces_existing_video(self, index):
        from modules.vault_loader import Document

        original = Document(
            video_id="BV001",
            source_url="http://example.com/BV001",
            content="## Topic\n原始内容。",
        )
        index.build([original], force=True)

        updated = Document(
            video_id="BV001",
            source_url="http://example.com/BV001",
            content="## Topic\n更新后的内容。",
        )
        index.add_documents([updated])

        reloaded = HybridIndex(index.index_dir, model=FakeModel())
        video_chunks = [c for c in reloaded.chunks.values() if c.video_id == "BV001"]
        assert len(video_chunks) == 1
        assert "更新后的内容" in video_chunks[0].content
        assert reloaded.faiss_index.ntotal == len(reloaded.chunks)

    def test_profiles_for_same_video_coexist_and_update_independently(self, index):
        from modules.vault_loader import Document

        default_doc = Document(
            video_id="BV001",
            source_url="http://example.com/BV001",
            content="## Topic\ndefault body",
            profile="default",
        )
        detailed_doc = Document(
            video_id="BV001",
            source_url="http://example.com/BV001?profile=detailed",
            content="## Topic\ndetailed body",
            profile="detailed",
        )

        index.build([default_doc], force=True)
        index.add_documents([detailed_doc])
        assert {c.profile for c in index.chunks.values()} == {"default", "detailed"}

        updated_default = Document(
            video_id="BV001",
            source_url="http://example.com/BV001",
            content="## Topic\nupdated default body",
            profile="default",
        )
        index.add_documents([updated_default])

        by_profile = {c.profile: c for c in index.chunks.values()}
        assert set(by_profile) == {"default", "detailed"}
        assert "updated default body" in by_profile["default"].content
        assert "detailed body" in by_profile["detailed"].content

    def test_add_documents_uses_incremental_path(self, index, monkeypatch):
        from modules.vault_loader import Document

        doc1 = Document(
            video_id="BV001",
            source_url="http://example.com/BV001",
            content="## Topic\nfirst body",
        )
        index.build([doc1], force=True)

        def fail_full_rebuild(_chunks):
            raise AssertionError("add_documents should not call _build_atomic")

        monkeypatch.setattr(index, "_build_atomic", fail_full_rebuild)
        doc2 = Document(
            video_id="BV002",
            source_url="http://example.com/BV002",
            content="## Topic\nsecond body",
        )
        index.add_documents([doc2])

        assert len(index.chunks) == 2
        assert index.faiss_index.ntotal == 2

    def test_incremental_update_does_not_clone_id_map_index(self, index, monkeypatch):
        from modules.vault_loader import Document

        original = Document(
            video_id="BV001",
            source_url="http://example.com/BV001",
            content="## Topic\noriginal body",
        )
        index.build([original], force=True)

        monkeypatch.setattr(
            indexer_module.faiss,
            "clone_index",
            lambda value: (_ for _ in ()).throw(AssertionError("clone_index should not run")),
        )
        index.add_documents(
            [
                Document(
                    video_id="BV002",
                    source_url="http://example.com/BV002",
                    content="## Topic\nsecond body",
                )
            ]
        )

        assert index.faiss_index.ntotal == 2

    def test_incremental_failure_restores_live_faiss(self, index, monkeypatch):
        from modules.vault_loader import Document

        original = Document(
            video_id="BV001",
            source_url="http://example.com/BV001",
            content="## Topic\noriginal body",
        )
        index.build([original], force=True)

        def fail_keyword_update(*args, **kwargs):
            raise OSError("forced keyword staging failure")

        monkeypatch.setattr(index, "_copy_and_update_keyword_store", fail_keyword_update)
        with pytest.raises(RuntimeError, match="Incremental index update failed"):
            index.add_documents(
                [
                    Document(
                        video_id="BV002",
                        source_url="http://example.com/BV002",
                        content="## Topic\nsecond body",
                    )
                ]
            )

        assert index.faiss_index.ntotal == 1
        assert {c.video_id for c in index.chunks.values()} == {"BV001"}

    def test_staged_keyword_update_does_not_mutate_live_index(self, index, monkeypatch):
        from modules.vault_loader import Document

        original = Document(
            video_id="BV001",
            source_url="http://example.com/BV001",
            content="## Topic\noriginal body",
        )
        index.build([original], force=True)

        monkeypatch.setattr(
            index,
            "_validate_staged_index",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("forced validation failure")),
        )
        with pytest.raises(RuntimeError, match="Incremental index update failed"):
            index.add_documents(
                [
                    Document(
                        video_id="BV002",
                        source_url="http://example.com/BV002",
                        content="## Topic\nsecond body",
                    )
                ]
            )

        with index.whoosh_ix.searcher() as searcher:
            assert searcher.doc_count() == 1

    def test_add_documents_only_embeds_replacement_chunks(self, tmp_path):
        from modules.vault_loader import Document

        with tempfile.TemporaryDirectory(dir=tmp_path) as td:
            model = CountingModel()
            index = HybridIndex(Path(td), model=model)
            doc1 = Document(
                video_id="BV001",
                source_url="http://example.com/BV001",
                content="## Topic\nfirst body",
            )
            index.build([doc1], force=True)

            model.calls.clear()
            doc2 = Document(
                video_id="BV002",
                source_url="http://example.com/BV002",
                content="## Topic\nsecond body",
            )
            index.add_documents([doc2])

            encoded_texts = [text for call in model.calls for text in call]
            assert len(encoded_texts) == 1
            assert "second body" in encoded_texts[0]
            assert "first body" not in "\n".join(encoded_texts)

    def test_incompatible_embedding_dimension_requires_rebuild(self, tmp_path):
        from modules.vault_loader import Document

        index_dir = tmp_path / "dimension-index"
        document = Document(
            video_id="BVdimension",
            source_url="http://example.com/BVdimension",
            content="## Topic\ncontent for dimension validation",
        )
        first = HybridIndex(index_dir, model=DimensionModel(4))
        first.build([document], force=True)
        first._close_whoosh()

        reloaded = HybridIndex(index_dir, model=DimensionModel(3))
        assert reloaded._needs_build is True
        assert reloaded.faiss_index is None

    def test_corrupt_persisted_metadata_requires_rebuild(self, tmp_path):
        from modules.vault_loader import Document

        index_dir = tmp_path / "corrupt-index"
        document = Document(
            video_id="BVcorrupt",
            source_url="http://example.com/BVcorrupt",
            content="## Topic\ncontent for corruption validation",
        )
        first = HybridIndex(index_dir, model=FakeModel())
        first.build([document], force=True)
        first._close_whoosh()
        (index_dir / "vector" / "id_map.json").write_text("{broken", encoding="utf-8")

        reloaded = HybridIndex(index_dir, model=FakeModel())
        assert reloaded._needs_build is True
        assert reloaded.faiss_index is None

    def test_atomic_swap_rolls_back_on_keyword_failure(self, index, monkeypatch):
        from modules.vault_loader import Document

        doc = Document(
            video_id="BV001",
            source_url="http://example.com/BV001",
            content="## Topic\noriginal body",
        )
        index.build([doc], force=True)
        original_generation = json.loads((index.vector_dir / "manifest.json").read_text())["generation_id"]
        real_rename = indexer_module.os.rename
        failed = {"value": False}

        def flaky_rename(src, dst):
            if str(src).endswith(".tmp_keyword") and not failed["value"]:
                failed["value"] = True
                raise OSError("forced keyword swap failure")
            return real_rename(src, dst)

        monkeypatch.setattr(indexer_module.os, "rename", flaky_rename)
        replacement = Chunk(
            chunk_id="BV002_chunk",
            video_id="BV002",
            source_url="http://example.com/BV002",
            content="## Topic\nreplacement body",
            section="Topic",
            start=0.0,
            end=1.0,
        )

        with pytest.raises(RuntimeError, match="Atomic build failed"):
            index._build_atomic([replacement])

        reloaded = HybridIndex(index.index_dir, model=FakeModel())
        assert sorted(c.video_id for c in reloaded.chunks.values()) == ["BV001"]
        assert json.loads((reloaded.vector_dir / "manifest.json").read_text())["generation_id"] == original_generation
        assert not (index.index_dir / ".swap_journal.json").exists()
