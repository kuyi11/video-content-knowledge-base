import copy
import hashlib
import json
import logging
import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any

import ollama
import yaml

from config import LLM_CONFIG, LLM_DIRECT_MAX_CHARS
from modules.content_map import (
    content_map_for_synthesis,
    extract_content_map,
    find_external_references,
    load_cached_artifact,
    required_unit_ids,
    window_digests,
    write_cached_artifact,
)
from modules.prompt_profiles import (
    DEFAULT_PROFILE_NAME,
    DEFAULT_SCHEMA_VERSION,
    PromptProfile,
    load_prompt_profile,
    load_structure_schema,
)
from modules.document_identity import extract_video_id, make_document_id
from modules.transcript_parser import format_time

logger = logging.getLogger(__name__)
MODEL = LLM_CONFIG["model"]
_ollama_client = ollama.Client(host=LLM_CONFIG["host"])

# Backward-compatible alias used by older code/tests.
JSON_SCHEMA = load_structure_schema(DEFAULT_SCHEMA_VERSION)

V1_FIELDS = {
    "title",
    "summary",
    "topics",
    "key_points",
    "insights",
    "actions",
    "tags",
}

V2_FIELDS = V1_FIELDS | {
    "knowledge_items",
    "questions",
    "pros_cons",
    "evidence",
    "risks",
}


class SchemaValidationError(Exception):
    pass


SYNTHESIS_SYSTEM_PROMPT = """你现在根据已经逐段提取并带来源编号的完整内容地图生成最终结构化结果。
规则：
1. title、summary、topics 和 key_points 必须忠实采用输入中的“共享全局主题概览”，不能只关注视频末尾。
2. 每个 substantive 窗口至少要有一个内容单元体现在最终结果中。
3. 只能使用内容地图里的事实，不得添加外部知识、URL、其他视频编号或虚构来源。
4. v2 结果中的每个知识点、问题、优缺点、证据和风险都必须引用真实 Sxxxx 来源。
5. confidence 只能填写“高”“中”“低”；不确定的转写术语不得解释成确定事实。
6. 原文不支持的 questions、pros_cons、evidence 或 risks 应返回空数组，不要为了填满栏目而编造。
7. actions 只保留视频明确给出的可执行建议；点赞、关注、投币、订阅和分享不属于知识建议。
8. covered_unit_ids 与 omitted_units 必须完整覆盖内容地图中的全部单元，major 单元不得遗漏。
9. v2 profile 的具体知识点、证据和风险应尽量覆盖各个全局主题组；原文不支持时仍应保持空数组。
10. 内容要精炼且避免跨字段重复；knowledge_items 最多 6 项，questions 和 pros_cons 各最多 3 项，evidence 和 risks 各最多 5 项。
11. 输出必须严格符合 JSON Schema。"""

OVERVIEW_SYSTEM_PROMPT = """你负责把按时间切分的视频内容地图整理为全局主题概览。
规则：
1. 覆盖每一个 window_id，并按真实主题或独立案例合并为 1-8 个 topic_groups。
2. 不同窗口可能来自不同患者、故事或案例；原文没有明确说明时，绝不能把前后案例串成同一个人。
3. summary 必须用一句话概括全部 topic_groups，不能只概括开头或结尾。
4. 只能使用输入中的事实、window_id 和 Sxxxx 来源，不得添加外部知识、URL 或其他视频编号。
5. 疑似 ASR 错误应使用保守表述，不得把不确定术语写成确定诊断或治疗。
6. 输出必须严格符合 JSON Schema。"""

CTA_RE = re.compile(r"点赞|关注|投币|一键三连|订阅|转发|分享给|点个赞")
VIDEO_ID_RE = re.compile(r"BV[A-Za-z0-9]+")
OVERVIEW_CACHE_VERSION = "content-overview-2026-07-v1"
SYNTHESIS_ARRAY_LIMITS = {
    "topics": 8,
    "key_points": 8,
    "insights": 8,
    "actions": 8,
    "tags": 12,
    "knowledge_items": 6,
    "questions": 3,
    "pros_cons": 3,
    "evidence": 5,
    "risks": 5,
    "pros": 5,
    "cons": 5,
    "source_refs": 12,
}
SYNTHESIS_LONG_STRING_FIELDS = {
    "summary",
    "explanation",
    "limitations",
    "answer",
    "quality_reason",
    "unresolved",
    "tradeoff",
    "evidence",
    "counterpoint",
    "impact",
    "mitigation",
}


def _as_profile(profile: str | PromptProfile | None) -> PromptProfile:
    if isinstance(profile, PromptProfile):
        return profile
    return load_prompt_profile(profile or DEFAULT_PROFILE_NAME)


def _ensure_no_unexpected_fields(data: dict, allowed: set[str], label: str):
    unexpected = sorted(set(data) - allowed)
    if unexpected:
        raise SchemaValidationError(f"Unexpected field in {label}: {unexpected[0]}")


def _ensure_required_fields(data: dict, required: set[str], label: str):
    missing = sorted(required - set(data))
    if missing:
        raise SchemaValidationError(f"Missing field in {label}: {missing[0]}")


