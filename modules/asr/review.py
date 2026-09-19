"""Versioned subprocess protocol for batched local ASR review."""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 1


def build_review_request(
    audio_path: str | Path,
    segments: list[dict[str, Any]],
    *,
    backend: str = "faster_whisper",
    language: str = "zh",
    hotwords: str | list[str] | None = None,
    model_path: str | Path | None = None,
    device: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    request = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id or f"asr-review-{uuid.uuid4().hex}",
        "audio_path": str(Path(audio_path)),
        "backend": backend,
        "language": language,
        "hotwords": hotwords or "",
        "segments": segments,
    }
    if model_path is not None:
        request["model_path"] = str(model_path)
    if device is not None:
        request["device"] = device
    validate_review_request(request)
    return request


def validate_review_request(request: dict[str, Any]) -> None:
    if request.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("Unsupported ASR review protocol version")
    if not str(request.get("request_id", "")).strip():
        raise ValueError("ASR review request_id is required")
    if not str(request.get("audio_path", "")).strip():
        raise ValueError("ASR review audio_path is required")
    if request.get("backend") not in {"faster_whisper", "paraformer"}:
        raise ValueError("Unsupported ASR review backend")
    segments = request.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("ASR review requires at least one segment")
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise ValueError(f"segments[{index}] must be an object")
        try:
            start = float(segment["start"])
            end = float(segment["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"segments[{index}] has invalid bounds") from exc
        if start < 0 or end <= start:
            raise ValueError(f"segments[{index}] has invalid bounds")


def build_review_segments(
    corrections: list[dict[str, Any]],
    *,
    padding_seconds: float = 10.0,
) -> list[dict[str, Any]]:
    candidates = []
    for correction in corrections:
        if not correction.get("review_needed"):
            continue
        start = correction.get("segment_start")
        end = correction.get("segment_end")
        if start is None or end is None:
            continue
        context = f"{correction.get('original', '')}=>{correction.get('candidate', '')}"
        candidates.append(
            {
                "start": max(0.0, float(start) - padding_seconds),
                "end": float(end) + padding_seconds,
                "contexts": [context],
            }
        )
    candidates.sort(key=lambda item: (item["start"], item["end"]))

    merged: list[dict[str, Any]] = []
    for candidate in candidates:
        if merged and candidate["start"] <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], candidate["end"])
            for context in candidate["contexts"]:
                if context not in merged[-1]["contexts"]:
                    merged[-1]["contexts"].append(context)
        else:
            merged.append(candidate)
    return [
        {
            "start": round(item["start"], 3),
            "end": round(item["end"], 3),
            "context": "; ".join(item["contexts"]),
        }
        for item in merged
    ]

def run_review_subprocess(
    request: dict[str, Any],
    *,
    python_executable: str | Path,
    worker_path: str | Path,
    timeout_seconds: float = 600.0,
) -> dict[str, Any]:
    validate_review_request(request)
    completed = subprocess.run(
        [str(python_executable), str(worker_path)],
        input=json.dumps(request, ensure_ascii=False).encode("utf-8"),
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    stdout = (
        completed.stdout.decode("utf-8")
        if isinstance(completed.stdout, bytes)
        else completed.stdout or ""
    )
    stderr = (
        completed.stderr.decode("utf-8", errors="replace")
        if isinstance(completed.stderr, bytes)
        else completed.stderr or ""
    )
    try:
        response = json.loads(stdout)
    except json.JSONDecodeError as exc:
        stderr = stderr.strip()
        raise RuntimeError(
            f"ASR review worker returned invalid JSON (exit={completed.returncode}): {stderr}"
        ) from exc
    if response.get("protocol_version") != PROTOCOL_VERSION:
        raise RuntimeError("ASR review worker protocol mismatch")
    if response.get("request_id") != request["request_id"]:
        raise RuntimeError("ASR review worker request_id mismatch")
    if completed.returncode != 0 or response.get("status") != "ok":
        raise RuntimeError(response.get("error") or stderr.strip() or "ASR review failed")
    return response