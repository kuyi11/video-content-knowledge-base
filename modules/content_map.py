"""Grounded, cacheable map stage for long-form transcript structuring."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import ollama

from config import (
    LLM_CONFIG,
    LLM_MAP_MAX_UNITS,
    LLM_MAP_TEMPERATURE,
    LLM_WINDOW_MAX_CHARS,
    LLM_WINDOW_OVERLAP_SECONDS,
    LLM_WINDOW_SECONDS,
    PROMPT_DIR,
    SOURCE_BLOCK_MAX_CHARS,
    SOURCE_BLOCK_MAX_GAP,
    SOURCE_BLOCK_MAX_SECONDS,
    STRUCTURE_CACHE_DIR,
)
from modules.file_lock import FileLock
from modules.asr.glossary import normalize_domains
from modules.transcript_parser import (
    TranscriptWindow,
    build_source_blocks,
    build_transcript_windows,
    parse_transcript_lines,
    render_transcript_window,
)
from modules.transcript_normalizer import annotate_asr_terms, glossary_fingerprint

logger = logging.getLogger(__name__)

CONTENT_MAP_VERSION = "content-map-v1"
CONTENT_MAP_PROMPT_VERSION = "content-map-2026-07-coverage3"
CONTENT_MAP_SCHEMA_PATH = PROMPT_DIR / "schemas" / "content-map-v1.json"
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

_ollama_client = ollama.Client(host=LLM_CONFIG["host"])

MAP_SYSTEM_PROMPT = """你是视频转写事实提取器。你只提取当前窗口明确表达的内容，不做最终总结。
规则：
1. 找出 PRIMARY 来源中的所有独立案例、主题、过程或观点，不能只提取窗口末尾内容。
2. CONTEXT_ONLY 只用于理解衔接；每个输出单元必须至少引用一个 PRIMARY 来源。
3. 只能使用输入中存在的 Sxxxx 来源编号，不允许写 URL、视频编号或外部资料。
4. 原文没有明确支持的内容不得补充；转写疑似错误时写入 uncertainties。
   输入中的“疑似术语”注释只是候选提示，不能直接当成确定事实。
