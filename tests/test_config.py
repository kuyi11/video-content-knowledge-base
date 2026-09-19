import config


def test_whisper_runtime_auto_selects_cuda(monkeypatch):
    monkeypatch.delenv("WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("WHISPER_COMPUTE_TYPE", raising=False)
    monkeypatch.setattr(config, "_ctranslate2_cuda_available", lambda: True)

    assert config._resolve_whisper_runtime() == ("cuda", "float16")


def test_whisper_runtime_auto_falls_back_to_cpu(monkeypatch):
    monkeypatch.setenv("WHISPER_DEVICE", "auto")
    monkeypatch.setenv("WHISPER_COMPUTE_TYPE", "auto")
    monkeypatch.setattr(config, "_ctranslate2_cuda_available", lambda: False)

    assert config._resolve_whisper_runtime() == ("cpu", "int8")


def test_whisper_runtime_respects_explicit_override(monkeypatch):
    monkeypatch.setenv("WHISPER_DEVICE", "cpu")
    monkeypatch.setenv("WHISPER_COMPUTE_TYPE", "float32")
    monkeypatch.setattr(config, "_ctranslate2_cuda_available", lambda: True)

    assert config._resolve_whisper_runtime() == ("cpu", "float32")


def test_summary_profile_config_supports_multiple_and_deduplicates():
    assert config._parse_summary_profiles("default, detailed,default") == (
        "default",
        "detailed",
    )


def test_summary_profile_config_falls_back_to_default():
    assert config._parse_summary_profiles(" , ") == ("default",)