def _ensure_str(value: Any, field_name: str, allow_empty: bool = True):
    if not isinstance(value, str):
        raise SchemaValidationError(f"{field_name} must be str")
    if not allow_empty and not value.strip():
        raise SchemaValidationError(f"{field_name} cannot be empty")


def _ensure_str_list(value: Any, field_name: str, allow_empty: bool = True):
    if not isinstance(value, list):
        raise SchemaValidationError(f"{field_name} must be list")
    if not allow_empty and len(value) == 0:
        raise SchemaValidationError(f"{field_name} cannot be empty")
    for i, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise SchemaValidationError(f"{field_name} must contain non-empty strings")


def _ensure_score(value: Any, field_name: str):
    if not isinstance(value, int):
        raise SchemaValidationError(f"{field_name} must be int")
    if value < 1 or value > 5:
        raise SchemaValidationError(f"{field_name} must be between 1 and 5")


def _validate_source_refs(
    item: dict,
    label: str,
    *,
    allowed_source_refs: set[str] | None = None,
    require_source_refs: bool = False,
):
    if require_source_refs and "source_refs" not in item:
        raise SchemaValidationError(f"{label}.source_refs is required")
    if "source_refs" not in item:
        return
    _ensure_str_list(
        item["source_refs"],
        f"{label}.source_refs",
        allow_empty=not require_source_refs,
    )
    if allowed_source_refs is not None:
        unknown = sorted(set(item["source_refs"]) - allowed_source_refs)
        if unknown:
            raise SchemaValidationError(f"{label}.source_refs contains unknown ref: {unknown[0]}")


def _validate_object_list(
    items: Any,
    field_name: str,
    required_fields: set[str],
    string_fields: set[str],
    list_fields: set[str] | None = None,
    score_fields: set[str] | None = None,
    allow_empty_string_fields: set[str] | None = None,
    allowed_source_refs: set[str] | None = None,
    require_source_refs: bool = False,
):
    if not isinstance(items, list):
        raise SchemaValidationError(f"{field_name} must be list")

    list_fields = list_fields or set()
    score_fields = score_fields or set()
    allow_empty_string_fields = allow_empty_string_fields or set()
    allowed_fields = required_fields | {"source_refs"}

    for i, item in enumerate(items):
        label = f"{field_name}[{i}]"
        if not isinstance(item, dict):
            raise SchemaValidationError(f"{label} must be object")
        _ensure_required_fields(item, required_fields, label)
        _ensure_no_unexpected_fields(item, allowed_fields, label)

        for nested_field in string_fields:
            _ensure_str(
                item[nested_field],
                f"{label}.{nested_field}",
                allow_empty=nested_field in allow_empty_string_fields,
            )
        for nested_field in list_fields:
            _ensure_str_list(item[nested_field], f"{label}.{nested_field}", allow_empty=True)
        for nested_field in score_fields:
            _ensure_score(item[nested_field], f"{label}.{nested_field}")

        if "confidence" in item and item["confidence"] not in {"高", "中", "低"}:
            raise SchemaValidationError(f"{label}.confidence must be 高, 中, or 低")

        _validate_source_refs(
            item,
            label,
            allowed_source_refs=allowed_source_refs,
            require_source_refs=require_source_refs,
        )


def _validate_v1_base(data: dict):
    _ensure_str(data["title"], "title")
    _ensure_str(data["summary"], "summary", allow_empty=False)
    for field in ["topics", "key_points", "insights", "actions", "tags"]:
        _ensure_str_list(data[field], field, allow_empty=True)

    if len(data["tags"]) < 3:
        raise SchemaValidationError("tags must have at least 3 items")


def _validate_v2_details(
    data: dict,
    *,
    allowed_source_refs: set[str] | None = None,
    require_source_refs: bool = False,
):
    _validate_object_list(
        data["knowledge_items"],
        "knowledge_items",
        required_fields={"name", "definition", "explanation", "application", "limitations", "importance", "confidence"},
        string_fields={"name", "definition", "explanation", "application", "limitations", "importance", "confidence"},
        allow_empty_string_fields={"application", "limitations"},
        allowed_source_refs=allowed_source_refs,
        require_source_refs=require_source_refs,
    )
    _validate_object_list(
        data["questions"],
        "questions",
        required_fields={"question", "question_type", "answer", "quality", "quality_score", "quality_reason", "unresolved"},
        string_fields={"question", "question_type", "answer", "quality", "quality_reason", "unresolved"},
        score_fields={"quality_score"},
        allow_empty_string_fields={"unresolved"},
        allowed_source_refs=allowed_source_refs,
        require_source_refs=require_source_refs,
    )
    _validate_object_list(
        data["pros_cons"],
        "pros_cons",
        required_fields={"subject", "pros", "cons", "best_for", "not_for", "tradeoff"},
        string_fields={"subject", "best_for", "not_for", "tradeoff"},
        list_fields={"pros", "cons"},
        allow_empty_string_fields={"best_for", "not_for", "tradeoff"},
        allowed_source_refs=allowed_source_refs,
        require_source_refs=require_source_refs,
    )
    _validate_object_list(
        data["evidence"],
        "evidence",
        required_fields={"claim", "evidence_type", "evidence", "confidence", "counterpoint"},
        string_fields={"claim", "evidence_type", "evidence", "confidence", "counterpoint"},
        allow_empty_string_fields={"counterpoint"},
        allowed_source_refs=allowed_source_refs,
        require_source_refs=require_source_refs,
    )
    _validate_object_list(
        data["risks"],
        "risks",
        required_fields={"risk", "impact", "mitigation", "confidence"},
        string_fields={"risk", "impact", "mitigation", "confidence"},
        allow_empty_string_fields={"impact", "mitigation"},
        allowed_source_refs=allowed_source_refs,
        require_source_refs=require_source_refs,
    )


