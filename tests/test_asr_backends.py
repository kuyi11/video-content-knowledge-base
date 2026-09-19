import sys
from types import SimpleNamespace

import pytest

from modules.asr.factory import create_asr_backend
from modules.asr.paraformer_backend import (
    ParaformerBackend,
    _timestamp_seconds,
    _vad_boundaries_from_result,
    build_funasr_hotword,
)
from modules.asr.types import backend_slug, normalize_backend_name


def test_backend_name_normalization():
    assert normalize_backend_name("faster-whisper") == "faster_whisper"
    assert normalize_backend_name("whisper") == "faster_whisper"
    assert normalize_backend_name("ParaFormer") == "paraformer"
    assert backend_slug("faster_whisper") == "faster-whisper"


def test_factory_rejects_unknown_backend():
    with pytest.raises(ValueError):
        create_asr_backend("unknown")


def test_funasr_hotword_builder_uses_unweighted_canonical_terms():
    hotword = build_funasr_hotword(
        [
            {"canonical": "term_alpha", "aliases": ["wrong_alias"], "hotword_weight": 20},
            {"candidate": "term_beta", "hotword_weight": 150},
            {"canonical": "", "hotword_weight": 20},
        ]
    )

    assert hotword == "term_alpha term_beta"
    assert "wrong_alias" not in hotword


def test_paraformer_model_class_detection_distinguishes_contextual_model(tmp_path):
    from modules.asr.paraformer_backend import _model_class_from_config

    ordinary = tmp_path / "ordinary"
    contextual = tmp_path / "contextual"
    ordinary.mkdir()
    contextual.mkdir()
    (ordinary / "config.yaml").write_text("model: Paraformer\n", encoding="utf-8")
    (contextual / "config.yaml").write_text(
        "model: ContextualParaformer\n", encoding="utf-8"
    )

    assert _model_class_from_config(str(ordinary)) == "Paraformer"
    assert _model_class_from_config(str(contextual)) == "ContextualParaformer"

def test_funasr_timestamps_are_milliseconds_even_below_one_second():
    assert _timestamp_seconds(610) == pytest.approx(0.61)


def test_funasr_vad_boundaries_ignore_invalid_ranges():
    result = [
        {
            "value": [
                [610, 5530],
                [9000, 8000],
                ["bad", 12000],
            ]
        }
    ]

    assert _vad_boundaries_from_result(result) == [(0.61, 5.53)]

def test_paraformer_configures_vad_segment_limit(monkeypatch):
    calls = []

    class FakeAutoModel:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setitem(sys.modules, "funasr", SimpleNamespace(AutoModel=FakeAutoModel))

    backend = ParaformerBackend(
        model_path="asr-model",
        vad_path="vad-model",
        punc_path="punc-model",
        device="cuda",
    )

    assert calls[0]["model"] == "asr-model"
    assert "max_single_segment_time" not in calls[0]
    assert calls[1]["model"] == "vad-model"
    assert calls[1]["max_single_segment_time"] == 20_000
    assert backend.metadata()["vad_max_segment_ms"] == 20_000
    assert backend.metadata()["supports_model_hotwords"] is False
    assert backend.metadata()["hotwords_applied"] is False
