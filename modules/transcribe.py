import gc
import hashlib
import json
import logging
import os
import re
import tempfile
from difflib import SequenceMatcher
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import ffmpeg
import torch
from faster_whisper import WhisperModel

from config import (
    ASR_BACKEND,
    ASR_HOTWORDS_MAX,
    PARAFORMER_DEVICE,
    PARAFORMER_MODEL_PATH,
    PARAFORMER_PUNC_PATH,
    PARAFORMER_VAD_MAX_SEGMENT_MS,
    PARAFORMER_VAD_PATH,
    TEMP_DIR,
    WHISPER_MODEL_PATH,
    WHISPER_DEVICE,
    WHISPER_COMPUTE_TYPE,
    WHISPER_LANGUAGE,
    MAX_DIRECT_DURATION,
    SEGMENT_MINUTES,
    SEGMENT_OVERLAP,
    FFMPEG_BIN,
    FFPROBE_BIN,
)
from modules.asr.types import ASRContext, backend_slug, normalize_backend_name

logger = logging.getLogger(__name__)


def transcribe(
    audio_path: str,
    language: str | None = WHISPER_LANGUAGE,
    force: bool = False,
    output_path: str | Path | None = None,
    asr_backend: str | None = None,
    hotwords: str | list[str] | None = None,
    domains: str | list[str] | tuple[str, ...] | None = None,
) -> str:
    audio = Path(audio_path)
    output = Path(output_path) if output_path else audio.with_suffix(".txt")
    metadata_path = output.with_suffix(".meta.json")
    backend_name = normalize_backend_name(asr_backend or ASR_BACKEND)
    if not force and output.exists() and _cache_is_valid(
        audio, metadata_path, language, backend_name, hotwords=hotwords, domains=domains
    ):
        return str(output)

    backend = None
    model = None
    try:
        if backend_name == "faster_whisper":
            model = WhisperModel(
                WHISPER_MODEL_PATH,
                device=WHISPER_DEVICE,
                compute_type=WHISPER_COMPUTE_TYPE,
            )
            segments, info = _transcribe_audio(model, audio, language, hotwords=hotwords)
            backend_metadata = {
                **_whisper_metadata(),
                "hotwords_applied": bool(hotwords),
            }
        else:
            from modules.asr.factory import create_asr_backend

            backend = create_asr_backend(backend_name)
            segments, info = _transcribe_audio_backend(
                backend,
                audio,
                language,
                hotwords=hotwords,
            )
            backend_metadata = backend.metadata()

        lines = [
            f"# Language: {info.language} (probability: {float(info.language_probability):.4f})",
            f"# ASR backend: {backend_name}",
            "",
        ]
        for seg in segments:
            lines.append(f"[{seg['start']:.1f}s -> {seg['end']:.1f}s] {seg['text']}")

        text = "\n".join(lines)
        _atomic_write_text(output, text)
        metadata = _cache_metadata(
            audio,
            language,
            backend_name,
            backend_metadata,
            hotwords=hotwords,
            domains=domains,
        )
        metadata.update(
            {
                "language_detected": getattr(info, "language", None),
                "language_probability": getattr(info, "language_probability", None),
                "transcript_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            }
        )
        raw_path = _write_backend_artifact(output, text, metadata)
        metadata["raw_asr_path"] = str(raw_path)
        metadata.update(_write_correction_artifacts(raw_path, text, metadata))
        _atomic_write_text(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2))
        return str(output)
    finally:
        if backend is not None:
            try:
                backend.close()
            except Exception:
                logger.debug("ASR backend close failed", exc_info=True)
        if model is not None:
            del model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        gc.collect()


def _audio_digest(audio: Path) -> str:
    digest = hashlib.sha256()
    with audio.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _requested_language(language: str | None) -> str:
    normalized = str(language or "auto").strip()
    return normalized or "auto"


def _model_language(language: str | None) -> str | None:
    requested = _requested_language(language)
    return None if requested.lower() == "auto" else requested



