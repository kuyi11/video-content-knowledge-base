"""Add reversible, context-gated ASR terminology hints."""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import yaml

from config import PROMPT_DIR


GLOSSARY_DIR = PROMPT_DIR / "glossaries"
DEFAULT_GLOSSARY_PATH = GLOSSARY_DIR / "medical.yaml"
_CONTEXT_CHARS = 120


@lru_cache(maxsize=16)
def _load_glossary(path_str: str) -> dict:
    path = Path(path_str)
    if not path.exists():
        return {"version": "none", "entries": []}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict) or not isinstance(raw.get("entries", []), list):
        raise ValueError(f"Invalid transcript glossary: {path}")
    return raw


def _normalize_domains(domains: str | Iterable[str] | None) -> tuple[str, ...]:
    if domains is None:
        return ("medical",)
    values = domains.split(",") if isinstance(domains, str) else domains
    result: list[str] = []
    for value in values:
        name = str(value).strip().lower()
        if name and name not in result:
            result.append(name)
    return tuple(result)


def _glossary_paths(
    glossary_path: Path | None,
    domains: str | Iterable[str] | None,
) -> list[Path]:
    if glossary_path is not None:
        return [Path(glossary_path)]
    return [GLOSSARY_DIR / f"{domain}.yaml" for domain in _normalize_domains(domains)]


def _entries(glossary_path: Path | None, domains: str | Iterable[str] | None) -> list[dict]:
    result: list[dict] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for path in _glossary_paths(glossary_path, domains):
        glossary = _load_glossary(str(path.resolve()))
        for raw in glossary.get("entries", []):
            if not isinstance(raw, dict):
                continue
            canonical = str(raw.get("canonical") or raw.get("candidate") or "").strip()
            candidate = str(raw.get("candidate") or canonical).strip()
            aliases = raw.get("aliases", raw.get("variants", []))
            contexts = raw.get("context", [])
            if not canonical or not candidate or not isinstance(aliases, list):
                continue
            if not isinstance(contexts, list):
                contexts = []
            aliases = [str(item).strip() for item in aliases if str(item).strip()]
            contexts = [str(item).strip() for item in contexts if str(item).strip()]
            key = (canonical, tuple(aliases))
            if key in seen:
                continue
            seen.add(key)
            result.append(
                {
                    "canonical": canonical,
                    "candidate": candidate,
                    "aliases": aliases,
                    "context": contexts,
                    "note": str(raw.get("note", "")).strip(),
                }
            )
    return result


def _matching_contexts(
    text: str,
    start: int,
    end: int,
    contexts: list[str],
) -> list[str]:
    if not contexts:
        return []
    left = max(0, start - _CONTEXT_CHARS)
    right = min(len(text), end + _CONTEXT_CHARS)
    context = text[left:start] + text[end:right]
    return [term for term in contexts if term and term in context]


def _timestamp_bounds(text: str, position: int) -> tuple[float, float] | None:
    line_start = text.rfind("\n", 0, position) + 1
    line_end = text.find("\n", position)
    if line_end < 0:
        line_end = len(text)
    match = re.match(
        r"^\[(\d+(?:\.\d+)?)s\s*->\s*(\d+(?:\.\d+)?)s\]",
        text[line_start:line_end],
    )
    if not match:
        return None
    return float(match.group(1)), float(match.group(2))

def analyze_asr_terms(
    text: str,
    glossary_path: Path | None = None,
    *,
    domains: str | Iterable[str] | None = None,
) -> tuple[str, list[dict]]:
    corrections: list[dict] = []
    replacements: list[tuple[int, int, str]] = []
    occupied: list[tuple[int, int]] = []

    for entry in _entries(glossary_path, domains):
        for variant in entry["aliases"]:
            search_from = 0
            while True:
                start = text.find(variant, search_from)
                if start < 0:
                    break
                end = start + len(variant)
                search_from = end
                matched_contexts = _matching_contexts(
                    text, start, end, entry["context"]
                )
                context_required = bool(entry["context"])
                grounded = bool(matched_contexts) or not context_required
                timestamp_bounds = _timestamp_bounds(text, start)
                record = {
                    "start_char": start,
                    "end_char": end,
                    "segment_start": timestamp_bounds[0] if timestamp_bounds else None,
                    "segment_end": timestamp_bounds[1] if timestamp_bounds else None,
                    "original": variant,
                    "canonical": entry["canonical"],
                    "candidate": entry["candidate"],
                    "matched_contexts": matched_contexts,
                    "confidence": "medium" if grounded else "low",
                    "action": "annotated" if grounded else "retained",
                    "review_needed": grounded,
                }
                corrections.append(record)
                if not grounded or any(start < used_end and end > used_start for used_start, used_end in occupied):
                    continue
                note = entry["note"]
                suffix = f"；{note}" if note else ""
                annotation = f"{variant}〔疑似术语：{entry['candidate']}{suffix}〕"
                replacements.append((start, end, annotation))
                occupied.append((start, end))

    annotated = text
    for start, end, annotation in sorted(replacements, reverse=True):
        annotated = annotated[:start] + annotation + annotated[end:]
    corrections.sort(key=lambda item: (item["start_char"], item["end_char"], item["canonical"]))
    return annotated, corrections


def annotate_asr_terms(
    text: str,
    glossary_path: Path | None = None,
    *,
    domains: str | Iterable[str] | None = None,
) -> str:
    annotated, _ = analyze_asr_terms(
        text,
        glossary_path,
        domains=domains,
    )
    return annotated

def glossary_fingerprint(
    glossary_path: Path | None = None,
    *,
    domains: str | Iterable[str] | None = None,
) -> str:
    paths = _glossary_paths(glossary_path, domains)
    payload = []
    for path in paths:
        if path.exists():
            payload.append({"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        else:
            payload.append({"path": str(path.resolve()), "sha256": "missing"})
    if not payload:
        return "none"
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()