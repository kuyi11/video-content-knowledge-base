"""Optional FunASR Paraformer backend.

This module intentionally imports funasr lazily so the default Whisper workflow
continues to work when FunASR is not installed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from config import (
    PARAFORMER_DEVICE,
    PARAFORMER_MODEL_PATH,
    PARAFORMER_PUNC_PATH,
    PARAFORMER_VAD_MAX_SEGMENT_MS,
    PARAFORMER_VAD_PATH,
)
from modules.asr.types import ASRContext, BackendResult, TranscriptionInfo


def build_funasr_hotword(entries: list[dict[str, Any]], limit: int = 80) -> str:
    terms: list[str] = []
    for entry in entries[: max(0, limit)]:
        canonical = str(entry.get("canonical") or entry.get("candidate") or "").strip()
        if canonical and canonical not in terms:
            terms.append(canonical)
    return " ".join(terms)


def _model_class_from_config(model_path: str) -> str:
    config_path = Path(model_path) / "config.yaml"
    if not config_path.is_file():
        return "unknown"
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return "unknown"
    return str(config.get("model") or "unknown")

def _timestamp_seconds(value: Any) -> float:
    try:
        return float(value) / 1000.0
    except (TypeError, ValueError):
        return 0.0


def _vad_boundaries_from_result(result: Any) -> list[tuple[float, float]]:
    items = result if isinstance(result, list) else [result]
    boundaries: list[tuple[float, float]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        values = item.get("value")
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, (list, tuple)) or len(value) < 2:
                continue
            try:
                start = float(value[0]) / 1000.0
                end = float(value[1]) / 1000.0
            except (TypeError, ValueError):
                continue
            if end > start >= 0:
                boundaries.append((round(start, 3), round(end, 3)))
    return boundaries


def _text_from_result(result: Any) -> str:
    items = result if isinstance(result, list) else [result]
    texts: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if text:
            texts.append(text)
    return " ".join(texts)


class ParaformerBackend:
    name = "paraformer"
    handles_long_audio = True

    def __init__(
        self,
        model_path: str = PARAFORMER_MODEL_PATH,
        vad_path: str = PARAFORMER_VAD_PATH,
        punc_path: str = PARAFORMER_PUNC_PATH,
        device: str = PARAFORMER_DEVICE,
    ):
        try:
            from funasr import AutoModel
        except ImportError as exc:
            raise RuntimeError(
                "FunASR is not installed. Install the optional funasr dependency in an isolated "
                "project environment before using ASR_BACKEND=paraformer."
            ) from exc

        self.model_path = str(model_path)
        self.vad_path = str(vad_path)
        self.punc_path = str(punc_path)
        self.device = device
        self.model_class = _model_class_from_config(self.model_path)
        self.supports_model_hotwords = self.model_class in {
            "ContextualParaformer",
            "SeacoParaformer",
        }
        self.hotwords_requested = False
        self.hotwords_applied = False
        self._asr_model = AutoModel(
            model=self.model_path,
            punc_model=self.punc_path,
            device=self.device,
            disable_update=True,
        )
        self._vad_model = AutoModel(
            model=self.vad_path,
            device=self.device,
            disable_update=True,
            max_single_segment_time=PARAFORMER_VAD_MAX_SEGMENT_MS,
        )

    def metadata(self) -> dict:
        return {
            "asr_backend": self.name,
            "model_path": self.model_path,
            "vad_model_path": self.vad_path,
            "punc_model_path": self.punc_path,
            "device": self.device,
            "timestamp_source": "fsmn-vad",
            "vad_max_segment_ms": PARAFORMER_VAD_MAX_SEGMENT_MS,
            "segmentation_strategy": "full_audio_vad_then_asr",
            "model_class": self.model_class,
            "supports_model_hotwords": self.supports_model_hotwords,
            "hotwords_requested": self.hotwords_requested,
            "hotwords_applied": self.hotwords_applied,
        }

    def transcribe_window(self, audio_path: Path, context: ASRContext) -> BackendResult:
        try:
            import soundfile as sf
        except ImportError as exc:
            raise RuntimeError("soundfile is required by the Paraformer backend") from exc

        kwargs: dict[str, Any] = {}
        self.hotwords_requested = bool(context.hotwords)
        self.hotwords_applied = bool(context.hotwords and self.supports_model_hotwords)
        if self.hotwords_applied:
            kwargs["hotword"] = context.hotwords

        waveform, sample_rate = sf.read(str(audio_path), dtype="float32")
        if sample_rate <= 0:
            raise RuntimeError(f"Invalid audio sample rate: {sample_rate}")
        if getattr(waveform, "ndim", 1) > 1:
            waveform = waveform.mean(axis=1)

        duration = len(waveform) / float(sample_rate)
        vad_result = self._vad_model.generate(input=str(audio_path))
        boundaries = _vad_boundaries_from_result(vad_result)
        if not boundaries and duration > 0:
            boundaries = [(0.0, duration)]

        segments: list[dict[str, Any]] = []
        for start, end in boundaries:
            start = max(0.0, min(start, duration))
            end = max(start, min(end, duration))
            start_sample = int(start * sample_rate)
            end_sample = int(end * sample_rate)
            if end_sample <= start_sample:
                continue

            result = self._asr_model.generate(
                input=waveform[start_sample:end_sample],
                **kwargs,
            )
            text = _text_from_result(result)
            if text:
                segments.append(
                    {
                        "start": round(start, 2),
                        "end": round(end, 2),
                        "text": text,
                    }
                )

        info = TranscriptionInfo(
            language=context.language or "zh",
            language_probability=1.0,
            backend=self.name,
            model_path=self.model_path,
            metadata=self.metadata(),
        )
        return BackendResult(segments, info)

    def close(self) -> None:
        self._asr_model = None
        self._vad_model = None