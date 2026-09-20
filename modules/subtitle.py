"""Platform and embedded subtitle resolution for the transcript stage."""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from yt_dlp import YoutubeDL

from config import (
    FFMPEG_BIN,
    FFPROBE_BIN,
    SUBTITLE_ALLOW_ANY_LANGUAGE,
    SUBTITLE_ENABLED,
    SUBTITLE_LANGUAGES,
    TEMP_DIR,
)

logger = logging.getLogger(__name__)

SUBTITLE_PARSER_VERSION = "subtitle-parser-v1"
TIMESTAMP_RE = re.compile(
    r"(?P<start>(?:\d{1,2}:)?\d{1,2}:\d{2}[,.]\d{3}|\d{1,2}:\d{2}[,.]\d{3})"
    r"\s*-->\s*"
    r"(?P<end>(?:\d{1,2}:)?\d{1,2}:\d{2}[,.]\d{3}|\d{1,2}:\d{2}[,.]\d{3})"
)
TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True, slots=True)
class SubtitleTrack:
    language: str
    is_auto: bool
    extension: str
    url: str = ""
    data: str = ""
    name: str = ""

    @property
    def text_source(self) -> str:
        return "subtitle_auto" if self.is_auto else "subtitle_manual"

    @property
    def track_id(self) -> str:
        kind = "auto" if self.is_auto else "manual"
        return f"{kind}:{self.language}:{self.name}".strip(":")


@dataclass(frozen=True, slots=True)
class TranscriptArtifact:
    path: Path
    text_source: str
    language: str | None
    is_auto: bool = False
    provider: str = ""
    track_id: str = ""
    fallback_chain: tuple[str, ...] = ()

    def metadata(self) -> dict:
        metadata = {
            "version": 1,
            "text_source": self.text_source,
            "source_language": self.language,
            "source_provider": self.provider,
            "fallback_chain": list(self.fallback_chain),
        }
        if self.text_source.startswith("subtitle_"):
            metadata.update(
                {
                    "parser_version": SUBTITLE_PARSER_VERSION,
                    "subtitle_language": self.language,
                    "subtitle_is_auto": self.is_auto,
                    "subtitle_provider": self.provider,
                    "subtitle_track_id": self.track_id,
                }
            )
        return metadata


def normalize_language(language: str) -> str:
    return str(language or "").strip().replace("_", "-").lower()


def _language_rank(language: str, preferred: tuple[str, ...]) -> tuple[int, int]:
    normalized = normalize_language(language)
    for index, wanted in enumerate(preferred):
        target = normalize_language(wanted)
        if normalized == target:
            return index, 0
    for index, wanted in enumerate(preferred):
        target = normalize_language(wanted)
        if normalized.split("-")[0] == target.split("-")[0]:
            return index, 1
    return len(preferred), 0


def rank_subtitle_tracks(
    tracks: list[SubtitleTrack],
    preferred_languages: tuple[str, ...] = SUBTITLE_LANGUAGES,
    *,
    allow_any_language: bool = SUBTITLE_ALLOW_ANY_LANGUAGE,
) -> list[SubtitleTrack]:
    ranked = sorted(
        tracks,
        key=lambda track: (
            _language_rank(track.language, preferred_languages),
            1 if track.is_auto else 0,
            track.language.lower(),
        ),
    )
    if allow_any_language:
        return ranked
    return [
        track
        for track in ranked
        if _language_rank(track.language, preferred_languages)[0] < len(preferred_languages)
    ]


def select_subtitle_track(
    tracks: list[SubtitleTrack],
    preferred_languages: tuple[str, ...] = SUBTITLE_LANGUAGES,
    *,
    allow_any_language: bool = SUBTITLE_ALLOW_ANY_LANGUAGE,
) -> SubtitleTrack | None:
    """Choose the preferred language, then prefer manual over automatic."""
    ranked = rank_subtitle_tracks(
        tracks,
        preferred_languages,
        allow_any_language=allow_any_language,
    )
    return ranked[0] if ranked else None


