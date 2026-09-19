import json
from pathlib import Path

import modules.subtitle as subtitle_module
from modules.subtitle import SubtitleTrack


def test_select_subtitle_track_prefers_language_then_manual():
    tracks = [
        SubtitleTrack(language="en", is_auto=False, extension="vtt", url="en"),
        SubtitleTrack(language="zh-Hans", is_auto=True, extension="vtt", url="zh-auto"),
        SubtitleTrack(language="zh-Hans", is_auto=False, extension="srt", url="zh-manual"),
    ]

    selected = subtitle_module.select_subtitle_track(
        tracks,
        ("zh-CN", "zh-Hans", "en"),
    )

    assert selected is not None
    assert selected.url == "zh-manual"
    assert selected.text_source == "subtitle_manual"


def test_select_subtitle_track_can_reject_unlisted_languages():
    tracks = [SubtitleTrack(language="fr", is_auto=False, extension="vtt", url="fr")]

    assert subtitle_module.select_subtitle_track(
        tracks,
        ("zh", "en"),
        allow_any_language=False,
    ) is None


def test_language_ranking_prefers_exact_variant_over_earlier_family_match():
    tracks = [
        SubtitleTrack(language="zh-CN", is_auto=False, extension="srt", url="cn"),
        SubtitleTrack(language="zh-Hans", is_auto=False, extension="srt", url="hans"),
    ]

    selected = subtitle_module.select_subtitle_track(
        tracks,
        ("zh-Hans", "zh-CN"),
    )

    assert selected is not None
    assert selected.url == "hans"


def test_parse_srt_and_webvtt_multiline_cues():
    text = """WEBVTT

00:00:01.000 --> 00:00:03.500 align:start
<c.yellow>第一行</c>
第二行

2
00:03:04,250 --> 00:03:06,000
肌糖原
"""

    segments = subtitle_module.parse_subtitle_text(text)

    assert segments == [
        {"start": 1.0, "end": 3.5, "text": "第一行 第二行"},
        {"start": 184.25, "end": 186.0, "text": "肌糖原"},
    ]


def test_platform_transcript_supports_inline_subtitle_data(tmp_path, monkeypatch):
    monkeypatch.setattr(subtitle_module, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(
        subtitle_module,
        "_platform_tracks",
        lambda url: [
            SubtitleTrack(
                language="zh-Hans",
                is_auto=False,
                extension="srt",
                data="1\n00:00:00,000 --> 00:00:02,000\n字幕正文\n",
            )
        ],
    )

    artifact = subtitle_module.get_platform_transcript(
        "https://www.bilibili.com/video/BVinline/",
        "BVinline",
        preferred_languages=("zh-Hans", "en"),
    )

    assert artifact is not None
    assert artifact.text_source == "subtitle_manual"
    assert artifact.path.name == "BVinline_norm.txt"
    assert "[0.0s -> 2.0s] 字幕正文" in artifact.path.read_text(encoding="utf-8")
    metadata = json.loads(artifact.path.with_suffix(".meta.json").read_text(encoding="utf-8"))
    assert metadata["text_source"] == "subtitle_manual"
    assert metadata["raw_subtitle_path"].endswith(".srt")


def test_cached_platform_transcript_validates_digest(tmp_path, monkeypatch):
    monkeypatch.setattr(subtitle_module, "TEMP_DIR", tmp_path)
    track_calls = []
    track = SubtitleTrack(
        language="zh-Hans",
        is_auto=False,
        extension="srt",
        data="1\n00:00:00,000 --> 00:00:01,000\n第一次\n",
    )

    def fake_tracks(url):
        track_calls.append(url)
        return [track]

    monkeypatch.setattr(subtitle_module, "_platform_tracks", fake_tracks)
    first = subtitle_module.get_platform_transcript(
        "https://example.com/BVcache",
        "BVcache",
        preferred_languages=("zh-Hans",),
    )
    second = subtitle_module.get_platform_transcript(
        "https://example.com/BVcache",
        "BVcache",
        preferred_languages=("zh-Hans",),
    )

    assert first is not None and second is not None
    assert len(track_calls) == 1

    first.path.write_text("tampered", encoding="utf-8")
    third = subtitle_module.get_platform_transcript(
        "https://example.com/BVcache",
        "BVcache",
        preferred_languages=("zh-Hans",),
    )
    assert third is not None
    assert len(track_calls) == 2


def test_platform_transcript_tries_next_ranked_track(tmp_path, monkeypatch):
    monkeypatch.setattr(subtitle_module, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(
        subtitle_module,
        "_platform_tracks",
        lambda url: [
            SubtitleTrack(
                language="zh-Hans",
                is_auto=False,
                extension="srt",
                data="invalid subtitle",
            ),
            SubtitleTrack(
                language="en",
                is_auto=False,
                extension="srt",
                data="1\n00:00:00,000 --> 00:00:02,000\nfallback caption\n",
            ),
        ],
    )

    artifact = subtitle_module.get_platform_transcript(
        "https://example.com/BVfallback",
        "BVfallback",
        preferred_languages=("zh-Hans", "en"),
    )

    assert artifact is not None
    assert artifact.language == "en"
    assert "fallback caption" in artifact.path.read_text(encoding="utf-8")


def test_embedded_track_ignores_bitmap_subtitle_streams(monkeypatch):
    payload = {
        "streams": [
            {"index": 2, "codec_name": "hdmv_pgs_subtitle", "tags": {"language": "zh"}},
            {"index": 3, "codec_name": "subrip", "tags": {"language": "en"}},
        ]
    }
    monkeypatch.setattr(
        subtitle_module.subprocess,
        "run",
        lambda *args, **kwargs: type(
            "Result", (), {"returncode": 0, "stdout": json.dumps(payload)}
        )(),
    )

    selected = subtitle_module._embedded_track(Path("video.mkv"), ("zh", "en"))

    assert selected is not None
    assert selected["index"] == 3


def test_embedded_track_respects_allow_any_language(monkeypatch):
    payload = {
        "streams": [
            {"index": 1, "codec_name": "subrip", "tags": {"language": "fr"}},
        ]
    }
    monkeypatch.setattr(subtitle_module, "SUBTITLE_ALLOW_ANY_LANGUAGE", False)
    monkeypatch.setattr(
        subtitle_module.subprocess,
        "run",
        lambda *args, **kwargs: type(
            "Result", (), {"returncode": 0, "stdout": json.dumps(payload)}
        )(),
    )

    assert subtitle_module._embedded_track(Path("video.mkv"), ("zh", "en")) is None
