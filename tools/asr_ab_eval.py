"""Run isolated P0/P1/W0/W1 ASR evaluations without touching the vault."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "temp" / "evaluation"


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def build_jobs(audio_path: Path, output_dir: Path, domains: list[str]) -> list[dict]:
    stem = audio_path.stem[:-5] if audio_path.stem.endswith("_norm") else audio_path.stem
    jobs = []
    for label, backend, use_hotwords in (
        ("P0", "paraformer", False),
        ("P1", "paraformer", True),
        ("W0", "faster_whisper", False),
        ("W1", "faster_whisper", True),
    ):
        prefix = output_dir / f"{stem}__ab__{label}"
        jobs.append(
            {
                "label": label,
                "backend": backend,
                "use_hotwords": use_hotwords,
                "domains": domains if use_hotwords else [],
                "audio_path": str(audio_path),
                "output_path": str(prefix.with_suffix(".txt")),
                "result_path": str(prefix.with_suffix(".result.json")),
                "log_path": str(prefix.with_suffix(".log")),
            }
        )
    return jobs


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_child(job_path: Path) -> int:
    sys.path.insert(0, str(ROOT))
    from modules.asr.glossary import build_backend_hotwords, load_domain_entries
    from modules.subtitle import load_transcript_metadata
    from modules.transcribe import transcribe

    job = json.loads(job_path.read_text(encoding="utf-8"))
    domains = job["domains"]
    hotwords = (
        build_backend_hotwords(load_domain_entries(domains), job["backend"])
        if job["use_hotwords"]
        else ""
    )
    started = time.perf_counter()
    result = {
        "protocol_version": 1,
        "label": job["label"],
        "backend": job["backend"],
        "use_hotwords": job["use_hotwords"],
        "domains": domains,
        "status": "error",
    }
    try:
        output_path = Path(
            transcribe(
                job["audio_path"],
                language=job.get("language", "zh"),
                force=True,
                output_path=job["output_path"],
                asr_backend=job["backend"],
                hotwords=hotwords,
                domains=domains,
            )
        )
        metadata = load_transcript_metadata(output_path)
        result.update(
            {
                "status": "ok",
                "output_path": str(output_path),
                "metadata_path": str(output_path.with_suffix(".meta.json")),
                "sha256": _sha256(output_path),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "device": metadata.get("device"),
                "model_path": metadata.get("model_path"),
                "hotword_sha256": metadata.get("hotword_sha256"),
                "glossary_fingerprint": metadata.get("glossary_fingerprint"),
                "correction_count": metadata.get("correction_count", 0),
                "review_needed_count": metadata.get("review_needed_count", 0),
                "model_class": metadata.get("model_class"),
                "supports_model_hotwords": metadata.get("supports_model_hotwords"),
                "hotwords_requested": metadata.get("hotwords_requested", False),
                "hotwords_applied": metadata.get("hotwords_applied", False),
            }
        )
    except Exception as exc:
        result.update(
            {
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
    _atomic_write_json(Path(job["result_path"]), result)
    return 0 if result["status"] == "ok" else 1


def run_matrix(args) -> int:
    audio_path = Path(args.audio).resolve()
    if not audio_path.is_file():
        raise FileNotFoundError(audio_path)
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    domains = [item.strip().lower() for item in args.domains.split(",") if item.strip()]
    jobs = build_jobs(audio_path, output_dir, domains)
    manifest_path = output_dir / f"{audio_path.stem}__ab__manifest.json"
    manifest = {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "audio_path": str(audio_path),
        "audio_sha256": _sha256(audio_path),
        "language": args.language,
        "domains": domains,
        "execution": "sequential",
        "vault_updated": False,
        "index_updated": False,
        "jobs": [],
    }
    _atomic_write_json(manifest_path, manifest)

    interpreters = {
        "paraformer": Path(args.paraformer_python).resolve(),
        "faster_whisper": Path(args.whisper_python).resolve(),
    }
    for job in jobs:
        interpreter = interpreters[job["backend"]]
        if not interpreter.is_file():
            raise FileNotFoundError(interpreter)
        job["language"] = args.language
        job_path = Path(job["result_path"]).with_suffix(".job.json")
        _atomic_write_json(job_path, job)
        with Path(job["log_path"]).open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                [str(interpreter), str(Path(__file__).resolve()), "--child", str(job_path)],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
            )
        result_path = Path(job["result_path"])
        result = (
            json.loads(result_path.read_text(encoding="utf-8"))
            if result_path.exists()
            else {
                "label": job["label"],
                "backend": job["backend"],
                "status": "error",
                "error": f"child exited {completed.returncode} without result",
            }
        )
        result["returncode"] = completed.returncode
        result["log_path"] = job["log_path"]
        manifest["jobs"].append(result)
        _atomic_write_json(manifest_path, manifest)
        if completed.returncode and not args.continue_on_error:
            break

    manifest["status"] = (
        "ok" if len(manifest["jobs"]) == 4 and all(job["status"] == "ok" for job in manifest["jobs"])
        else "partial"
    )
    _atomic_write_json(manifest_path, manifest)
    print(manifest_path)
    return 0 if manifest["status"] == "ok" else 1


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", nargs="?")
    parser.add_argument("--domains", default="")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--whisper-python", default=str(ROOT / ".venv" / "Scripts" / "python.exe"))
    parser.add_argument("--paraformer-python", default=str(ROOT / ".venv-funasr-test" / "Scripts" / "python.exe"))
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--child", type=Path, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.child:
        return run_child(args.child)
    if not args.audio:
        raise SystemExit("audio path is required")
    return run_matrix(args)


if __name__ == "__main__":
    raise SystemExit(main())