def _track_entries(collection: dict, *, is_auto: bool) -> list[SubtitleTrack]:
    tracks = []
    for language, raw_entries in (collection or {}).items():
        if normalize_language(language) == "danmaku":
            continue
        entries = raw_entries if isinstance(raw_entries, list) else [raw_entries]
        usable = [
            entry
            for entry in entries
            if isinstance(entry, dict)
            and (entry.get("url") or entry.get("data"))
            and str(entry.get("ext", "")).lower() != "xml"
        ]
        if not usable:
            continue
        usable.sort(key=lambda entry: 0 if str(entry.get("ext", "")).lower() in {"vtt", "srt"} else 1)
        entry = usable[0]
        tracks.append(
            SubtitleTrack(
                language=str(language),
                is_auto=is_auto,
                extension=str(entry.get("ext", "vtt")).lower(),
                url=str(entry.get("url", "")),
                data=str(entry.get("data", "")),
                name=str(entry.get("name", "")),
            )
        )
    return tracks


def _platform_tracks(url: str) -> list[SubtitleTrack]:
    params = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "subtitlesformat": "vtt/srt/best",
    }
    with YoutubeDL(params) as ydl:
        info = ydl.extract_info(url, download=False)
    return _track_entries(info.get("subtitles"), is_auto=False) + _track_entries(
        info.get("automatic_captions"), is_auto=True
    )


def _read_remote_subtitle(track: SubtitleTrack) -> str:
    if track.data:
        return track.data
    params = {"quiet": True, "no_warnings": True, "noplaylist": True}
    with YoutubeDL(params) as ydl:
        response = ydl.urlopen(track.url)
        payload = response.read()
    encoding = "utf-8"
    try:
        header_encoding = response.headers.get_content_charset()
        if header_encoding:
            encoding = header_encoding
    except AttributeError:
        pass
    try:
        return payload.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def _parse_timestamp(value: str) -> float:
    value = value.replace(",", ".")
    parts = value.split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return float(minutes) * 60 + float(seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return float(hours) * 3600 + float(minutes) * 60 + float(seconds)
    raise ValueError(f"Invalid subtitle timestamp: {value}")


def _clean_caption(lines: list[str]) -> str:
    cleaned_lines = []
    for line in lines:
        line = line.strip()
        if line and (not cleaned_lines or line != cleaned_lines[-1]):
            cleaned_lines.append(line)
    text = " ".join(cleaned_lines)
    text = html.unescape(text.replace("\\N", " "))
    text = TAG_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_subtitle_text(text: str) -> list[dict]:
    """Parse SRT or WebVTT into canonical timestamped segments."""
    raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    segments: list[dict] = []
    index = 0
    while index < len(raw_lines):
        line = raw_lines[index].strip()
        if not line or line.upper() == "WEBVTT":
            index += 1
            continue
        if line.upper().startswith(("NOTE", "STYLE", "REGION")):
            index += 1
            while index < len(raw_lines) and raw_lines[index].strip():
                index += 1
            continue
        match = TIMESTAMP_RE.search(line)
        if not match:
            index += 1
            continue

        start = _parse_timestamp(match.group("start"))
        end = _parse_timestamp(match.group("end"))
        index += 1
        caption_lines = []
        while index < len(raw_lines):
            candidate = raw_lines[index]
            if not candidate.strip():
                break
            if TIMESTAMP_RE.search(candidate.strip()):
                break
            caption_lines.append(candidate)
            index += 1
        caption = _clean_caption(caption_lines)
        if end > start and caption:
            candidate = {"start": round(start, 3), "end": round(end, 3), "text": caption}
            if not segments or candidate["text"] != segments[-1]["text"] or candidate["start"] != segments[-1]["start"]:
                segments.append(candidate)
        while index < len(raw_lines) and not raw_lines[index].strip():
            index += 1
    return segments


def _atomic_write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    ) as stream:
        temp_path = Path(stream.name)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(str(temp_path), str(path))
    finally:
        temp_path.unlink(missing_ok=True)


def _safe_filename_part(value: str, fallback: str = "unknown") -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "")).strip("-.")
    return safe or fallback


def _write_canonical_transcript(
    output_path: Path,
    segments: list[dict],
    metadata: dict,
) -> Path:
    if not segments:
        raise ValueError("Subtitle contains no usable timestamped segments")
    lines = [
        f"# Language: {metadata.get('source_language') or 'unknown'}",
        f"# Text source: {metadata['text_source']}",
        "",
    ]
    lines.extend(
        f"[{segment['start']:.1f}s -> {segment['end']:.1f}s] {segment['text']}"
        for segment in segments
    )
    _atomic_write(output_path, "\n".join(lines) + "\n")
    metadata = dict(metadata)
    metadata["transcript_sha256"] = hashlib.sha256(output_path.read_bytes()).hexdigest()
    _atomic_write(
        output_path.with_suffix(".meta.json"),
        json.dumps(metadata, ensure_ascii=False, indent=2),
    )
    return output_path