def _apply_profile_validation(data: dict, validation: dict | None):
    validation = validation or {}
    min_list_lengths = validation.get("min_list_lengths", {})
    if not isinstance(min_list_lengths, dict):
        raise SchemaValidationError("validation.min_list_lengths must be mapping")

    for field, minimum in min_list_lengths.items():
        if field not in data:
            raise SchemaValidationError(f"validation references missing field: {field}")
        if not isinstance(data[field], list):
            raise SchemaValidationError(f"{field} must be list")
        if len(data[field]) < int(minimum):
            raise SchemaValidationError(f"{field} must have at least {minimum} items")


def validate_structure_schema(
    data: dict,
    schema_version: str = DEFAULT_SCHEMA_VERSION,
    validation: dict | None = None,
    *,
    allowed_source_refs: set[str] | None = None,
    require_source_refs: bool = False,
):
    if not isinstance(data, dict):
        raise SchemaValidationError("structured output must be object")

    if schema_version == "v1":
        required = V1_FIELDS
    elif schema_version == "v2":
        required = V2_FIELDS
    else:
        raise SchemaValidationError(f"Unsupported schema_version: {schema_version}")

    _ensure_required_fields(data, required, "root")
    _ensure_no_unexpected_fields(data, required, "root")
    _validate_v1_base(data)
    if schema_version == "v2":
        _validate_v2_details(
            data,
            allowed_source_refs=allowed_source_refs,
            require_source_refs=require_source_refs,
        )
    _apply_profile_validation(data, validation)


def _chat_options(temperature: float) -> dict[str, Any]:
    return {
        "temperature": temperature,
        "num_ctx": LLM_CONFIG["num_ctx"],
        "num_predict": LLM_CONFIG["num_predict"],
    }


def _iter_strings(value: Any):
    if isinstance(value, dict):
        for nested in value.values():
            yield from _iter_strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_strings(nested)
    elif isinstance(value, str):
        yield value


def _validate_repetition(value: Any, label: str = "root") -> None:
    if isinstance(value, dict):
        long_fields = [
            (key, re.sub(r"\s+", "", nested))
            for key, nested in value.items()
            if isinstance(nested, str) and len(re.sub(r"\s+", "", nested)) >= 120
        ]
        for index, (left_key, left) in enumerate(long_fields):
            for right_key, right in long_fields[index + 1:]:
                shorter, longer = sorted((left, right), key=len)
                if shorter in longer or SequenceMatcher(None, left, right).ratio() >= 0.92:
                    raise SchemaValidationError(
                        f"Repeated long content in {label}.{left_key} and {label}.{right_key}"
                    )
        for key, nested in value.items():
            _validate_repetition(nested, f"{label}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _validate_repetition(nested, f"{label}[{index}]")


def validate_grounded_output(data: dict, *, source_id: str | None = None) -> None:
    external = find_external_references(data)
    if external:
        raise SchemaValidationError(f"Output contains external URL: {external[0]}")

    for text in _iter_strings(data):
        for video_id in VIDEO_ID_RE.findall(text):
            if not source_id or video_id != source_id:
                raise SchemaValidationError(f"Output contains unrelated video ID: {video_id}")
    for action in data.get("actions", []):
        if CTA_RE.search(action):
            raise SchemaValidationError(f"Non-knowledge call to action is not allowed: {action}")
    _validate_repetition(data)


def _bind_grounded_schema(
    schema: dict,
    *,
    allowed_source_refs: set[str],
    require_source_refs: bool,
) -> dict:
    bound = copy.deepcopy(schema)

    def visit(node: Any):
        if not isinstance(node, dict):
            return
        properties = node.get("properties")
        if isinstance(properties, dict):
            for field_name, field_schema in properties.items():
                if not isinstance(field_schema, dict):
                    continue
                if field_schema.get("type") == "array":
                    limit = SYNTHESIS_ARRAY_LIMITS.get(field_name)
                    if limit is not None:
                        field_schema["maxItems"] = limit
                    items = field_schema.get("items")
                    if isinstance(items, dict) and items.get("type") == "string":
                        items["maxLength"] = 240
                elif field_schema.get("type") == "string":
                    field_schema["maxLength"] = (
                        480 if field_name in SYNTHESIS_LONG_STRING_FIELDS else 240
                    )
            if "source_refs" in properties:
                source_schema = properties["source_refs"]
                source_schema["minItems"] = 1 if require_source_refs else 0
                if allowed_source_refs:
                    source_schema["items"]["enum"] = sorted(allowed_source_refs)
                if require_source_refs:
                    required = node.setdefault("required", [])
                    if "source_refs" not in required:
                        required.append("source_refs")
            if "confidence" in properties:
                properties["confidence"]["enum"] = ["高", "中", "低"]
        for nested in node.values():
            if isinstance(nested, dict):
                visit(nested)
            elif isinstance(nested, list):
                for item in nested:
                    visit(item)

    visit(bound)
    return bound


def _synthesis_schema(prompt_profile: PromptProfile, content_map: dict) -> dict:
    all_unit_ids = [unit["unit_id"] for unit in content_map["units"]]
    allowed_refs = set(content_map["sources"])
    result_schema = _bind_grounded_schema(
        prompt_profile.json_schema,
        allowed_source_refs=allowed_refs,
        require_source_refs=prompt_profile.schema_version == "v2",
    )
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "result": result_schema,
            "covered_unit_ids": {
                "type": "array",
                "uniqueItems": True,
                "items": {"type": "string", "enum": all_unit_ids},
            },
            "omitted_units": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "unit_id": {"type": "string", "enum": all_unit_ids},
                        "reason": {"type": "string"},
                    },
                    "required": ["unit_id", "reason"],
                },
            },
        },
        "required": ["result", "covered_unit_ids", "omitted_units"],
    }


