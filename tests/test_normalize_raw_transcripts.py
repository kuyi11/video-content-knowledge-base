from tools.normalize_raw_transcripts import normalize_file


def test_normalize_raw_transcript_adds_metadata_once(tmp_path):
    note = tmp_path / "示例标题__medical__unprocessed.md"
    note.write_text(
        '<iframe src="https://player.bilibili.com/player.html?bvid=BVtest123&amp;page=1"></iframe>\n\n'
        "## Transcript\n\n**0:00** · 原始字幕\n",
        encoding="utf-8",
    )

    assert normalize_file(note) is True
    normalized = note.read_text(encoding="utf-8")
    assert normalized.startswith("---\n")
    assert "title: 示例标题\n" in normalized
    assert "video_id: BVtest123\n" in normalized
    assert "transcript_source: platform_caption\n" in normalized
    assert "chunk_type: raw_transcript\n" in normalized
    assert "domain: medical\n" in normalized
    assert "quality: raw_unverified\n" in normalized
    assert "review_status: pending\n" in normalized
    assert "source_of_truth: true\n" in normalized

    assert normalize_file(note) is False
    assert normalized == note.read_text(encoding="utf-8")
