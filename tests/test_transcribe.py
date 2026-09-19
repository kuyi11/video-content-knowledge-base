from pathlib import Path
from types import SimpleNamespace

import modules.transcribe as transcribe_module


def test_build_audio_windows_applies_overlap(tmp_path, monkeypatch):
    calls = []

    def fake_slice(audio, output_path, start, duration):
        calls.append((start, duration, output_path))

    monkeypatch.setattr(transcribe_module, "_slice_audio", fake_slice)

    windows = list(
        transcribe_module._build_audio_windows(
            Path("input.wav"),
            duration=1000,
            work_dir=tmp_path,
        )
    )

    assert windows[0][0:2] == (0.0, 480.0)
    assert windows[1][0] == 470.0
    assert windows[-1][1] == 1000.0
    assert len(calls) == len(windows)


def test_transcribe_long_audio_offsets_and_deduplicates(tmp_path, monkeypatch):
    audio_path = tmp_path / "BV123_norm.wav"
    audio_path.write_bytes(b"placeholder")

    info = SimpleNamespace(language="zh", language_probability=0.99)
    windows = [
        (0.0, 600.0, tmp_path / "part0.wav"),
        (590.0, 1200.0, tmp_path / "part1.wav"),
    ]

    class FakeModel:
        pass

    def fake_window(model, chunk_path, language, hotwords=None):
        if chunk_path.name == "part0.wav":
            return (
                [
                    {"start": 585.0, "end": 595.0, "text": "重叠内容"},
                    {"start": 595.0, "end": 600.0, "text": "第一段"},
                ],
                info,
            )
        return (
            [
                {"start": 0.0, "end": 5.0, "text": "重叠内容"},
                {"start": 5.0, "end": 15.0, "text": "第二段"},
            ],
            info,
        )

    monkeypatch.setattr(transcribe_module, "WhisperModel", lambda *args, **kwargs: FakeModel())
    monkeypatch.setattr(transcribe_module, "_probe_duration", lambda path: 1200.0)
    monkeypatch.setattr(
        transcribe_module,
        "_build_audio_windows",
        lambda audio, duration, work_dir: windows,
    )
    monkeypatch.setattr(transcribe_module, "_transcribe_window", fake_window)

    result_path = transcribe_module.transcribe(str(audio_path))
    output = Path(result_path).read_text(encoding="utf-8")

    assert output.count("重叠内容") == 1
    assert "[585.0s -> 595.0s] 重叠内容" in output
    assert "[595.0s -> 600.0s] 第一段" in output
    assert "[595.0s -> 605.0s] 第二段" in output


def test_whisper_window_passes_hotwords_to_model():
    calls = []

    class FakeModel:
        def transcribe(self, audio, **kwargs):
            calls.append((audio, kwargs))
            return (
                [SimpleNamespace(start=0.0, end=1.0, text="肌糖原")],
                SimpleNamespace(language="zh", language_probability=1.0),
            )

    segments, _ = transcribe_module._transcribe_window(
        FakeModel(),
        Path("sample.wav"),
        "zh",
        hotwords="肌糖原，肝糖原",
    )

    assert segments[0]["text"] == "肌糖原"
    assert calls[0][1]["hotwords"] == "肌糖原，肝糖原"

def test_transcribe_cache_tracks_audio_and_force_refresh(tmp_path, monkeypatch):
    audio_path = tmp_path / "BVcache_norm.wav"
    audio_path.write_bytes(b"audio-v1")
    info = SimpleNamespace(language="zh", language_probability=0.99)
    calls = {"models": 0}

    class FakeModel:
        pass

    def make_model(*args, **kwargs):
        calls["models"] += 1
        return FakeModel()

    def fake_window(model, audio, language, hotwords=None):
        return ([{"start": 0.0, "end": 1.0, "text": "缓存测试"}], info)

    monkeypatch.setattr(transcribe_module, "WhisperModel", make_model)
    monkeypatch.setattr(transcribe_module, "_probe_duration", lambda path: 1.0)
    monkeypatch.setattr(transcribe_module, "_transcribe_window", fake_window)

    transcribe_module.transcribe(str(audio_path))
    transcribe_module.transcribe(str(audio_path))
    assert calls["models"] == 1

    transcribe_module.transcribe(str(audio_path), force=True)
    assert calls["models"] == 2

    audio_path.write_bytes(b"audio-v2")
    transcribe_module.transcribe(str(audio_path))
    assert calls["models"] == 3


def test_auto_language_is_detected_once_for_long_audio(tmp_path, monkeypatch):
    audio_path = tmp_path / "BVauto_norm.wav"
    audio_path.write_bytes(b"placeholder")
    windows = [
        (0.0, 500.0, tmp_path / "part0.wav"),
        (490.0, 900.0, tmp_path / "part1.wav"),
    ]
    requested_languages = []

    class FakeModel:
        pass

    def fake_window(model, chunk_path, language, hotwords=None):
        requested_languages.append(language)
        return (
            [{"start": 0.0, "end": 1.0, "text": chunk_path.stem}],
            SimpleNamespace(language="ja", language_probability=0.95),
        )

    monkeypatch.setattr(transcribe_module, "WhisperModel", lambda *args, **kwargs: FakeModel())
    monkeypatch.setattr(transcribe_module, "_probe_duration", lambda path: 900.0)
    monkeypatch.setattr(
        transcribe_module,
        "_build_audio_windows",
        lambda audio, duration, work_dir: windows,
    )
    monkeypatch.setattr(transcribe_module, "_transcribe_window", fake_window)

    transcribe_module.transcribe(str(audio_path), language="auto")

    assert requested_languages == ["auto", "ja"]


