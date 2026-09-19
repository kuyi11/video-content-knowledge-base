"""Shared ASR backend result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class TranscriptionInfo:
    language: str = "unknown"
    language_probability: float = 1.0
    backend: str = ""
    model_path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BackendResult:
    segments: list[dict[str, Any]]
    info: TranscriptionInfo


@dataclass(slots=True)
class ASRContext:
    language: str | None = None
    hotwords: str | list[str] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def normalize_backend_name(name: str | None) -> str:
    normalized = str(name or "faster_whisper").strip().lower().replace("-", "_")
    if normalized in {"whisper", "fasterwhisper"}:
        return "faster_whisper"
    return normalized or "faster_whisper"


def backend_slug(name: str | None) -> str:
    return normalize_backend_name(name).replace("_", "-")
