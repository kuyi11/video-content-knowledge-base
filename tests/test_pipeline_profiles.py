from pathlib import Path

import pipeline as pipeline_module
from pipeline import Pipeline


def _structured(profile_name):
    base = {
        "title": f"{profile_name} title",
        "summary": "summary",
        "topics": ["topic"],
        "key_points": ["point"],
        "insights": [],
        "actions": [],
        "tags": ["tag1", "tag2", "tag3"],
    }
    if profile_name != "default":
        base.update(
            {
                "knowledge_items": [],
                "questions": [],
                "pros_cons": [],
                "evidence": [],
                "risks": [],
            }
        )
    return base


def _configure_pipeline_run(monkeypatch, tmp_path, synthesis):
    audio_path = tmp_path / "video.wav"
    transcript_path = tmp_path / "video.txt"
    audio_path.write_bytes(b"audio")
    transcript_path.write_text("[0.0s -> 2.0s] content", encoding="utf-8")
    content_map = {"version": "content-map-v1", "units": [], "sources": {}, "windows": []}
    extract_calls = []

    monkeypatch.setattr(pipeline_module, "get_platform_transcript", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline_module, "get_embedded_transcript", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline_module, "get_cached_subtitle_transcript", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline_module, "get_cached_asr_transcript", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline_module, "get_audio", lambda url: audio_path)
    monkeypatch.setattr(
        pipeline_module,
        "transcribe",
        lambda audio, **kwargs: str(transcript_path),
    )

    def fake_extract(text, **kwargs):
        extract_calls.append((text, kwargs))
        return content_map

    monkeypatch.setattr(pipeline_module, "extract_content_map", fake_extract)
    monkeypatch.setattr(pipeline_module, "structure_content_map", synthesis)
    monkeypatch.setattr(pipeline_module, "render_markdown", lambda data, url, **kwargs: "# note")

    def fake_write(markdown, url, output_dir, profile, **kwargs):
        path = Path(output_dir) / f"BVmulti__{profile}.md"
        path.write_text(markdown, encoding="utf-8")
        return str(path)

    monkeypatch.setattr(pipeline_module, "write_to_obsidian", fake_write)
    return extract_calls


def test_profile_selection_accepts_comma_list_and_deduplicates(tmp_path):
    pipeline = Pipeline(vault_path=tmp_path / "vault", index_dir=tmp_path / "index")

    profiles = pipeline._get_profiles(profiles="default,detailed,default")

    assert [profile.name for profile in profiles] == ["default", "detailed"]

def test_asr_state_fingerprint_requires_matching_backend_domains_and_hotwords(tmp_path):
    pipeline = Pipeline(vault_path=tmp_path / "vault", index_dir=tmp_path / "index")
    requested = pipeline._asr_state_config("paraformer", ["medical"], "肌糖原 20")
    state = {"transcript": {"text_source": "asr", **requested}}

    assert pipeline._state_matches_transcript(state, requested)
    assert not pipeline._state_matches_transcript(
        state,
        pipeline._asr_state_config("faster_whisper", ["medical"], "肌糖原"),
    )
    assert not pipeline._state_matches_transcript(
        state,
        pipeline._asr_state_config("paraformer", ["fitness"], "肌糖原 20"),
    )


def test_subtitle_state_is_independent_of_asr_selection(tmp_path):
    pipeline = Pipeline(vault_path=tmp_path / "vault", index_dir=tmp_path / "index")
    state = {"transcript": {"text_source": "subtitle_manual"}}
    requested = pipeline._asr_state_config("paraformer", ["medical"], "心包积液 20")

    assert pipeline._state_matches_transcript(state, requested)


def test_legacy_state_without_transcript_fingerprint_requires_refresh(tmp_path):
    pipeline = Pipeline(vault_path=tmp_path / "vault", index_dir=tmp_path / "index")
    requested = pipeline._asr_state_config("paraformer", ["medical"], "心包积液 20")

    assert not pipeline._state_matches_transcript({}, requested)


def test_multi_profile_run_extracts_once_and_synthesizes_selected_profiles(monkeypatch, tmp_path):
    synthesis_calls = []

    def synthesize(content_map, profile, **kwargs):
        synthesis_calls.append(profile.name)
        return _structured(profile.name)

    extract_calls = _configure_pipeline_run(monkeypatch, tmp_path, synthesize)
    pipeline = Pipeline(vault_path=tmp_path / "vault", index_dir=tmp_path / "index")
    monkeypatch.setattr(pipeline, "_already_processed", lambda *args, **kwargs: False)
    monkeypatch.setattr(pipeline, "_update_index", lambda *args, **kwargs: 9)
    completed = []
    monkeypatch.setattr(
        pipeline,
        "_mark_completed",
        lambda video_id, md_path, profile, **kwargs: completed.append(profile.name),
    )

    result = pipeline.run(
        "https://www.bilibili.com/video/BVmulti/",
        profiles=["default", "detailed"],
    )

    assert result["status"] == "ok"
    assert len(extract_calls) == 1
    assert synthesis_calls == ["default", "detailed"]
    assert completed == ["default", "detailed"]
    assert set(result["profiles"]) == {"default", "detailed"}