def _overview_schema(content_map: dict) -> dict:
    window_ids = [digest["window_id"] for digest in window_digests(content_map)]
    source_refs = sorted(content_map["sources"])
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": {"type": "string", "minLength": 1},
            "summary": {"type": "string", "minLength": 1},
            "topic_groups": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string", "minLength": 1},
                        "summary": {"type": "string", "minLength": 1},
                        "window_ids": {
                            "type": "array",
                            "minItems": 1,
                            "uniqueItems": True,
                            "items": {"type": "string", "enum": window_ids},
                        },
                        "source_refs": {
                            "type": "array",
                            "minItems": 1,
                            "uniqueItems": True,
                            "items": {"type": "string", "enum": source_refs},
                        },
                    },
                    "required": ["title", "summary", "window_ids", "source_refs"],
                },
            },
            "tags": {
                "type": "array",
                "minItems": 3,
                "items": {"type": "string", "minLength": 1},
            },
        },
        "required": ["title", "summary", "topic_groups", "tags"],
    }


def _validate_overview(overview: dict, content_map: dict) -> None:
    if not isinstance(overview, dict):
        raise SchemaValidationError("Overview must be object")
    if set(overview) != {"title", "summary", "topic_groups", "tags"}:
        raise SchemaValidationError("Overview fields do not match schema")
    _ensure_str(overview["title"], "overview.title", allow_empty=False)
    _ensure_str(overview["summary"], "overview.summary", allow_empty=False)
    _ensure_str_list(overview["tags"], "overview.tags", allow_empty=False)
    if len(overview["tags"]) < 3:
        raise SchemaValidationError("overview.tags must have at least 3 items")
    if not isinstance(overview["topic_groups"], list) or not overview["topic_groups"]:
        raise SchemaValidationError("overview.topic_groups cannot be empty")
    if len(overview["topic_groups"]) > 8:
        raise SchemaValidationError("overview.topic_groups cannot contain more than 8 items")

    digests = window_digests(content_map)
    allowed_windows = {digest["window_id"] for digest in digests}
    refs_by_window = {
        digest["window_id"]: set(digest["source_refs"])
        for digest in digests
    }
    allowed_refs = set(content_map["sources"])
    covered_windows = set()
    for index, group in enumerate(overview["topic_groups"]):
        label = f"overview.topic_groups[{index}]"
        if not isinstance(group, dict) or set(group) != {
            "title",
            "summary",
            "window_ids",
            "source_refs",
        }:
            raise SchemaValidationError(f"{label} fields do not match schema")
        _ensure_str(group["title"], f"{label}.title", allow_empty=False)
        _ensure_str(group["summary"], f"{label}.summary", allow_empty=False)
        _ensure_str_list(group["window_ids"], f"{label}.window_ids", allow_empty=False)
        _ensure_str_list(group["source_refs"], f"{label}.source_refs", allow_empty=False)
        if not set(group["window_ids"]) <= allowed_windows:
            raise SchemaValidationError(f"{label} contains unknown window")
        if not set(group["source_refs"]) <= allowed_refs:
            raise SchemaValidationError(f"{label} contains unknown source")
        group_windows = set(group["window_ids"])
        duplicate_windows = sorted(covered_windows & group_windows)
        if duplicate_windows:
            raise SchemaValidationError(
                f"Window appears in multiple overview groups: {duplicate_windows[0]}"
            )
        group_refs = set().union(*(refs_by_window[window_id] for window_id in group_windows))
        unrelated_refs = sorted(set(group["source_refs"]) - group_refs)
        if unrelated_refs:
            raise SchemaValidationError(
                f"{label} cites source outside its windows: {unrelated_refs[0]}"
            )
        covered_windows.update(group_windows)
    missing = sorted(allowed_windows - covered_windows)
    if missing:
        raise SchemaValidationError(f"Overview is missing window: {missing[0]}")
    validate_grounded_output(
        {
            "summary": overview["summary"],
            "topics": [group["title"] for group in overview["topic_groups"]],
            "actions": [],
        },
        source_id=content_map.get("source_id") or None,
    )


