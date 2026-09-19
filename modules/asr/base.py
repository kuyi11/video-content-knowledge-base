"""Base protocol for ASR backends."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from modules.asr.types import ASRContext, BackendResult


class ASRBackend(Protocol):
    name: str

    def metadata(self) -> dict:
        ...

    def transcribe_window(self, audio_path: Path, context: ASRContext) -> BackendResult:
        ...

    def close(self) -> None:
        ...