def _normalize_domains(domains: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if domains is None:
        return []
    values = domains.split(",") if isinstance(domains, str) else domains
    normalized = []
    for value in values:
        name = str(value).strip().lower()
        if name and name not in normalized:
            normalized.append(name)
    return normalized


def _hotword_digest(hotwords: str | list[str] | None) -> str:
    if not hotwords:
        return "none"
    payload = hotwords if isinstance(hotwords, str) else list(hotwords)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

def _glossary_digest(
    domains: str | list[str] | tuple[str, ...] | None,
) -> str:
    normalized = _normalize_domains(domains)
    if not normalized:
        return "none"
    from modules.transcript_normalizer import glossary_fingerprint

    return glossary_fingerprint(domains=normalized)

def _whisper_metadata() -> dict:
    return {
        "model_path": str(WHISPER_MODEL_PATH),
        "device": WHISPER_DEVICE,
        "compute_type": WHISPER_COMPUTE_TYPE,
        "supports_model_hotwords": True,
    }


def _paraformer_metadata() -> dict:
    return {
        "model_path": str(PARAFORMER_MODEL_PATH),
        "vad_model_path": str(PARAFORMER_VAD_PATH),
        "punc_model_path": str(PARAFORMER_PUNC_PATH),
        "device": PARAFORMER_DEVICE,
        "timestamp_source": "fsmn-vad",
        "vad_max_segment_ms": PARAFORMER_VAD_MAX_SEGMENT_MS,
        "segmentation_strategy": "full_audio_vad_then_asr",
    }


def _correction_artifacts_exist(metadata: dict[str, Any]) -> bool:
    if not metadata.get("domains"):
        return True
    corrected_path = metadata.get("corrected_asr_path")
    corrections_path = metadata.get("corrections_path")
    return bool(
        corrected_path
        and corrections_path
        and Path(corrected_path).is_file()
        and Path(corrections_path).is_file()
    )

def _cache_metadata(
    audio: Path,
    language: str | None,
    backend_name: str | None = None,
    backend_metadata: dict[str, Any] | None = None,
    *,
    hotwords: str | list[str] | None = None,
    domains: str | list[str] | tuple[str, ...] | None = None,
) -> dict:
    requested = _requested_language(language)
    normalized_backend = normalize_backend_name(backend_name or ASR_BACKEND)
    metadata = {
        "version": 4,
        "text_source": "asr",
        "asr_backend": normalized_backend,
        "audio_sha256": _audio_digest(audio),
        "language": requested,
        "language_requested": requested,
        "fallback_chain": ["platform:not_found", "embedded:not_available", "asr:selected"],
        "hotwords_max": ASR_HOTWORDS_MAX,
        "hotword_sha256": _hotword_digest(hotwords),
        "hotwords_requested": bool(hotwords),
        "domains": _normalize_domains(domains),
        "glossary_fingerprint": _glossary_digest(domains),
    }
    if backend_metadata:
        metadata.update(backend_metadata)
    elif normalized_backend == "faster_whisper":
        metadata.update(_whisper_metadata())
        metadata["hotwords_applied"] = bool(hotwords)
    elif normalized_backend == "paraformer":
        metadata.update(_paraformer_metadata())
    return metadata


def _cache_is_valid(
    audio: Path,
    metadata_path: Path,
    language: str | None,
    backend_name: str | None = None,
    *,
    hotwords: str | list[str] | None = None,
    domains: str | list[str] | tuple[str, ...] | None = None,
) -> bool:
    if not metadata_path.exists():
        return False
    try:
        expected = _cache_metadata(
            audio, language, backend_name, hotwords=hotwords, domains=domains
        )
        actual = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        all(actual.get(key) == value for key, value in expected.items())
        and _correction_artifacts_exist(actual)
    )


def get_cached_asr_transcript(
    video_id: str,
    *,
    asr_backend: str | None = None,
    hotwords: str | list[str] | None = None,
    domains: str | list[str] | tuple[str, ...] | None = None,
) -> str | None:
    """Return a complete cached ASR transcript without touching the audio stage."""
    output = TEMP_DIR / f"{video_id}_norm.txt"
    metadata_path = output.with_suffix(".meta.json")
    expected_backend = normalize_backend_name(asr_backend or ASR_BACKEND)
    if not output.exists() or not metadata_path.exists():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        expected_digest = metadata.get("transcript_sha256")
        if (
            metadata.get("version") != 4
            or metadata.get("text_source") != "asr"
            or metadata.get("asr_backend") != expected_backend
            or not expected_digest
            or hashlib.sha256(output.read_bytes()).hexdigest() != expected_digest
            or metadata.get("language_requested") != _requested_language(WHISPER_LANGUAGE)
            or metadata.get("hotword_sha256") != _hotword_digest(hotwords)
            or metadata.get("domains") != _normalize_domains(domains)
            or metadata.get("glossary_fingerprint") != _glossary_digest(domains)
        ):
            return None
        if expected_backend == "faster_whisper" and (
            metadata.get("model_path") != str(WHISPER_MODEL_PATH)
            or metadata.get("device") != WHISPER_DEVICE
            or metadata.get("compute_type") != WHISPER_COMPUTE_TYPE
        ):
            return None
        if expected_backend == "paraformer" and any(
            metadata.get(key) != value
            for key, value in _paraformer_metadata().items()
        ):
            return None
        if not _correction_artifacts_exist(metadata):
            return None
    except (OSError, json.JSONDecodeError):
        return None
    return str(output)


