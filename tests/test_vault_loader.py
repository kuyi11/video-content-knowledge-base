from modules.vault_loader import VaultLoader


class FakeModel:
    def encode(self, texts, normalize_embeddings=True):
        raise AssertionError("No transcript embeddings expected in this test")


class TranscriptModel:
    def encode(self, texts, normalize_embeddings=True):
        return [[1.0] for _ in texts]


def _note(video_id: str, profile: str, body: str) -> str:
    return (
        "---\n"
        f"source: https://www.bilibili.com/video/{video_id}/\n"
        f"profile: {profile}\n"
        "---\n"
        f"## Summary\n{body}\n"
    )


def test_loader_prefers_canonical_profile_note_and_deduplicates_legacy_file(tmp_path):
    video_id = "BVloaderunique"
    legacy = tmp_path / "old-url-hash.md"
    canonical = tmp_path / f"{video_id}__detailed.md"
    default = tmp_path / f"{video_id}__default.md"

    legacy.write_text(_note(video_id, "detailed", "old detailed"), encoding="utf-8")
    canonical.write_text(_note(video_id, "detailed", "new detailed"), encoding="utf-8")
    default.write_text(_note(video_id, "default", "default body"), encoding="utf-8")

    loader = VaultLoader(tmp_path, TranscriptModel())
    detailed = loader.load_one(video_id, "detailed")
    documents = loader.load_all()

    assert detailed is not None
    assert detailed.document_id == f"{video_id}__detailed"
    assert "new detailed" in detailed.content
    assert {(doc.video_id, doc.profile) for doc in documents} == {
        (video_id, "default"),
        (video_id, "detailed"),
    }


def test_loader_recurses_and_supports_external_obsidian_frontmatter_and_transcript(tmp_path):
    note_dir = tmp_path / "Knowledge Collection" / "Medical" / "unprocessed"
    note_dir.mkdir(parents=True)
    video_id = "BVexternal123"
    note = note_dir / "medical-note.md"
    note.write_text(
        "---\n"
        f"source_url: https://www.bilibili.com/video/{video_id}/\n"
        "profile: medical\n"
        "---\n"
        "# 医疗笔记\n\n"
        "## Transcript\n\n"
        "**0:00** · 第一段内容\n\n"
        "**0:12** · 第二段内容\n",
        encoding="utf-8",
    )

    loader = VaultLoader(tmp_path, TranscriptModel())
    doc = loader.load_one(video_id, "medical")

    assert doc is not None
    assert doc.source_url.endswith(f"/{video_id}/")
    assert doc.domain == "medical"
    assert [segment.start for segment in doc.segments] == [0.0, 12.0]
    assert doc.segments[0].end == 12.0
    assert doc.segments[1].text == "第二段内容"


def test_loader_parses_iframe_bvid_and_embedded_transcript_without_source_url(tmp_path):
    note_dir = tmp_path / "Knowledge Collection" / "Medical" / "unprocessed"
    note_dir.mkdir(parents=True)
    video_id = "BViframe123"
    note = note_dir / "raw-note.md"
    note.write_text(
        "---\n"
        "domain: medical\n"
        "review_status: pending\n"
        "---\n"
        f'<iframe src="https://player.bilibili.com/player.html?bvid={video_id}"></iframe>\n\n'
        "## Transcript\n\n"
        "**0:03** · 第一段原始转录\n\n"
        "**0:18** · 第二段原始转录\n",
        encoding="utf-8",
    )

    doc = VaultLoader(tmp_path, TranscriptModel()).load_one(video_id)

    assert doc is not None
    assert doc.video_id == video_id
    assert doc.chunk_type == "raw_transcript"
    assert doc.quality == "raw_unverified"
    assert doc.source_of_truth is True
    assert [segment.start for segment in doc.segments] == [3.0, 18.0]
    assert doc.segments[0].end == 18.0


def test_loader_excludes_vault_operations_documents(tmp_path):
    (tmp_path / "仓库首页.md").write_text("# 首页", encoding="utf-8")
    (tmp_path / "语言与术语核查清单.md").write_text("# 核查", encoding="utf-8")
    notes = tmp_path / "笔记"
    notes.mkdir()
    (notes / "项目记录.md").write_text("# 记录", encoding="utf-8")
    included = tmp_path / "BVincluded.md"
    included.write_text(_note("BVincluded", "default", "知识内容"), encoding="utf-8")

    documents = VaultLoader(tmp_path, TranscriptModel()).load_all()

    assert [doc.video_id for doc in documents] == ["BVincluded"]


def test_external_vault_does_not_read_global_temp_transcript(tmp_path, monkeypatch):
    import modules.vault_loader as vault_loader

    video_id = "BVexternaltemp"
    note_dir = tmp_path / "external" / "unprocessed"
    note_dir.mkdir(parents=True)
    note = note_dir / "note.md"
    note.write_text(
        _note(video_id, "default", "## Transcript\n\n无时间轴正文"),
        encoding="utf-8",
    )
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    (temp_dir / f"{video_id}_norm.txt").write_text(
        "[0s -> 2s] 不应读取", encoding="utf-8"
    )
    monkeypatch.setattr(vault_loader, "TEMP_DIR", temp_dir)

    doc = VaultLoader(note_dir.parent, TranscriptModel()).load_one(video_id)
    assert doc is not None
    assert doc.segments == []


def test_transcript_changes_are_reflected_in_content_hash(tmp_path):
    video_id = "BVhashtranscript"
    note = tmp_path / f"{video_id}.md"
    note.write_text(_note(video_id, "default", "summary"), encoding="utf-8")
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    transcript = temp_dir / f"{video_id}_norm.txt"
    transcript.write_text("[0s -> 2s] first", encoding="utf-8")

    loader = VaultLoader(tmp_path, TranscriptModel(), temp_dir=temp_dir)
    first = loader.load_one(video_id)
    transcript.write_text("[0s -> 2s] second", encoding="utf-8")
    second = loader.load_one(video_id)

    assert first is not None and second is not None
    assert first.content_hash != second.content_hash


def test_structured_summary_defaults_to_raw_evidence_policy(tmp_path):
    video_id = "BVsummarypolicy"
    note = tmp_path / "videos" / f"{video_id}__default.md"
    note.parent.mkdir()
    note.write_text(_note(video_id, "default", "summary"), encoding="utf-8")

    doc = VaultLoader(tmp_path, TranscriptModel()).load_one(video_id)

    assert doc is not None
    assert doc.chunk_type == "structured_summary"
    assert doc.answer_policy == "summary_requires_raw_evidence"