def get_cached_subtitle_transcript(
    video_id: str,
    preferred_languages: tuple[str, ...] = SUBTITLE_LANGUAGES,
) -> TranscriptArtifact | None:
    """Return a validated canonical subtitle transcript of any supported kind."""
    output_path = TEMP_DIR / f"{video_id}_norm.txt"
    metadata_path = output_path.with_suffix(".meta.json")
    if not output_path.exists() or not metadata_path.exists():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    text_source = metadata.get("text_source")
    if text_source not in {"subtitle_manual", "subtitle_auto", "subtitle_embedded"}:
        return None
    if metadata.get("parser_version") != SUBTITLE_PARSER_VERSION:
        return None
    if tuple(metadata.get("preferred_languages", [])) != tuple(preferred_languages):
        return None
    expected_digest = metadata.get("transcript_sha256")
    if not expected_digest or hashlib.sha256(output_path.read_bytes()).hexdigest() != expected_digest:
        return None
    language = str(metadata.get("source_language") or "")
    if (
        not SUBTITLE_ALLOW_ANY_LANGUAGE
        and _language_rank(language, preferred_languages)[0] >= len(preferred_languages)
    ):
        return None
    return TranscriptArtifact(
        path=output_path,
        text_source=str(text_source),
        language=language or None,
        is_auto=bool(metadata.get("subtitle_is_auto", False)),
        provider=str(metadata.get("subtitle_provider", "platform")),
        track_id=str(metadata.get("subtitle_track_id", "")),
        fallback_chain=tuple(metadata.get("fallback_chain", [])),
    )


def _cached_platform_artifact(video_id: str, preferred_languages: tuple[str, ...]) -> TranscriptArtifact | None:
    artifact = get_cached_subtitle_transcript(video_id, preferred_languages)
    if artifact and artifact.provider == "platform":
        return artifact
    return None


def get_platform_transcript(
    url: str,
    video_id: str,
    *,
    force: bool = False,
    preferred_languages: tuple[str, ...] = SUBTITLE_LANGUAGES,
) -> TranscriptArtifact | None:
    """Resolve a platform subtitle without downloading audio or video."""
    if not SUBTITLE_ENABLED:
        return None
    if not force:
        cached = _cached_platform_artifact(video_id, preferred_languages)
        if cached:
            logger.info("[subtitle] Reusing cached platform subtitle: %s", cached.path)
            return cached
    try:
        tracks = _platform_tracks(url)
        ranked_tracks = rank_subtitle_tracks(tracks, preferred_languages)
        if not ranked_tracks:
            logger.info("[subtitle] No preferred platform subtitle found: %s", url)
            return None
        failures = []
        for track in ranked_tracks:
            try:
                raw_text = _read_remote_subtitle(track)
                segments = parse_subtitle_text(raw_text)
                if not segments:
                    raise ValueError("no usable timestamped segments")
                output_path = TEMP_DIR / f"{video_id}_norm.txt"
                raw_name = (
                    f"{_safe_filename_part(video_id)}__{track.text_source}__"
                    f"{_safe_filename_part(normalize_language(track.language))}."
                    f"{_safe_filename_part(track.extension, 'vtt')}"
                )
                raw_path = TEMP_DIR / "subtitles" / raw_name
                _atomic_write(raw_path, raw_text)
                artifact = TranscriptArtifact(
                    path=output_path,
                    text_source=track.text_source,
                    language=track.language,
                    is_auto=track.is_auto,
                    provider="platform",
                    track_id=track.track_id,
                    fallback_chain=(f"platform:{track.text_source}",),
                )
                metadata = artifact.metadata()
                metadata.update({
                    "video_id": video_id,
                    "raw_subtitle_path": str(raw_path),
                    "raw_subtitle_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
                    "preferred_languages": list(preferred_languages),
                })
                _write_canonical_transcript(output_path, segments, metadata)
                logger.info(
                    "[subtitle] Selected %s subtitle (%s): %s",
                    track.language,
                    track.text_source,
                    output_path,
                )
                return artifact
            except Exception as exc:
                failures.append(f"{track.track_id}: {exc}")
                logger.warning("[subtitle] Rejected track %s: %s", track.track_id, exc)
        logger.warning("[subtitle] All platform subtitle tracks were unusable: %s", "; ".join(failures))
        return None
    except Exception as exc:
        logger.warning("[subtitle] Platform subtitle lookup failed, falling back: %s", exc)
        return None


