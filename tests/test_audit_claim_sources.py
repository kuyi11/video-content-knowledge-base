from pathlib import Path

from tools.audit_claim_sources import (
    audit_claims,
    parse_segments,
    source_window,
    transcript_path,
)


def test_parse_segments_and_source_window(tmp_path):
    transcript = tmp_path / "BVtest.txt"
    transcript.write_text(
        "[0.0s -> 2.0s] 第一段\n[2.0s -> 4.0s] 第二段\n",
        encoding="utf-8",
    )

    segments = parse_segments(transcript)

    assert segments[1]["text"] == "第二段"
    assert source_window(segments, ["S0002"], "chunk") == [segments[1]]


def test_source_window_prefers_structured_time_ranges():
    segments = [
        {"start": 20.0, "end": 21.0, "text": "错误"},
        {"start": 60.0, "end": 61.0, "text": "正确"},
    ]

    assert source_window(segments, ["S0044"], "chunk", {"S0044": (59.0, 62.0)}) == [segments[1]]


def test_raw_chunk_prefers_obsidian_timestamp_source(tmp_path):
    raw = tmp_path / "raw.md"
    temp = tmp_path / "BVtest_norm.txt"
    raw.write_text("**27:57** · 原始时间轴\n", encoding="utf-8")
    temp.write_text("[1677.0s -> 1678.0s] 归一化时间轴\n", encoding="utf-8")

    selected = transcript_path(
        "BVtest",
        "BVtest__raw_2757",
        [],
        {"BVtest": raw},
        tmp_path,
    )

    assert selected == raw


def test_audit_reports_unresolved_without_source(tmp_path):
    labels = tmp_path / "labels.jsonl"
    labels.write_text(
        '{"id":"x","claim":"结论","evidence":[{"chunk_id":"fake","source_refs":["S0001"],"text":"不存在"}],"label":"supported"}\n',
        encoding="utf-8",
    )
    raw_root = tmp_path / "raw"
    raw_root.mkdir()
    structured = tmp_path / "structured"
    structured.mkdir()

    report = audit_claims(labels, raw_root, tmp_path / "temp", structured)

    assert report["summary"]["case_status_counts"] == {"unresolved": 1}