def _atomic_write_text(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as stream:
        temp_path = Path(stream.name)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(str(temp_path), str(path))
    finally:
        temp_path.unlink(missing_ok=True)


def _backend_artifact_path(output: Path, backend_name: str) -> Path:
    stem = output.stem
    video_id = stem[:-5] if stem.endswith("_norm") else stem
    return output.with_name(f"{video_id}__asr__{backend_slug(backend_name)}.raw.txt")


def _write_backend_artifact(output: Path, text: str, metadata: dict[str, Any]) -> Path:
    backend_name = metadata.get("asr_backend", ASR_BACKEND)
    raw_path = _backend_artifact_path(output, backend_name)
    _atomic_write_text(raw_path, text)
    raw_metadata = dict(metadata)
    raw_metadata["artifact_role"] = "raw_asr"
    raw_metadata["transcript_sha256"] = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    _atomic_write_text(raw_path.with_suffix(".meta.json"), json.dumps(raw_metadata, ensure_ascii=False, indent=2))
    return raw_path


def _write_correction_artifacts(
    raw_path: Path,
    text: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    domains = metadata.get("domains") or []
    if not domains:
        return {
            "correction_version": 1,
            "correction_count": 0,
            "review_needed_count": 0,
        }

    from modules.transcript_normalizer import analyze_asr_terms, glossary_fingerprint

    corrected, corrections = analyze_asr_terms(text, domains=domains)
    corrected_path = raw_path.with_name(raw_path.name.replace(".raw.txt", ".corrected.txt"))
    corrections_path = raw_path.with_name(raw_path.name.replace(".raw.txt", ".corrections.json"))
    fingerprint = glossary_fingerprint(domains=domains)
    report = {
        "version": 1,
        "asr_backend": metadata.get("asr_backend"),
        "domains": list(domains),
        "glossary_fingerprint": fingerprint,
        "raw_asr_path": str(raw_path),
        "corrected_asr_path": str(corrected_path),
        "corrections": corrections,
    }
    _atomic_write_text(corrected_path, corrected)
    _atomic_write_text(
        corrections_path,
        json.dumps(report, ensure_ascii=False, indent=2),
    )
    return {
        "correction_version": 1,
        "glossary_fingerprint": fingerprint,
        "corrected_asr_path": str(corrected_path),
        "corrections_path": str(corrections_path),
        "correction_count": len(corrections),
        "review_needed_count": sum(
            1 for correction in corrections if correction["review_needed"]
        ),
    }

def _transcribe_audio(
    model: WhisperModel,
    audio: Path,
    language: str | None,
    hotwords: str | list[str] | None = None,
):
    duration = _probe_duration(audio)
    if duration is not None and duration > MAX_DIRECT_DURATION:
        return _transcribe_long_audio(
            model, audio, language, duration, hotwords=hotwords
        )
    return _transcribe_window(model, audio, language, hotwords=hotwords)

def _transcribe_audio_backend(
    backend,
    audio: Path,
    language: str | None,
    hotwords: str | list[str] | None = None,
):
    duration = _probe_duration(audio)
    context = ASRContext(language=language, hotwords=hotwords)
    if (
        duration is not None
        and duration > MAX_DIRECT_DURATION
        and not getattr(backend, "handles_long_audio", False)
    ):
        return _transcribe_long_audio_backend(backend, audio, context, duration)
    result = backend.transcribe_window(audio, context)
    return result.segments, result.info


def _transcribe_window(
    model: WhisperModel,
    audio: Path,
    language: str | None,
    hotwords: str | list[str] | None = None,
):
    kwargs = {
        "language": _model_language(language),
        "vad_filter": True,
    }
    if hotwords:
        kwargs["hotwords"] = hotwords
    segments, info = model.transcribe(str(audio), **kwargs)
    return _collect_segments(segments), info


def _transcribe_long_audio(
    model: WhisperModel,
    audio: Path,
    language: str | None,
    duration: float,
    hotwords: str | list[str] | None = None,
):
    all_segments = []
    header_info = None
    window_language = language
    with tempfile.TemporaryDirectory(prefix=f"{audio.stem}_chunks_", dir=str(TEMP_DIR)) as tmp_dir:
        work_dir = Path(tmp_dir)
        for start, end, chunk_path in _build_audio_windows(audio, duration, work_dir):
            try:
                chunk_segments, info = _transcribe_window(
                    model, chunk_path, window_language, hotwords=hotwords
                )
            finally:
                chunk_path.unlink(missing_ok=True)
            if header_info is None:
                header_info = info
                if _model_language(language) is None and getattr(info, "language", None):
                    window_language = info.language
            for seg in chunk_segments:
                seg["start"] = round(seg["start"] + start, 2)
                seg["end"] = round(seg["end"] + start, 2)
            all_segments.extend(chunk_segments)

    if header_info is None:
        header_info = SimpleNamespace(
            language=_model_language(language) or "unknown",
            language_probability=1.0,
        )

    return _dedup_segments(all_segments), header_info

def _transcribe_long_audio_backend(backend, audio: Path, context: ASRContext, duration: float):
    all_segments = []
    header_info = None
    window_language = context.language
    with tempfile.TemporaryDirectory(prefix=f"{audio.stem}_chunks_", dir=str(TEMP_DIR)) as tmp_dir:
        work_dir = Path(tmp_dir)
        for start, end, chunk_path in _build_audio_windows(audio, duration, work_dir):
            try:
                window_context = ASRContext(
                    language=window_language,
                    hotwords=context.hotwords,
                    metadata=context.metadata,
                )
                result = backend.transcribe_window(chunk_path, window_context)
                chunk_segments, info = result.segments, result.info
            finally:
                chunk_path.unlink(missing_ok=True)
            if header_info is None:
                header_info = info
                if _model_language(context.language) is None and getattr(info, "language", None):
                    window_language = info.language
            for seg in chunk_segments:
                seg["start"] = round(seg["start"] + start, 2)
                seg["end"] = round(seg["end"] + start, 2)
            all_segments.extend(chunk_segments)

    if header_info is None:
        header_info = SimpleNamespace(
            language=_model_language(context.language) or "unknown",
            language_probability=1.0,
        )

    return _dedup_segments(all_segments), header_info


def _build_audio_windows(audio: Path, duration: float, work_dir: Path):
    segment_seconds = max(1, int(SEGMENT_MINUTES * 60))
    overlap_seconds = max(0, int(SEGMENT_OVERLAP))
    step = segment_seconds - overlap_seconds
    if step <= 0:
        raise ValueError("SEGMENT_MINUTES must be larger than SEGMENT_OVERLAP")

    start = 0.0
    index = 0
    while start < duration:
        end = min(start + segment_seconds, duration)
        if end <= start:
            break
        chunk_path = work_dir / f"{audio.stem}_part{index:03d}_{int(start)}_{int(end)}.wav"
        _slice_audio(audio, chunk_path, start, end - start)
        yield round(start, 2), round(end, 2), chunk_path
        if end >= duration:
            break
        start += step
        index += 1


def _slice_audio(audio: Path, output_path: Path, start: float, duration: float):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        (
            ffmpeg
            .input(str(audio), ss=start, t=duration)
            .output(
                str(output_path),
                ac=1,
                ar=16000,
                sample_fmt="s16",
            )
            .overwrite_output()
            .run(cmd=str(FFMPEG_BIN), capture_stdout=True, capture_stderr=True)
        )
    except ffmpeg.Error as e:
        raise RuntimeError(
            f"Failed to slice audio: {e.stderr.decode() if e.stderr else 'N/A'}"
        ) from e


def _probe_duration(audio: Path) -> float | None:
    try:
        probe = ffmpeg.probe(str(audio), cmd=str(FFPROBE_BIN))
    except ffmpeg.Error as e:
        logger.warning("[transcribe] Probe failed for %s: %s", audio, e)
        return None

    for stream in probe.get("streams", []):
        if stream.get("codec_type") != "audio":
            continue
        duration = stream.get("duration")
        if duration is not None:
            try:
                return float(duration)
            except (TypeError, ValueError):
                pass

    format_duration = probe.get("format", {}).get("duration")
    if format_duration is not None:
        try:
            return float(format_duration)
        except (TypeError, ValueError):
            return None
    return None


def _collect_segments(segments):
    result = []
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        result.append(
            {
                "start": round(float(seg.start), 2),
                "end": round(float(seg.end), 2),
                "text": text,
            }
        )
    return result


def _dedup_segments(segments: list[dict]) -> list[dict]:
    if not segments:
        return []

    ordered = sorted(segments, key=lambda s: (s["start"], s["end"]))
    deduped = [ordered[0]]
    for seg in ordered[1:]:
        if any(_segment_is_duplicate(prev, seg) for prev in deduped[-4:]):
            continue
        deduped.append(seg)
    return deduped


def _segment_is_duplicate(prev: dict, current: dict) -> bool:
    overlap = min(prev["end"], current["end"]) - max(prev["start"], current["start"])
    if overlap <= 0:
        return False

    prev_text = _normalize_text(prev["text"])
    curr_text = _normalize_text(current["text"])
    if not prev_text or not curr_text:
        return False
    if prev_text == curr_text:
        return True
    if prev_text in curr_text or curr_text in prev_text:
        return True

    similarity = SequenceMatcher(None, prev_text, curr_text).ratio()
    if similarity >= 0.9:
        return True

    short_span = min(prev["end"] - prev["start"], current["end"] - current["start"])
    if overlap >= min(2.0, max(0.5, short_span * 0.5)) and similarity >= 0.78:
        return True
    return False


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text)
