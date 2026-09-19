"""Load domain-specific ASR hotwords without promoting error aliases."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import yaml

from config import ASR_HOTWORDS_MAX, PROMPT_DIR
from modules.asr.types import normalize_backend_name

GLOSSARY_DIR = PROMPT_DIR / "glossaries"


def normalize_domains(domains: str | Iterable[str] | None) -> tuple[str, ...]:
    if domains is None:
        return ()
    values = domains.split(",") if isinstance(domains, str) else domains
    normalized: list[str] = []
    for value in values:
        name = str(value).strip().lower()
        if name and name not in normalized:
            normalized.append(name)
    return tuple(normalized)


def _normalize_entry(raw: dict[str, Any], default_domain: str) -> dict[str, Any] | None:
    canonical = str(raw.get("canonical") or raw.get("candidate") or "").strip()
    if not canonical:
        return None
    aliases = raw.get("aliases", raw.get("variants", []))
    if not isinstance(aliases, list):
        aliases = []
    domains = normalize_domains(raw.get("domains") or [default_domain])
    try:
        weight = int(raw.get("hotword_weight", 20))
    except (TypeError, ValueError):
        weight = 20
    return {
        "canonical": canonical,
        "aliases": [str(item).strip() for item in aliases if str(item).strip()],
        "hotword_weight": max(1, min(weight, 100)),
        "domains": list(domains),
        "context": [str(item).strip() for item in raw.get("context", []) if str(item).strip()],
    }


def load_domain_entries(
    domains: str | Iterable[str] | None,
    *,
    glossary_dir: Path | None = None,
    limit: int = ASR_HOTWORDS_MAX,
) -> list[dict[str, Any]]:
    selected = normalize_domains(domains)
    if not selected:
        return []
    root = Path(glossary_dir or GLOSSARY_DIR)
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for domain in selected:
        path = root / f"{domain}.yaml"
        if not path.exists():
            raise ValueError(f"Unknown ASR domain or missing glossary: {domain}")
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict) or not isinstance(raw.get("entries", []), list):
            raise ValueError(f"Invalid ASR glossary: {path}")
        for item in raw["entries"]:
            if not isinstance(item, dict):
                continue
            entry = _normalize_entry(item, domain)
            if entry is None or entry["canonical"] in seen:
                continue
            entries.append(entry)
            seen.add(entry["canonical"])
            if len(entries) >= max(0, limit):
                return entries
    return entries


def build_backend_hotwords(entries: list[dict[str, Any]], backend: str) -> str:
    if not entries:
        return ""
    backend_name = normalize_backend_name(backend)
    if backend_name == "paraformer":
        from modules.asr.paraformer_backend import build_funasr_hotword

        return build_funasr_hotword(entries, limit=len(entries))
    return "，".join(entry["canonical"] for entry in entries)


def hotword_fingerprint(domains: str | Iterable[str] | None, entries: list[dict[str, Any]]) -> str:
    payload = {
        "domains": normalize_domains(domains),
        "entries": entries,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()