def test_multi_profile_run_preserves_success_when_another_profile_fails(monkeypatch, tmp_path):
    def synthesize(content_map, profile, **kwargs):
        if profile.name == "detailed":
            raise RuntimeError("detailed failed")
        return _structured(profile.name)

    extract_calls = _configure_pipeline_run(monkeypatch, tmp_path, synthesize)
    pipeline = Pipeline(vault_path=tmp_path / "vault", index_dir=tmp_path / "index")
    monkeypatch.setattr(pipeline, "_already_processed", lambda *args, **kwargs: False)
    monkeypatch.setattr(pipeline, "_update_index", lambda *args, **kwargs: 5)
    completed = []
    monkeypatch.setattr(
        pipeline,
        "_mark_completed",
        lambda video_id, md_path, profile, **kwargs: completed.append(profile.name),
    )

    result = pipeline.run(
        "https://www.bilibili.com/video/BVmulti/",
        profiles="default,detailed",
    )

    assert len(extract_calls) == 1
    assert result["status"] == "partial"
    assert result["profiles"]["default"]["status"] == "ok"
    assert result["profiles"]["detailed"]["status"] == "error"
    assert completed == ["default"]


def test_pipeline_uses_platform_subtitle_without_downloading_audio(monkeypatch, tmp_path):
    transcript_path = tmp_path / "BVsubtitle_norm.txt"
    transcript_path.write_text("[0.0s -> 2.0s] platform caption", encoding="utf-8")
    artifact = pipeline_module.TranscriptArtifact(
        path=transcript_path,
        text_source="subtitle_manual",
        language="zh-Hans",
        provider="platform",
        track_id="manual:zh-Hans",
        fallback_chain=("platform:subtitle_manual",),
    )
    monkeypatch.setattr(pipeline_module, "get_cached_subtitle_transcript", lambda *args: None)
    monkeypatch.setattr(pipeline_module, "get_cached_asr_transcript", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline_module, "get_platform_transcript", lambda *args, **kwargs: artifact)
    monkeypatch.setattr(
        pipeline_module,
        "get_audio",
        lambda url: (_ for _ in ()).throw(AssertionError("audio should not be downloaded")),
    )
    monkeypatch.setattr(
        pipeline_module,
        "extract_content_map",
        lambda text, **kwargs: {"version": "content-map-v1", "units": [], "sources": {}, "windows": []},
    )
    monkeypatch.setattr(
        pipeline_module,
        "structure_content_map",
        lambda content_map, profile, **kwargs: _structured(profile.name),
    )
    rendered_metadata = []

    def fake_render(data, url, **kwargs):
        rendered_metadata.append(kwargs["transcript_metadata"])
        return "# note"

    monkeypatch.setattr(pipeline_module, "render_markdown", fake_render)
    monkeypatch.setattr(
        pipeline_module,
        "write_to_obsidian",
        lambda markdown, url, output_dir, profile, **kwargs: str(tmp_path / f"{profile}.md"),
    )
    pipeline = Pipeline(vault_path=tmp_path / "vault", index_dir=tmp_path / "index")
    monkeypatch.setattr(pipeline, "_already_processed", lambda *args, **kwargs: False)
    monkeypatch.setattr(pipeline, "_update_index", lambda *args, **kwargs: 1)
    monkeypatch.setattr(pipeline, "_mark_completed", lambda *args, **kwargs: None)

    result = pipeline.run("https://www.bilibili.com/video/BVsubtitle/")

    assert result["status"] == "ok"
    assert result["audio_path"] is None
    assert result["text_source"] == "subtitle_manual"
    assert rendered_metadata[0]["subtitle_language"] == "zh-Hans"

def test_pipeline_does_not_apply_whisper_hotwords_by_default(monkeypatch, tmp_path):
    extract_calls = _configure_pipeline_run(
        monkeypatch,
        tmp_path,
        lambda content_map, profile, **kwargs: _structured(profile.name),
    )
    transcript_path = tmp_path / "video.txt"
    transcribe_calls = []

    def fake_transcribe(audio, **kwargs):
        transcribe_calls.append(kwargs)
        return str(transcript_path)

    monkeypatch.setattr(pipeline_module, "WHISPER_HOTWORDS_ENABLED", False)
    monkeypatch.setattr(pipeline_module, "transcribe", fake_transcribe)
    pipeline = Pipeline(vault_path=tmp_path / "vault", index_dir=tmp_path / "index")
    monkeypatch.setattr(pipeline, "_already_processed", lambda *args, **kwargs: False)
    monkeypatch.setattr(pipeline, "_update_index", lambda *args, **kwargs: 1)
    monkeypatch.setattr(pipeline, "_mark_completed", lambda *args, **kwargs: None)

    result = pipeline.run(
        "https://www.bilibili.com/video/BVwhisperguard/",
        domains=["fitness"],
    )

    assert result["status"] == "ok"
    assert extract_calls
    assert transcribe_calls[0]["asr_backend"] == "faster_whisper"
    assert transcribe_calls[0]["hotwords"] == ""
    assert tuple(transcribe_calls[0]["domains"]) == ("fitness",)