5. recommendations 只记录视频明确给出的建议，不得把点赞、关注、投币或分享作为知识建议。
6. 如果窗口只有片头、片尾、广告或无有效内容，units 必须为空并说明 non_content_reason。
7. 输出必须严格符合 JSON Schema。"""


class ContentMapValidationError(ValueError):
    pass


def normalize_window_map(data: dict) -> dict:
    """Repair a contradictory classifier choice without changing its facts."""
    normalized = copy.deepcopy(data)
    if not isinstance(normalized, dict):
        return normalized
    if normalized.get("units"):
        repaired_units = []
        for index, raw_unit in enumerate(normalized.get("units", []), 1):
            if not isinstance(raw_unit, dict):
                continue
            unit = copy.deepcopy(raw_unit)
            title = str(unit.get("title", "")).strip()
            summary = str(unit.get("summary", "")).strip()
            key_points = unit.get("key_points")
            if not isinstance(key_points, list):
                key_points = []
            key_points = [str(point).strip() for point in key_points if str(point).strip()]
            if not title:
                title = summary or (key_points[0] if key_points else "")
            if not summary:
                summary = title
            if not title or not summary:
                logger.warning("Dropping empty content unit %d from window map", index)
                continue
            unit["title"] = title
            unit["summary"] = summary
            unit["unit_type"] = str(unit.get("unit_type", "other")).strip() or "other"
            unit["importance"] = (
                unit.get("importance")
                if unit.get("importance") in {"major", "supporting", "minor"}
                else "supporting"
            )
            unit["key_points"] = key_points
            for field in ["recommendations", "uncertainties"]:
                values = unit.get(field)
                unit[field] = (
                    [str(value).strip() for value in values if str(value).strip()]
                    if isinstance(values, list)
                    else []
                )
            repaired_units.append(unit)
        normalized["units"] = repaired_units
    if normalized.get("units"):
        normalized["content_type"] = "substantive"
        normalized["non_content_reason"] = ""
    else:
        reason = str(normalized.get("non_content_reason", "")).strip()
        if "广告" in reason:
            content_type = "advertisement"
        elif any(keyword in reason for keyword in ["片头", "片尾", "开场", "结尾"]):
            content_type = "intro_outro"
        else:
            content_type = "empty"
        normalized["content_type"] = content_type
    return normalized


def bind_content_map_schema(
    allowed_source_refs: list[str] | tuple[str, ...],
    *,
    max_units: int = LLM_MAP_MAX_UNITS,
) -> dict[str, Any]:
    """Bind runtime source IDs and limits to the stable map-stage contract."""
    schema = json.loads(CONTENT_MAP_SCHEMA_PATH.read_text(encoding="utf-8"))
    unit_array = schema["properties"]["units"]
    unit_array["maxItems"] = max(1, int(max_units))
    source_items = unit_array["items"]["properties"]["source_refs"]["items"]
    source_items["enum"] = list(allowed_source_refs)
    return schema


def _ensure_string_list(value: Any, label: str) -> None:
    if not isinstance(value, list):
        raise ContentMapValidationError(f"{label} must be list")
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ContentMapValidationError(f"{label} must contain non-empty strings")


def validate_window_map(data: dict, window: TranscriptWindow) -> None:
    if not isinstance(data, dict):
        raise ContentMapValidationError("window map must be object")
    expected_root = {"content_type", "units", "non_content_reason"}
    if set(data) != expected_root:
        raise ContentMapValidationError("window map fields do not match schema")

    content_type = data["content_type"]
    if content_type not in {"substantive", "intro_outro", "advertisement", "empty"}:
        raise ContentMapValidationError(f"invalid content_type: {content_type}")
    if not isinstance(data["units"], list):
        raise ContentMapValidationError("units must be list")
    if not isinstance(data["non_content_reason"], str):
        raise ContentMapValidationError("non_content_reason must be str")
    if content_type == "substantive" and not data["units"]:
        raise ContentMapValidationError("substantive window must contain units")
    if content_type != "substantive" and data["units"]:
        raise ContentMapValidationError("non-substantive window cannot contain units")
    if content_type != "substantive" and not data["non_content_reason"].strip():
        raise ContentMapValidationError("non-substantive window requires a reason")
    if len(data["units"]) > LLM_MAP_MAX_UNITS:
        raise ContentMapValidationError("too many units in window map")

    allowed_refs = set(window.allowed_ref_ids)
    primary_refs = set(window.primary_ref_ids)
    expected_unit_fields = {
        "title",
        "summary",
        "unit_type",
        "importance",
        "key_points",
        "recommendations",
        "uncertainties",
        "source_refs",
    }
    for index, unit in enumerate(data["units"]):
        label = f"units[{index}]"
        if not isinstance(unit, dict) or set(unit) != expected_unit_fields:
            raise ContentMapValidationError(f"{label} fields do not match schema")
        for field in ["title", "summary", "unit_type"]:
            if not isinstance(unit[field], str) or not unit[field].strip():
                raise ContentMapValidationError(f"{label}.{field} cannot be empty")
        if unit["importance"] not in {"major", "supporting", "minor"}:
            raise ContentMapValidationError(f"{label}.importance is invalid")
        for field in ["key_points", "recommendations", "uncertainties", "source_refs"]:
            _ensure_string_list(unit[field], f"{label}.{field}")
        refs = set(unit["source_refs"])
        if not refs:
            raise ContentMapValidationError(f"{label}.source_refs cannot be empty")
        if not refs <= allowed_refs:
            raise ContentMapValidationError(f"{label} contains unknown source_refs")
        if not refs & primary_refs:
            raise ContentMapValidationError(f"{label} only cites context-only sources")


def _chat_options(temperature: float) -> dict[str, Any]:
    return {
        "temperature": temperature,
        "num_ctx": LLM_CONFIG["num_ctx"],
        "num_predict": LLM_CONFIG["num_predict"],
    }


def _extract_window(
    window: TranscriptWindow,
    *,
    client,
    max_retries: int,
    domains: tuple[str, ...],
) -> dict:
    schema = bind_content_map_schema(window.primary_ref_ids)
    rendered_window = annotate_asr_terms(render_transcript_window(window), domains=domains)
    user_prompt = (
        f"窗口：{window.window_id}\n"
        f"必须覆盖的 PRIMARY 来源：{', '.join(window.primary_ref_ids)}\n\n"
        f"转写窗口：\n{rendered_window}"
    )
    messages = [
        {"role": "system", "content": MAP_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    last_content = ""
    last_error: Exception | None = None

    for attempt in range(max_retries):
        request_messages = list(messages)
        if attempt and last_content:
            request_messages.extend(
                [
                    {"role": "assistant", "content": last_content},
                    {
                        "role": "user",
                        "content": f"上一次输出校验失败：{last_error}。只修正 JSON，不要增加原文没有的事实。",
                    },
                ]
            )
        response = client.chat(
            model=LLM_CONFIG["model"],
            messages=request_messages,
            format=schema,
            options=_chat_options(LLM_MAP_TEMPERATURE),
            keep_alive=LLM_CONFIG["keep_alive"],
        )
        last_content = response["message"]["content"].strip()
        try:
            result = normalize_window_map(json.loads(last_content))
            validate_window_map(result, window)
            return result
        except (json.JSONDecodeError, ContentMapValidationError, KeyError, TypeError) as exc:
            last_error = exc
            logger.warning(
                "Content map %s attempt %d/%d failed: %s",
                window.window_id,
                attempt + 1,
                max_retries,
                exc,
            )

    raise RuntimeError(
        f"Failed to extract {window.window_id} after {max_retries} attempts: {last_error}"
    )


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp_path), str(path))


def _safe_cache_key(source_id: str | None, transcript_hash: str) -> str:
    if source_id:
        cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", source_id)
        if cleaned:
            return cleaned
    return f"transcript-{transcript_hash[:16]}"


def _cache_manifest(transcript_hash: str, domains: tuple[str, ...]) -> dict:
    return {
        "content_map_version": CONTENT_MAP_VERSION,
        "prompt_version": CONTENT_MAP_PROMPT_VERSION,
        "schema_fingerprint": hashlib.sha256(CONTENT_MAP_SCHEMA_PATH.read_bytes()).hexdigest(),
        "transcript_hash": transcript_hash,
        "model": LLM_CONFIG["model"],
        "num_ctx": LLM_CONFIG["num_ctx"],
        "window_seconds": LLM_WINDOW_SECONDS,
        "window_max_chars": LLM_WINDOW_MAX_CHARS,
        "window_overlap_seconds": LLM_WINDOW_OVERLAP_SECONDS,
        "source_block_max_chars": SOURCE_BLOCK_MAX_CHARS,
        "source_block_max_seconds": SOURCE_BLOCK_MAX_SECONDS,
        "source_block_max_gap": SOURCE_BLOCK_MAX_GAP,
        "domains": list(domains),
        "glossary_fingerprint": glossary_fingerprint(domains=domains),
    }


def _load_matching_cache(cache_path: Path, expected_manifest: dict) -> dict | None:
    manifest_path = cache_path / "manifest.json"
    map_path = cache_path / "content_map.json"
    if not manifest_path.exists() or not map_path.exists():
        return None
    try:
        actual_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        content_map = json.loads(map_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if actual_manifest != expected_manifest:
        return None
    if (
        not isinstance(content_map, dict)
        or content_map.get("version") != CONTENT_MAP_VERSION
        or not isinstance(content_map.get("units"), list)
        or not isinstance(content_map.get("sources"), dict)
    ):
        return None
    content_map["_cache_path"] = str(cache_path)
    return content_map


def load_cached_artifact(
    content_map: dict,
    name: str,
    expected_manifest: dict,
) -> dict | None:
    """Load a versioned sidecar produced from a cached content map."""
    cache_path = content_map.get("_cache_path")
    if not cache_path or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        return None
    artifact_path = Path(cache_path) / f"{name}.json"
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("manifest") != expected_manifest:
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else None


def write_cached_artifact(
    content_map: dict,
    name: str,
    manifest: dict,
    data: dict,
) -> None:
    """Atomically persist a versioned sidecar next to the content map cache."""
    cache_path = content_map.get("_cache_path")
    if not cache_path:
        return
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise ValueError(f"Invalid cache artifact name: {name}")
    _atomic_write_json(
        Path(cache_path) / f"{name}.json",
        {"manifest": manifest, "data": data},
    )


def _source_catalog(blocks) -> dict[str, dict]:
    return {
        block.ref_id: {
            "start": block.start,
            "end": block.end,
            "has_timestamp": block.has_timestamp,
            "text": block.text,
        }
        for block in blocks
    }


def _attach_unit_metadata(window: TranscriptWindow, window_map: dict, sources: dict) -> list[dict]:
    units = []
    for index, raw_unit in enumerate(window_map["units"], 1):
        unit = copy.deepcopy(raw_unit)
        refs = [sources[ref] for ref in unit["source_refs"]]
        unit.update(
            {
                "unit_id": f"{window.window_id}-U{index:02d}",
                "window_id": window.window_id,
                "start": min(ref["start"] for ref in refs),
                "end": max(ref["end"] for ref in refs),
            }
        )
        units.append(unit)
    return units


def extract_content_map(
    transcript_text: str,
    *,
    source_id: str | None = None,
    domains: str | list[str] | tuple[str, ...] | None = None,
    cache_dir: Path | None = None,
    force: bool = False,
    max_retries: int = 3,
    client=None,
) -> dict:
    transcript_hash = hashlib.sha256(transcript_text.encode("utf-8")).hexdigest()
    selected_domains = ("medical",) if domains is None else normalize_domains(domains)
    cache_root = Path(cache_dir) if cache_dir is not None else STRUCTURE_CACHE_DIR
    cache_path = cache_root / _safe_cache_key(source_id, transcript_hash) / transcript_hash[:16]
    manifest = _cache_manifest(transcript_hash, selected_domains)
    active_client = client or _ollama_client

    with FileLock(cache_path / ".content_map.lock"):
        manifest_path = cache_path / "manifest.json"
        existing_manifest = None
        if manifest_path.exists():
            try:
                existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing_manifest = None
        reuse_window_cache = not force and existing_manifest == manifest
        if not force:
            cached = _load_matching_cache(cache_path, manifest)
            if cached is not None:
                logger.info("[ContentMap] cache hit: %s", cache_path)
                return cached
        if not reuse_window_cache:
            _atomic_write_json(manifest_path, manifest)

        lines = parse_transcript_lines(transcript_text)
        blocks = build_source_blocks(
            lines,
            max_chars=SOURCE_BLOCK_MAX_CHARS,
            max_seconds=SOURCE_BLOCK_MAX_SECONDS,
            max_gap=SOURCE_BLOCK_MAX_GAP,
        )
        if not blocks:
            raise ValueError("Transcript contains no usable text")
        windows = build_transcript_windows(
            blocks,
            target_seconds=LLM_WINDOW_SECONDS,
            max_chars=LLM_WINDOW_MAX_CHARS,
            overlap_seconds=LLM_WINDOW_OVERLAP_SECONDS,
        )
        sources = _source_catalog(blocks)
        all_units = []
        window_results = []
        window_dir = cache_path / "windows"

        logger.info(
            "[ContentMap] %d chars -> %d source blocks -> %d windows",
            len(transcript_text),
            len(blocks),
            len(windows),
        )
        for window in windows:
            window_path = window_dir / f"{window.window_id}.json"
            window_map = None
            if reuse_window_cache and window_path.exists():
                try:
                    candidate = normalize_window_map(
                        json.loads(window_path.read_text(encoding="utf-8"))
                    )
                    validate_window_map(candidate, window)
                    window_map = candidate
                except (OSError, json.JSONDecodeError, ContentMapValidationError):
                    window_map = None
            if window_map is None:
                window_map = _extract_window(
                    window,
                    client=active_client,
                    max_retries=max_retries,
                    domains=selected_domains,
                )
                _atomic_write_json(window_path, window_map)

            units = _attach_unit_metadata(window, window_map, sources)
            all_units.extend(units)
            window_results.append(
                {
                    "window_id": window.window_id,
                    "start": window.start,
                    "end": window.end,
                    "primary_source_refs": list(window.primary_ref_ids),
                    "content_type": window_map["content_type"],
                    "non_content_reason": window_map["non_content_reason"],
                    "unit_ids": [unit["unit_id"] for unit in units],
                }
            )

        if not all_units:
            raise RuntimeError("Content map contains no substantive units")
        content_map = {
            "version": CONTENT_MAP_VERSION,
            "transcript_hash": transcript_hash,
            "source_id": source_id or "",
            "domains": list(selected_domains),
            "duration": max((block.end for block in blocks), default=0.0),
            "sources": sources,
            "windows": window_results,
            "units": all_units,
        }
        _atomic_write_json(cache_path / "content_map.json", content_map)
        _atomic_write_json(manifest_path, manifest)
        content_map["_cache_path"] = str(cache_path)
        return content_map


def _unique_strings(values) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def window_digests(content_map: dict) -> list[dict]:
    units_by_window: dict[str, list[dict]] = {}
    for unit in content_map.get("units", []):
        units_by_window.setdefault(unit["window_id"], []).append(unit)

    digests = []
    for window in content_map.get("windows", []):
        if window.get("content_type") != "substantive":
            continue
        units = units_by_window.get(window["window_id"], [])
        if not units:
            continue
        digests.append(
            {
                "window_id": window["window_id"],
                "start": window["start"],
                "end": window["end"],
                "unit_ids": [unit["unit_id"] for unit in units],
                "titles": _unique_strings(unit["title"] for unit in units),
                "summaries": _unique_strings(unit["summary"] for unit in units),
                "key_points": _unique_strings(
                    point for unit in units for point in unit.get("key_points", [])
                ),
                "recommendations": _unique_strings(
                    item for unit in units for item in unit.get("recommendations", [])
                ),
                "uncertainties": _unique_strings(
                    item for unit in units for item in unit.get("uncertainties", [])
                ),
                "source_refs": _unique_strings(
                    ref for unit in units for ref in unit.get("source_refs", [])
                ),
            }
        )
    return digests


def content_map_for_synthesis(content_map: dict) -> dict:
    return {
        "version": content_map["version"],
        "duration": content_map.get("duration", 0.0),
        "window_digests": window_digests(content_map),
    }


def required_unit_ids(content_map: dict) -> list[str]:
    units_by_window: dict[str, list[dict]] = {}
    for unit in content_map.get("units", []):
        units_by_window.setdefault(unit["window_id"], []).append(unit)

    required = []
    for window in content_map.get("windows", []):
        if window.get("content_type") != "substantive":
            continue
        units = units_by_window.get(window["window_id"], [])
        major = [unit for unit in units if unit.get("importance") == "major"]
        selected = (major or units)[:1]
        required.extend(unit["unit_id"] for unit in selected)
    return list(dict.fromkeys(required))


def find_external_references(value: Any) -> list[str]:
    found = []
    if isinstance(value, dict):
        for nested in value.values():
            found.extend(find_external_references(nested))
    elif isinstance(value, list):
        for nested in value:
            found.extend(find_external_references(nested))
    elif isinstance(value, str):
        found.extend(URL_RE.findall(value))
    return found