def _embedded_track(media_path: Path, preferred_languages: tuple[str, ...]) -> dict | None:
    command = [
        str(FFPROBE_BIN), "-v", "error", "-select_streams", "s",
        "-show_entries", "stream=index,codec_name:stream_tags=language,title",
        "-of", "json", str(media_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        return None
    payload = json.loads(result.stdout or "{}")
    text_codecs = {"ass", "ssa", "subrip", "srt", "text", "mov_text", "webvtt"}
    streams = [
        stream
        for stream in (payload.get("streams") or [])
        if str(stream.get("codec_name", "")).lower() in text_codecs
    ]
    ranked = sorted(
        streams,
        key=lambda stream: _language_rank(
            str((stream.get("tags") or {}).get("language", "")), preferred_languages
        ),
    )
    if not ranked:
        return None
    selected = ranked[0]
    language = str((selected.get("tags") or {}).get("language", "")) or None
    if not SUBTITLE_ALLOW_ANY_LANGUAGE:
        if language is None or _language_rank(language, preferred_languages)[0] >= len(preferred_languages):
            return None
    return {**selected, "language": language}


def _media_fingerprint(media_path: Path) -> dict[str, str | int]:
    """Return a cheap identity for cache validation without hashing large media files."""
    stat = media_path.stat()
    return {
        "media_path": str(media_path.resolve()),
        "media_size": stat.st_size,
        "media_mtime_ns": stat.st_mtime_ns,
    }


def get_embedded_transcript(
    media_path: str | Path | None,
    video_id: str,
    *,
    force: bool = False,
    preferred_languages: tuple[str, ...] = SUBTITLE_LANGUAGES,
) -> TranscriptArtifact | None:
    """Extract a text subtitle stream from an already available media file."""
    if not media_path or not SUBTITLE_ENABLED:
        return None
    media = Path(media_path)
    if not media.exists():
        return None
    try:
        media_fingerprint = _media_fingerprint(media)
    except OSError:
        return None
    output_path = TEMP_DIR / f"{video_id}_norm.txt"
    if not force and output_path.exists():
        metadata_path = output_path.with_suffix(".meta.json")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            cached_fingerprint = {
                key: metadata.get(key)
                for key in ("media_path", "media_size", "media_mtime_ns")
            }
            if (
                metadata.get("text_source") == "subtitle_embedded"
                and cached_fingerprint == media_fingerprint
            ):
                return TranscriptArtifact(
                    path=output_path,
                    text_source="subtitle_embedded",
                    language=metadata.get("source_language"),
                    provider="embedded",
                    track_id=str(metadata.get("subtitle_track_id", "")),
                    fallback_chain=tuple(metadata.get("fallback_chain", [])),
                )
        except (OSError, json.JSONDecodeError):
            pass
    try:
        stream = _embedded_track(media, preferred_languages)
        if not stream:
            return None
        stream_index = int(stream["index"])
        language = stream.get("language")
        raw_path = TEMP_DIR / "subtitles" / f"{video_id}__embedded__{stream_index}.srt"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            str(FFMPEG_BIN), "-y", "-i", str(media), "-map", f"0:{stream_index}",
            "-c:s", "srt", str(raw_path),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            return None
        raw_text = raw_path.read_text(encoding="utf-8-sig", errors="replace")
        artifact = TranscriptArtifact(
            path=output_path,
            text_source="subtitle_embedded",
            language=language,
            provider="embedded",
            track_id=f"embedded:{stream_index}",
            fallback_chain=("embedded:subtitle_embedded",),
        )
        metadata = artifact.metadata()
        metadata.update({
            "video_id": video_id,
            "raw_subtitle_path": str(raw_path),
            "raw_subtitle_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
            "preferred_languages": list(preferred_languages),
        })
        metadata.update(media_fingerprint)
        _write_canonical_transcript(output_path, parse_subtitle_text(raw_text), metadata)
        return artifact
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        logger.warning("[subtitle] Embedded subtitle extraction failed: %s", exc)
        return None


def load_transcript_metadata(transcript_path: str | Path) -> dict:
    path = Path(transcript_path).with_suffix(".meta.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}