def _text_bigrams(value: str) -> set[str]:
    compact = re.sub(r"[^\w\u4e00-\u9fff]+", "", value.lower())
    if len(compact) < 2:
        return {compact} if compact else set()
    return {compact[index : index + 2] for index in range(len(compact) - 1)}


def _normalize_overview_assignments(overview: dict, content_map: dict) -> dict:
    """Turn model-suggested groups into an exact, source-grounded window partition."""
    normalized = copy.deepcopy(overview)
    digests = window_digests(content_map)
    window_order = [digest["window_id"] for digest in digests]
    window_positions = {window_id: index for index, window_id in enumerate(window_order)}
    digest_by_window = {digest["window_id"]: digest for digest in digests}
    groups = normalized.get("topic_groups")
    if not isinstance(groups, list) or not groups:
        return normalized
    if any(not isinstance(group, dict) for group in groups):
        return normalized

    claimed_by_group = []
    group_features = []
    for group in groups:
        claimed = [
            window_id
            for window_id in group.get("window_ids", [])
            if window_id in window_positions
        ]
        claimed_by_group.append(list(dict.fromkeys(claimed)))
        group_features.append(
            _text_bigrams(f"{group.get('title', '')} {group.get('summary', '')}")
        )

    assignments: list[list[str]] = [[] for _ in groups]
    for window_id in window_order:
        explicit_candidates = [
            index
            for index, claimed in enumerate(claimed_by_group)
            if window_id in claimed
        ]
        candidates = explicit_candidates or list(range(len(groups)))
        digest = digest_by_window[window_id]
        digest_text = " ".join(
            [
                *digest.get("titles", []),
                *digest.get("summaries", []),
                *digest.get("key_points", []),
            ]
        )
        digest_features = _text_bigrams(digest_text)

        def candidate_score(group_index: int) -> tuple[int, int, int]:
            lexical_overlap = len(group_features[group_index] & digest_features)
            neighbors = [
                window_positions[claimed_window]
                for claimed_window in claimed_by_group[group_index]
                if claimed_window != window_id
            ]
            distance = (
                min(abs(window_positions[window_id] - position) for position in neighbors)
                if neighbors
                else len(window_order)
            )
            return lexical_overlap, -distance, -group_index

        selected = max(candidates, key=candidate_score)
        assignments[selected].append(window_id)

    rebuilt_groups = []
    for group, assigned_windows in zip(groups, assignments):
        if not assigned_windows:
            continue
        rebuilt = copy.deepcopy(group)
        rebuilt["window_ids"] = assigned_windows
        rebuilt["source_refs"] = list(
            dict.fromkeys(
                ref
                for window_id in assigned_windows
                for ref in digest_by_window[window_id].get("source_refs", [])
            )
        )
        rebuilt_groups.append(rebuilt)
    normalized["topic_groups"] = rebuilt_groups
    if isinstance(normalized.get("tags"), list):
        normalized["tags"] = list(dict.fromkeys(normalized["tags"]))
    return normalized


def _overview_cache_manifest(content_map: dict) -> dict:
    digests = window_digests(content_map)
    schema = _overview_schema(content_map)

    def fingerprint(value: Any) -> str:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    return {
        "version": OVERVIEW_CACHE_VERSION,
        "model": MODEL,
        "num_ctx": LLM_CONFIG["num_ctx"],
        "digests_fingerprint": fingerprint(digests),
        "schema_fingerprint": fingerprint(schema),
    }


def extract_content_overview(
    content_map: dict,
    *,
    max_retries: int = 3,
) -> dict:
    cached = content_map.get("_overview")
    if isinstance(cached, dict):
        _validate_overview(cached, content_map)
        return cached

    cache_manifest = _overview_cache_manifest(content_map)
    cached = load_cached_artifact(
        content_map,
        "content_overview",
        cache_manifest,
    )
    if isinstance(cached, dict):
        try:
            cached = _normalize_overview_assignments(cached, content_map)
            _validate_overview(cached, content_map)
            content_map["_overview"] = cached
            logger.info("[ContentOverview] cache hit")
            return cached
        except (SchemaValidationError, KeyError, TypeError, ValueError):
            logger.warning("Ignoring invalid cached content overview")

    digests = window_digests(content_map)
    messages = [
        {"role": "system", "content": OVERVIEW_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"窗口内容地图：\n{json.dumps(digests, ensure_ascii=False)}",
        },
    ]
    schema = _overview_schema(content_map)
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
                        "content": f"上一次全局概览校验失败：{last_error}。请覆盖缺失窗口并修正 JSON。",
                    },
                ]
            )
        response = _ollama_client.chat(
            model=MODEL,
            messages=request_messages,
            format=schema,
            options=_chat_options(0.1),
            keep_alive=LLM_CONFIG["keep_alive"],
        )
        last_content = response["message"]["content"].strip()
        try:
            overview = _normalize_overview_assignments(
                json.loads(last_content),
                content_map,
            )
            _validate_overview(overview, content_map)
            content_map["_overview"] = overview
            write_cached_artifact(
                content_map,
                "content_overview",
                cache_manifest,
                overview,
            )
            return overview
        except (json.JSONDecodeError, SchemaValidationError, KeyError, TypeError, ValueError) as exc:
            last_error = exc
            logger.warning("Overview attempt %d/%d failed: %s", attempt + 1, max_retries, exc)

    raise RuntimeError(f"Failed to build content overview after {max_retries} attempts: {last_error}")


