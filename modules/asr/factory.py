"""ASR backend factory."""

from __future__ import annotations

from modules.asr.types import normalize_backend_name


def create_asr_backend(name: str | None = None):
    backend = normalize_backend_name(name)
    if backend == "faster_whisper":
        from modules.asr.faster_whisper_backend import FasterWhisperBackend

        return FasterWhisperBackend()
    if backend == "paraformer":
        from modules.asr.paraformer_backend import ParaformerBackend

        return ParaformerBackend()
    raise ValueError(f"Unsupported ASR backend: {name}")
