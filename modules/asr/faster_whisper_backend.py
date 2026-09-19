"""faster-whisper ASR backend adapter."""

from __future__ import annotations

from pathlib import Path

from faster_whisper import WhisperModel

from config import WHISPER_COMPUTE_TYPE, WHISPER_DEVICE, WHISPER_MODEL_PATH
from modules.asr.types import ASRContext, BackendResult, TranscriptionInfo


def _model_language(language: str | None) -> str | None:
    requested = str(language or "auto").strip()
    return None if not requested or requested.lower() == "auto" else requested


class FasterWhisperBackend:
    name = "faster_whisper"

    def __init__(
        self,
        model_path: str = WHISPER_MODEL_PATH,
        device: str = WHISPER_DEVICE,
        compute_type: str = WHISPER_COMPUTE_TYPE,
    ):
        self.model_path = str(model_path)
        self.device = device
        self.compute_type = compute_type
        self._model = WhisperModel(
            self.model_path,
            device=self.device,
            compute_type=self.compute_type,
        )

    def metadata(self) -> dict:
        return {
            "asr_backend": self.name,
            "model_path": self.model_path,
            "device": self.device,
            "compute_type": self.compute_type,
        }

    def transcribe_window(self, audio_path: Path, context: ASRContext) -> BackendResult:
        kwargs = {
            "language": _model_language(context.language),
            "vad_filter": True,
        }
        if context.hotwords:
            kwargs["hotwords"] = context.hotwords
        segments, info = self._model.transcribe(str(audio_path), **kwargs)
        collected = []
        for seg in segments:
            text = seg.text.strip()
            if not text:
                continue
            collected.append(
                {
                    "start": round(float(seg.start), 2),
                    "end": round(float(seg.end), 2),
                    "text": text,
                }
            )
        transcription_info = TranscriptionInfo(
            language=getattr(info, "language", None) or "unknown",
            language_probability=float(getattr(info, "language_probability", 1.0) or 0.0),
            backend=self.name,
            model_path=self.model_path,
            metadata=self.metadata(),
        )
        return BackendResult(collected, transcription_info)

    def close(self) -> None:
        self._model = None