def _apply_overview_to_result(result: dict, overview: dict) -> dict:
    merged = copy.deepcopy(result)
    topics = [group["title"] for group in overview["topic_groups"]]
    summary = (
        overview["summary"].strip()
        if len(topics) == 1
        else f"视频主要涉及{'、'.join(topics)}。"
    )
    merged["title"] = overview["title"]
    merged["summary"] = summary
    merged["topics"] = topics
    merged["key_points"] = [group["summary"] for group in overview["topic_groups"]]
    merged["tags"] = list(dict.fromkeys([*overview["tags"], *merged.get("tags", [])]))
    return merged


def _validate_synthesis_coverage(envelope: dict, content_map: dict) -> None:
    if not isinstance(envelope, dict):
        raise SchemaValidationError("Synthesis envelope must be object")
    if set(envelope) != {"result", "covered_unit_ids", "omitted_units"}:
        raise SchemaValidationError("Synthesis envelope fields do not match schema")
    covered = envelope["covered_unit_ids"]
    omitted_items = envelope["omitted_units"]
    if not isinstance(covered, list) or len(covered) != len(set(covered)):
        raise SchemaValidationError("covered_unit_ids must be a unique list")
    if not isinstance(omitted_items, list):
        raise SchemaValidationError("omitted_units must be list")

    omitted = set()
    for item in omitted_items:
        if (
            not isinstance(item, dict)
            or set(item) != {"unit_id", "reason"}
            or not isinstance(item["reason"], str)
            or not item["reason"].strip()
        ):
            raise SchemaValidationError("Each omitted unit requires unit_id and reason")
        omitted.add(item["unit_id"])

    all_ids = {unit["unit_id"] for unit in content_map["units"]}
    covered_set = set(covered)
    if covered_set & omitted:
        raise SchemaValidationError("A unit cannot be both covered and omitted")
    unknown = sorted((covered_set | omitted) - all_ids)
    missing = sorted(all_ids - covered_set - omitted)
    if unknown:
        raise SchemaValidationError(f"Coverage contains unknown unit: {unknown[0]}")
    if missing:
        raise SchemaValidationError(f"Coverage is missing unit: {missing[0]}")

    required = set(required_unit_ids(content_map))
    missing_required = sorted(required - covered_set)
    if missing_required:
        raise SchemaValidationError(f"Required content unit omitted: {missing_required[0]}")

    for window in content_map["windows"]:
        if window["content_type"] != "substantive":
            continue
        if not covered_set.intersection(window["unit_ids"]):
            raise SchemaValidationError(f"No content covered from {window['window_id']}")


def _normalize_synthesis_coverage(
    envelope: dict,
    content_map: dict,
    *,
    include_required: bool = False,
) -> dict:
    """Fill bookkeeping omissions for optional units; required units stay strict."""
    normalized = copy.deepcopy(envelope)
    covered = set(normalized.get("covered_unit_ids", []))
    required = set(required_unit_ids(content_map))
    known = {unit["unit_id"] for unit in content_map["units"]}
    omitted_items = [
        item
        for item in normalized.get("omitted_units", [])
        if isinstance(item, dict)
    ]
    if include_required:
        covered.update(required)
        omitted_items = [item for item in omitted_items if item.get("unit_id") not in required]
        normalized["covered_unit_ids"] = [
            unit["unit_id"]
            for unit in content_map["units"]
            if unit["unit_id"] in covered
        ]
        normalized["omitted_units"] = omitted_items
    omitted = {item.get("unit_id") for item in omitted_items}
    missing_optional = sorted(known - covered - omitted - required)
    if missing_optional:
        normalized.setdefault("omitted_units", []).extend(
            {
                "unit_id": unit_id,
                "reason": "次要内容未纳入本 profile 的最终摘要",
            }
            for unit_id in missing_optional
        )
    return normalized


def _direct_structure(
    transcript_text: str,
    *,
    prompt_profile: PromptProfile,
    max_retries: int,
    source_id: str | None,
) -> dict:
    messages = [
        {"role": "system", "content": prompt_profile.system_prompt},
        {"role": "user", "content": prompt_profile.render_user_prompt(transcript_text)},
    ]
    last_content = ""
    last_error: Exception | None = None

    for attempt in range(max_retries):
        request_messages = list(messages)
        if attempt and last_content:
            request_messages.extend(
                [
                    {"role": "assistant", "content": last_content},
                    {"role": "user", "content": f"上一次 JSON 校验失败：{last_error}。请只修正错误。"},
                ]
            )
        response = _ollama_client.chat(
            model=MODEL,
            messages=request_messages,
            format=_bind_grounded_schema(
                prompt_profile.json_schema,
                allowed_source_refs=set(),
                require_source_refs=False,
            ),
            options=_chat_options(prompt_profile.temperature),
            keep_alive=LLM_CONFIG["keep_alive"],
        )
        last_content = response["message"]["content"].strip()
        try:
            result = json.loads(last_content)
            validate_structure_schema(
                result,
                schema_version=prompt_profile.schema_version,
                validation=prompt_profile.validation,
                allowed_source_refs=set(),
            )
            validate_grounded_output(result, source_id=source_id)
            return result
        except (json.JSONDecodeError, SchemaValidationError, KeyError, TypeError, ValueError) as exc:
            last_error = exc
            logger.warning("Direct structure attempt %d/%d failed: %s", attempt + 1, max_retries, exc)

    raise RuntimeError(
        f"Failed to structure transcript after {max_retries} attempts "
        f"(profile={prompt_profile.name}): {last_error}"
    )


