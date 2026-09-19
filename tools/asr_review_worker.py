"""JSON-only child process for batched ASR segment review."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_VERSION = 1


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


def _configure_environment(request: dict) -> None:
    backend = request["backend"]
    model_path = request.get("model_path")
    device = request.get("device")
    if backend == "faster_whisper":
        if model_path:
            os.environ["WHISPER_MODEL_PATH"] = str(model_path)
        if device:
            os.environ["WHISPER_DEVICE"] = str(device)
    else:
        if model_path:
            os.environ["PARAFORMER_MODEL_PATH"] = str(model_path)
        if device:
            os.environ["PARAFORMER_DEVICE"] = str(device)


def main() -> int:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    request_id = "unknown"
    backend = None
    try:
        request = json.loads(sys.stdin.read())
        request_id = str(request.get("request_id", "unknown"))
        if request.get("protocol_version") != PROTOCOL_VERSION:
            raise ValueError("unsupported protocol version")
        _configure_environment(request)
        sys.path.insert(0, str(ROOT))
        with contextlib.redirect_stdout(sys.stderr):
            from config import TEMP_DIR
            from modules.asr.factory import create_asr_backend
            from modules.asr.review import validate_review_request
            from modules.asr.types import ASRContext
            from modules.transcribe import _slice_audio

            validate_review_request(request)
            audio_path = Path(request["audio_path"])
            if not audio_path.is_file():
                raise FileNotFoundError(audio_path)
            backend = create_asr_backend(request["backend"])
            context = ASRContext(
                language=request.get("language", "zh"),
                hotwords=request.get("hotwords") or None,
            )
            results = []
            with tempfile.TemporaryDirectory(prefix="asr_review_", dir=str(TEMP_DIR)) as temp_dir:
                for index, segment in enumerate(request["segments"]):
                    start = float(segment["start"])
                    end = float(segment["end"])
                    chunk_path = Path(temp_dir) / f"segment_{index:04d}.wav"
                    _slice_audio(audio_path, chunk_path, start, end - start)
                    result = backend.transcribe_window(chunk_path, context)
                    local_segments = []
                    for item in result.segments:
                        local_segments.append(
                            {
                                "start": round(float(item["start"]) + start, 3),
                                "end": round(float(item["end"]) + start, 3),
                                "text": str(item["text"]),
                            }
                        )
                    results.append(
                        {
                            "start": start,
                            "end": end,
                            "context": segment.get("context", ""),
                            "text": " ".join(item["text"] for item in local_segments).strip(),
                            "segments": local_segments,
                        }
                    )
            backend_metadata = backend.metadata()
        _emit(
            {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": request_id,
                "status": "ok",
                "backend": request["backend"],
                "backend_metadata": backend_metadata,
                "results": results,
                "error": None,
            }
        )
        return 0
    except Exception as exc:
        _emit(
            {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": request_id,
                "status": "error",
                "results": [],
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        return 1
    finally:
        if backend is not None:
            with contextlib.suppress(Exception):
                backend.close()


if __name__ == "__main__":
    raise SystemExit(main())