def test_cached_asr_transcript_requires_matching_digest(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe_module, "TEMP_DIR", tmp_path)
    transcript = tmp_path / "BVcached_norm.txt"
    transcript.write_text("[0.0s -> 1.0s] cached", encoding="utf-8")
    digest = transcribe_module.hashlib.sha256(transcript.read_bytes()).hexdigest()
    transcript.with_suffix(".meta.json").write_text(
        transcribe_module.json.dumps(
            {
                "version": 4,
                "text_source": "asr",
                "asr_backend": "faster_whisper",
                "transcript_sha256": digest,
                "language_requested": transcribe_module._requested_language(
                    transcribe_module.WHISPER_LANGUAGE
                ),
                "model_path": str(transcribe_module.WHISPER_MODEL_PATH),
                "device": transcribe_module.WHISPER_DEVICE,
                "compute_type": transcribe_module.WHISPER_COMPUTE_TYPE,
                "hotword_sha256": "none",
                "domains": [],
                "glossary_fingerprint": "none",
            }
        ),
        encoding="utf-8",
    )

    assert transcribe_module.get_cached_asr_transcript("BVcached") == str(transcript)

    transcript.write_text("changed", encoding="utf-8")
    assert transcribe_module.get_cached_asr_transcript("BVcached") is None


def test_long_audio_capable_backend_bypasses_hard_windows(monkeypatch):
    info = SimpleNamespace(language="zh", language_probability=1.0)

    class FakeBackend:
        handles_long_audio = True

        def __init__(self):
            self.calls = []

        def transcribe_window(self, audio, context):
            self.calls.append((audio, context))
            return SimpleNamespace(
                segments=[{"start": 0.0, "end": 2.0, "text": "整段 VAD"}],
                info=info,
            )

    backend = FakeBackend()
    monkeypatch.setattr(transcribe_module, "_probe_duration", lambda path: 1200.0)
    monkeypatch.setattr(
        transcribe_module,
        "_transcribe_long_audio_backend",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("hard-window path must not be used")
        ),
    )

    segments, result_info = transcribe_module._transcribe_audio_backend(
        backend,
        Path("long.wav"),
        "zh",
    )

    assert segments[0]["text"] == "整段 VAD"
    assert result_info is info
    assert len(backend.calls) == 1

def test_correction_artifacts_are_auditable_and_reversible(tmp_path):
    raw_path = tmp_path / "BVterms__asr__paraformer.raw.txt"
    text = "[0.0s -> 2.0s] 训练后补充碳水恢复鸡糖原"
    raw_path.write_text(text, encoding="utf-8")

    result = transcribe_module._write_correction_artifacts(
        raw_path,
        text,
        {"asr_backend": "paraformer", "domains": ["fitness"]},
    )

    corrected = Path(result["corrected_asr_path"])
    report_path = Path(result["corrections_path"])
    report = transcribe_module.json.loads(report_path.read_text(encoding="utf-8"))
    assert "鸡糖原〔疑似术语：肌糖原〕" in corrected.read_text(encoding="utf-8")
    assert result["correction_count"] == 1
    assert result["review_needed_count"] == 1
    assert report["corrections"][0]["original"] == "鸡糖原"
    assert report["corrections"][0]["canonical"] == "肌糖原"

def test_domain_cache_requires_correction_artifacts(tmp_path):
    audio = tmp_path / "BVcache_norm.wav"
    audio.write_bytes(b"audio")
    metadata_path = tmp_path / "BVcache_norm.meta.json"
    metadata = transcribe_module._cache_metadata(
        audio,
        "zh",
        "paraformer",
        hotwords="肌糖原 20",
        domains=["fitness"],
    )
    metadata_path.write_text(
        transcribe_module.json.dumps(metadata),
        encoding="utf-8",
    )

    assert not transcribe_module._cache_is_valid(
        audio,
        metadata_path,
        "zh",
        "paraformer",
        hotwords="肌糖原 20",
        domains=["fitness"],
    )

    corrected = tmp_path / "BVcache.corrected.txt"
    corrections = tmp_path / "BVcache.corrections.json"
    corrected.write_text("corrected", encoding="utf-8")
    corrections.write_text("{}", encoding="utf-8")
    metadata["corrected_asr_path"] = str(corrected)
    metadata["corrections_path"] = str(corrections)
    metadata_path.write_text(
        transcribe_module.json.dumps(metadata),
        encoding="utf-8",
    )

    assert transcribe_module._cache_is_valid(
        audio,
        metadata_path,
        "zh",
        "paraformer",
        hotwords="肌糖原 20",
        domains=["fitness"],
    )