def structure_content_map(
    content_map: dict,
    *,
    profile: str | PromptProfile | None = DEFAULT_PROFILE_NAME,
    max_retries: int = 3,
    source_id: str | None = None,
) -> dict:
    prompt_profile = _as_profile(profile)
    source_id = source_id or content_map.get("source_id") or None
    overview = extract_content_overview(content_map, max_retries=max_retries)
    required = required_unit_ids(content_map)
    synthesis_input = content_map_for_synthesis(content_map)
    messages = [
        {
            "role": "system",
            "content": f"{prompt_profile.system_prompt}\n\n{SYNTHESIS_SYSTEM_PROMPT}",
        },
        {
            "role": "user",
            "content": (
                f"目标 profile：{prompt_profile.name}\n"
                f"必须覆盖的内容单元：{', '.join(required)}\n\n"
                "共享全局主题概览（标题、摘要、主题和关键点以此为准）：\n"
                f"{json.dumps(overview, ensure_ascii=False)}\n\n"
                "完整内容地图：\n"
                f"{json.dumps(synthesis_input, ensure_ascii=False)}"
            ),
        },
    ]
    schema = _synthesis_schema(prompt_profile, content_map)
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
                        "content": f"上一次综合结果校验失败：{last_error}。保留事实，只修正覆盖或格式错误。",
                    },
                ]
            )
        response = _ollama_client.chat(
            model=MODEL,
            messages=request_messages,
            format=schema,
            options=_chat_options(prompt_profile.temperature),
            keep_alive=LLM_CONFIG["keep_alive"],
        )
        last_content = response["message"]["content"].strip()
        try:
            envelope = _normalize_synthesis_coverage(
                json.loads(last_content),
                content_map,
                include_required=attempt == max_retries - 1,
            )
            _validate_synthesis_coverage(envelope, content_map)
            result = _apply_overview_to_result(envelope["result"], overview)
            validate_structure_schema(
                result,
                schema_version=prompt_profile.schema_version,
                validation=prompt_profile.validation,
                allowed_source_refs=set(content_map["sources"]),
                require_source_refs=prompt_profile.schema_version == "v2",
            )
            validate_grounded_output(result, source_id=source_id)
            return result
        except (json.JSONDecodeError, SchemaValidationError, KeyError, TypeError, ValueError) as exc:
            last_error = exc
            logger.warning(
                "Synthesis attempt %d/%d failed for profile=%s: %s",
                attempt + 1,
                max_retries,
                prompt_profile.name,
                exc,
            )

    raise RuntimeError(
        f"Failed to synthesize content map after {max_retries} attempts "
        f"(profile={prompt_profile.name}): {last_error}"
    )


def structure_transcript(
    transcript_text: str,
    max_retries: int = 3,
    profile: str | PromptProfile | None = DEFAULT_PROFILE_NAME,
    *,
    source_id: str | None = None,
) -> dict:
    prompt_profile = _as_profile(profile)
    if len(transcript_text) <= LLM_DIRECT_MAX_CHARS:
        return _direct_structure(
            transcript_text,
            prompt_profile=prompt_profile,
            max_retries=max_retries,
            source_id=source_id,
        )

    content_map = extract_content_map(
        transcript_text,
        source_id=source_id,
        max_retries=max_retries,
    )
    return structure_content_map(
        content_map,
        profile=prompt_profile,
        max_retries=max_retries,
        source_id=source_id,
    )


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _refs(item: dict) -> str:
    refs = item.get("source_refs") or []
    return ", ".join(refs) if refs else "N/A"


def _section(parts: list[str], title: str, body: str):
    if body.strip():
        parts.append(f"## {title}\n\n{body.strip()}\n")


def _render_knowledge_items(items: list[dict]) -> str:
    blocks = []
    for i, item in enumerate(items, 1):
        blocks.append(
            "\n".join(
                [
                    f"### 知识点 {i}: {item['name']}",
                    f"- 定义：{item['definition']}",
                    f"- 解释：{item['explanation']}",
                    f"- 适用场景：{item['application']}",
                    f"- 局限条件：{item['limitations']}",
                    f"- 重要性：{item['importance']}",
                    f"- 可信度：{item['confidence']}",
                    f"- 来源：{_refs(item)}",
                ]
            )
        )
    return "\n\n".join(blocks)


def _render_questions(items: list[dict]) -> str:
    blocks = []
    for i, item in enumerate(items, 1):
        blocks.append(
            "\n".join(
                [
                    f"### 问题 {i}: {item['question']}",
                    f"- 问题类型：{item['question_type']}",
                    f"- 视频回答：{item['answer']}",
                    f"- 问题质量：{item['quality']} ({item['quality_score']}/5)",
                    f"- 判断理由：{item['quality_reason']}",
                    f"- 未解决部分：{item['unresolved']}",
                    f"- 来源：{_refs(item)}",
                ]
            )
        )
    return "\n\n".join(blocks)


