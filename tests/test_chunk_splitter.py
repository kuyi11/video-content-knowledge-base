import pytest
from modules.chunk_splitter import ChunkSplitter
from modules.vault_loader import Document, Segment


class FakeModel:
    def encode(self, texts, normalize_embeddings=True):
        import numpy as np
        return np.random.rand(len(texts), 128)


class TestChunkSplitter:
    def test_split_by_headings(self):
        body = "这是一段足够长的章节内容，用来避免短章节合并规则影响标题切分测试。" * 3
        content = f"## Section A\n{body}\n## Section B\n{body}\n## Section C\n{body}"
        doc = Document(video_id="BV001", source_url="https://example.com/BV001", content=content)
        splitter = ChunkSplitter(FakeModel())
        chunks = splitter.split([doc])
        assert len(chunks) == 3
        assert "Section A" in chunks[0].section
        assert "Section B" in chunks[1].section

    def test_merge_short_sections(self):
        content = "## Short\nhi\n## Long\n" + "long text. " * 20
        doc = Document(video_id="BV002", source_url="https://example.com/BV002", content=content)
        splitter = ChunkSplitter(FakeModel())
        chunks = splitter.split([doc])
        assert len(chunks) == 2
        assert chunks[0].section == "Short"
        assert chunks[1].section == "Long"

    def test_split_long_section(self):
        long_text = "长句。" * 200
        content = f"## Long Section\n{long_text}"
        doc = Document(video_id="BV003", source_url="https://example.com/BV003", content=content)
        splitter = ChunkSplitter(FakeModel())
        chunks = splitter.split([doc])
        for c in chunks:
            assert len(c.content) <= 600

    def test_split_unpunctuated_long_section(self):
        long_text = "x" * 1300
        content = f"## Long Section\n{long_text}"
        doc = Document(video_id="BV007", source_url="https://example.com/BV007", content=content)
        splitter = ChunkSplitter(FakeModel())
        chunks = splitter.split([doc])

        assert len(chunks) >= 3
        assert all(len(c.content) <= 600 for c in chunks)

    def test_chunk_ids_assigned(self):
        content = "## Section A\ntext\n## Section B\ntext"
        doc = Document(video_id="BV004", source_url="https://example.com/BV004", content=content)
        splitter = ChunkSplitter(FakeModel())
        chunks = splitter.split([doc])
        assert all(c.chunk_id.startswith("BV004_") for c in chunks)
        assert len(set(c.chunk_id for c in chunks)) == len(chunks)

    def test_empty_document(self):
        doc = Document(video_id="BV005", source_url="https://example.com/BV005", content="")
        splitter = ChunkSplitter(FakeModel())
        chunks = splitter.split([doc])
        assert len(chunks) == 0

    def test_has_timestamp_no_segments(self):
        content = "## Section A\ntext"
        doc = Document(
            video_id="BV006", source_url="https://example.com/BV006",
            content=content, segments=[],
        )
        splitter = ChunkSplitter(FakeModel())
        chunks = splitter.split([doc])
        assert len(chunks) > 0
        assert not chunks[0].has_timestamp

    def test_raw_transcript_chunks_preserve_exact_refs_times_and_metadata(self):
        doc = Document(
            video_id="BVraw",
            source_url="",
            content="## Transcript",
            document_id="BVraw__raw",
            segments=[
                Segment(start=3.0, end=18.0, text="第一段原始转录"),
                Segment(start=18.0, end=27.5, text="第二段原始转录"),
            ],
            source_path="F:/obsidian/raw.md",
            chunk_type="raw_transcript",
            domain="medical",
            quality="raw_unverified",
            review_status="pending",
            source_of_truth=True,
            content_hash="abc123",
        )

        chunks = ChunkSplitter(FakeModel()).split([doc])

        assert len(chunks) == 1
        chunk = chunks[0]
        assert chunk.start == 3.0
        assert chunk.end == 27.5
        assert chunk.has_timestamp is True
        assert chunk.source_refs == ["S0001", "S0002"]
        assert "[S0001 3.00s-18.00s]" in chunk.content
        assert chunk.chunk_type == "raw_transcript"
        assert chunk.source_path == "F:/obsidian/raw.md"
        assert chunk.content_hash == "abc123"

    def test_nested_evidence_heading_keeps_claim_with_its_source_refs(self):
        content = """## 内容脉络
### 6. 心梗急救
出现持续胸痛应立即就医，进行心电图检查。
- 来源：S0044 (21:01-21:30)
### 7. 冬季洗澡
冬季洗澡要避免水温过高和时间过长。
- 来源：S0051 (24:26-24:55)
"""
        doc = Document(video_id="BVmedical", source_url="", content=content)

        chunks = ChunkSplitter(FakeModel()).split([doc])

        assert len(chunks) == 2
        assert chunks[0].section == "内容脉络 > 6. 心梗急救"
        assert "心电图检查" in chunks[0].content
        assert chunks[0].source_refs == ["S0044"]
        assert "水温过高" in chunks[1].content
        assert chunks[1].source_refs == ["S0051"]

    def test_long_evidence_subchunk_inherits_section_refs(self):
        content = "## 证据\n" + ("这一句没有编号。" * 100) + "\n来源：S0044"
        doc = Document(video_id="BVlong", source_url="", content=content)

        chunks = ChunkSplitter(FakeModel()).split([doc])

        assert len(chunks) > 1
        assert all(chunk.source_refs == ["S0044"] for chunk in chunks)

    def test_document_refs_do_not_leak_into_unreferenced_sections(self):
        content = "## 无来源章节\n" + ("这里只是背景说明。" * 12)
        doc = Document(
            video_id="BVscope",
            source_url="",
            content=content,
            source_refs=["S0001", "S0099"],
        )

        chunks = ChunkSplitter(FakeModel()).split([doc])

        assert len(chunks) == 1
        assert chunks[0].source_refs == []

    def test_structured_note_with_transcript_emits_separate_raw_evidence(self):
        doc = Document(
            video_id="BVmixed",
            source_url="",
            content="## Summary\n" + ("总结内容。" * 20) + "\n## Transcript\n原始正文",
            document_id="BVmixed__medical",
            profile="medical",
            segments=[Segment(start=1.0, end=4.0, text="原始转录证据")],
            chunk_type="structured_summary",
            domain="medical",
            quality="derived_pending_review",
            answer_policy="summary_requires_raw_evidence",
        )

        chunks = ChunkSplitter(FakeModel()).split([doc])

        summary = [chunk for chunk in chunks if chunk.chunk_type == "structured_summary"]
        raw = [chunk for chunk in chunks if chunk.chunk_type == "raw_transcript"]
        assert len(summary) == 1
        assert "原始正文" not in summary[0].content
        assert len(raw) == 1
        assert raw[0].source_of_truth is True
        assert raw[0].source_refs == ["S0001"]
        assert raw[0].document_id == "BVmixed__medical__raw"
