from modules.transcript_normalizer import analyze_asr_terms, annotate_asr_terms
from modules.transcript_parser import (
    SourceBlock,
    build_source_blocks,
    build_transcript_windows,
    parse_transcript_lines,
    render_transcript_window,
)


def test_parse_and_merge_timestamped_transcript():
    text = """# Language: zh

[0.0s -> 1.0s] 为什么不能抽血
[1.0s -> 2.0s] 因为你没有病理
[10.0s -> 11.0s] 新的案例
"""
    lines = parse_transcript_lines(text)
    blocks = build_source_blocks(lines, max_chars=100, max_seconds=30, max_gap=5)

    assert len(lines) == 3
    assert len(blocks) == 2
    assert blocks[0].ref_id == "S0001"
    assert "为什么不能抽血\n因为你没有病理" == blocks[0].text
    assert blocks[1].start == 10.0


def test_windows_cover_each_primary_source_exactly_once():
    blocks = [
        SourceBlock(f"S{i:04d}", float((i - 1) * 50), float(i * 50), f"block-{i}")
        for i in range(1, 8)
    ]
    windows = build_transcript_windows(
        blocks,
        target_seconds=120,
        max_chars=1000,
        overlap_seconds=60,
    )

    primary_refs = [ref for window in windows for ref in window.primary_ref_ids]
    assert primary_refs == [block.ref_id for block in blocks]
    assert len(primary_refs) == len(set(primary_refs))
    assert any(len(window.blocks) > len(window.primary_ref_ids) for window in windows[1:])
    assert "CONTEXT_ONLY" in render_transcript_window(windows[1])


def test_plain_text_falls_back_without_fake_timestamps():
    lines = parse_transcript_lines("first line\nsecond line")
    blocks = build_source_blocks(lines, max_chars=100, max_seconds=30, max_gap=5)

    assert len(blocks) == 1
    assert blocks[0].has_timestamp is False


def test_medical_glossary_adds_reversible_hint_only_with_context():
    ungrounded = annotate_asr_terms("只有看到视酸膏，心包基业很多")
    grounded = annotate_asr_terms("血常规提示视酸膏，超声提示心包基业")

    assert "疑似术语" not in ungrounded
    assert "视酸膏〔疑似术语：嗜酸高" in grounded
    assert "心包基业〔疑似术语：心包积液" in grounded


def test_asr_term_analysis_records_retained_and_review_candidates():
    ungrounded_text, ungrounded = analyze_asr_terms("鸡糖原", domains=["fitness"])
    grounded_text, grounded = analyze_asr_terms(
        "训练后补充碳水恢复鸡糖原", domains=["fitness"]
    )

    assert ungrounded_text == "鸡糖原"
    assert ungrounded[0]["action"] == "retained"
    assert ungrounded[0]["confidence"] == "low"
    assert ungrounded[0]["review_needed"] is False
    assert "疑似术语：肌糖原" in grounded_text
    assert grounded[0]["action"] == "annotated"
    assert grounded[0]["confidence"] == "medium"
    assert grounded[0]["review_needed"] is True

def test_domain_glossary_only_applies_to_selected_domain():
    transcript = "训练后补充碳水有助于恢复鸡糖原"

    fitness = annotate_asr_terms(transcript, domains=["fitness"])
    medical = annotate_asr_terms(transcript, domains=["medical"])
    disabled = annotate_asr_terms(transcript, domains=[])

    assert "鸡糖原〔疑似术语：肌糖原〕" in fitness
    assert medical == transcript
    assert disabled == transcript

def test_asr_term_analysis_carries_source_timestamp_for_review():
    _, corrections = analyze_asr_terms(
        "[76.7s -> 79.3s] 训练后腿部肌糖缘被消耗",
        domains=["fitness"],
    )

    assert corrections[0]["segment_start"] == 76.7
    assert corrections[0]["segment_end"] == 79.3
    assert corrections[0]["review_needed"] is True