def _render_pros_cons(items: list[dict]) -> str:
    blocks = []
    for i, item in enumerate(items, 1):
        pros = _bullets(item["pros"]) if item["pros"] else "- N/A"
        cons = _bullets(item["cons"]) if item["cons"] else "- N/A"
        blocks.append(
            "\n".join(
                [
                    f"### 对象 {i}: {item['subject']}",
                    "- 优点：",
                    pros,
                    "- 缺点：",
                    cons,
                    f"- 适合：{item['best_for']}",
                    f"- 不适合：{item['not_for']}",
                    f"- 权衡：{item['tradeoff']}",
                    f"- 来源：{_refs(item)}",
                ]
            )
        )
    return "\n\n".join(blocks)


def _render_evidence(items: list[dict]) -> str:
    blocks = []
    for i, item in enumerate(items, 1):
        blocks.append(
            "\n".join(
                [
                    f"### 证据 {i}: {item['claim']}",
                    f"- 证据类型：{item['evidence_type']}",
                    f"- 支持内容：{item['evidence']}",
                    f"- 可信度：{item['confidence']}",
                    f"- 可能反面：{item['counterpoint']}",
                    f"- 来源：{_refs(item)}",
                ]
            )
        )
    return "\n\n".join(blocks)


def _render_risks(items: list[dict]) -> str:
    blocks = []
    for i, item in enumerate(items, 1):
        blocks.append(
            "\n".join(
                [
                    f"### 风险 {i}: {item['risk']}",
                    f"- 影响：{item['impact']}",
                    f"- 缓解方式：{item['mitigation']}",
                    f"- 可信度：{item['confidence']}",
                    f"- 来源：{_refs(item)}",
                ]
            )
        )
    return "\n\n".join(blocks)


def _render_content_outline(content_map: dict) -> str:
    sources = content_map.get("sources", {})
    blocks = []
    for index, digest in enumerate(window_digests(content_map), 1):
        time_range = "N/A"
        if digest.get("end", 0.0) > digest.get("start", 0.0):
            time_range = f"{format_time(digest['start'])}-{format_time(digest['end'])}"
        refs = []
        for ref_id in digest.get("source_refs", []):
            source = sources.get(ref_id, {})
            if source.get("has_timestamp"):
                refs.append(
                    f"{ref_id} ({format_time(source.get('start', 0.0))}-"
                    f"{format_time(source.get('end', 0.0))})"
                )
            else:
                refs.append(ref_id)
        blocks.append(
            "\n".join(
                [
                    f"### {index}. {'、'.join(digest['titles'])}",
                    "；".join(digest["summaries"]),
                    f"- 时间：{time_range}",
                    f"- 来源：{', '.join(refs) if refs else 'N/A'}",
                ]
            )
        )
    return "\n\n".join(blocks)


def render_markdown(
    data: dict,
    url: str,
    profile: str = DEFAULT_PROFILE_NAME,
    schema_version: str = DEFAULT_SCHEMA_VERSION,
    prompt_version: str | None = None,
    content_map: dict | None = None,
    transcript_metadata: dict | None = None,
) -> str:
    video_id = extract_video_id(url)
    if not video_id:
        raise ValueError(f"Cannot extract video ID from URL: {url}")
    md_id = make_document_id(video_id, profile)
    date_str = datetime.now().strftime("%Y-%m-%d")

    frontmatter = {
        "title": data["title"],
        "source": url,
        "id": md_id,
        "video_id": video_id,
        "date": date_str,
        "profile": profile,
        "schema_version": schema_version,
        "tags": data["tags"],
    }
    if prompt_version:
        frontmatter["prompt_version"] = prompt_version
    if transcript_metadata:
        for key in [
            "text_source",
            "source_language",
            "source_provider",
            "subtitle_language",
            "subtitle_is_auto",
            "subtitle_provider",
            "subtitle_track_id",
            "fallback_chain",
        ]:
            value = transcript_metadata.get(key)
            if value not in (None, "", []):
                frontmatter[key] = value

    yaml_block = yaml.safe_dump(
        frontmatter,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    ).strip()

    parts = [f"---\n{yaml_block}\n---\n"]
    _section(parts, "一句话总结", data["summary"])
    if content_map:
        _section(parts, "内容脉络", _render_content_outline(content_map))
    _section(parts, "主要话题", _bullets(data.get("topics", [])))
    _section(parts, "核心观点", _bullets(data.get("key_points", [])))
    _section(parts, "洞察", _bullets(data.get("insights", [])))

    if data.get("knowledge_items"):
        _section(parts, "具体知识点", _render_knowledge_items(data["knowledge_items"]))
    if data.get("questions"):
        _section(parts, "问题分析", _render_questions(data["questions"]))
    if data.get("pros_cons"):
        _section(parts, "优缺点分析", _render_pros_cons(data["pros_cons"]))
    if data.get("evidence"):
        _section(parts, "证据与结论", _render_evidence(data["evidence"]))
    if data.get("risks"):
        _section(parts, "风险与限制", _render_risks(data["risks"]))

    _section(parts, "行动建议", _bullets(data.get("actions", [])))
    return "\n".join(parts).rstrip() + "\n"
