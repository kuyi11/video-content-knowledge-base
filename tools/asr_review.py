"""Review timestamped ASR corrections with one isolated backend process."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from modules.asr.review import (
    build_review_request,
    build_review_segments,
    run_review_subprocess,
)

ROOT = Path(__file__).resolve().parents[1]


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio")
    parser.add_argument("corrections")
    parser.add_argument("--backend", choices=["faster_whisper", "paraformer"], default="faster_whisper")
    parser.add_argument("--python", dest="python_executable")
    parser.add_argument("--padding", type=float, default=10.0)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--language", default="zh")
    parser.add_argument("--output")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    audio_path = Path(args.audio).resolve()
    corrections_path = Path(args.corrections).resolve()
    report = json.loads(corrections_path.read_text(encoding="utf-8"))
    review_segments = build_review_segments(
        report.get("corrections", []),
        padding_seconds=args.padding,
    )
    if not review_segments:
        raise SystemExit("no timestamped review_needed corrections found")

    default_python = (
        ROOT / ".venv-funasr-test" / "Scripts" / "python.exe"
        if args.backend == "paraformer"
        else ROOT / ".venv" / "Scripts" / "python.exe"
    )
    request = build_review_request(
        audio_path,
        review_segments,
        backend=args.backend,
        language=args.language,
        request_id=f"{audio_path.stem}-{args.backend}-review",
    )
    response = run_review_subprocess(
        request,
        python_executable=Path(args.python_executable).resolve() if args.python_executable else default_python,
        worker_path=ROOT / "tools" / "asr_review_worker.py",
        timeout_seconds=args.timeout,
    )
    output_path = (
        Path(args.output).resolve()
        if args.output
        else corrections_path.with_name(
            f"{corrections_path.stem}.{args.backend}.review.json"
        )
    )
    payload = {
        "version": 1,
        "audio_path": str(audio_path),
        "corrections_path": str(corrections_path),
        "review_backend": args.backend,
        "padding_seconds": args.padding,
        "request": request,
        "response": response,
        "automatic_replacement": False,
    }
    _atomic_write(output_path, payload)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())