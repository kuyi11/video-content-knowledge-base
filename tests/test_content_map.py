import json

import pytest

from modules.content_map import (
    ContentMapValidationError,
    bind_content_map_schema,
    extract_content_map,
    load_cached_artifact,
    validate_window_map,
    write_cached_artifact,
)
from modules.transcript_parser import SourceBlock, TranscriptWindow


def _unit(title="Case", refs=None):
    return {
        "title": title,
        "summary": f"Summary for {title}",
        "unit_type": "case",
        "importance": "major",
        "key_points": [f"Point for {title}"],
        "recommendations": [],
        "uncertainties": [],
        "source_refs": refs or ["S0001"],
    }


def _window():
    return TranscriptWindow(
        window_id="W001",
        blocks=(
            SourceBlock("S0001", 0.0, 10.0, "first"),
            SourceBlock("S0002", 10.0, 20.0, "second"),
        ),
        primary_ref_ids=("S0001", "S0002"),
    )


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return {"message": {"content": json.dumps(self.payload, ensure_ascii=False)}}


def test_runtime_schema_binds_only_current_window_sources():
    schema = bind_content_map_schema(["S0041", "S0042"], max_units=7)
    units = schema["properties"]["units"]
    refs = units["items"]["properties"]["source_refs"]

    assert units["maxItems"] == 7
    assert refs["items"]["enum"] == ["S0041", "S0042"]


def test_content_map_unit_count_and_topics_are_dynamic():
    window = _window()
    one_unit = {
        "content_type": "substantive",
        "units": [_unit()],
        "non_content_reason": "",
    }
    two_units = {
        "content_type": "substantive",
        "units": [_unit("Alcohol"), _unit("Skin", ["S0002"])],
        "non_content_reason": "",
    }

    validate_window_map(one_unit, window)
    validate_window_map(two_units, window)


def test_window_map_rejects_unknown_or_context_only_source():
    window = TranscriptWindow(
        window_id="W002",
        blocks=(
            SourceBlock("S0001", 0.0, 10.0, "context"),
            SourceBlock("S0002", 10.0, 20.0, "primary"),
        ),
        primary_ref_ids=("S0002",),
    )
    unknown = {
        "content_type": "substantive",
        "units": [_unit(refs=["S9999"])],
        "non_content_reason": "",
    }
    context_only = {
        "content_type": "substantive",
        "units": [_unit(refs=["S0001"])],
        "non_content_reason": "",
    }

    with pytest.raises(ContentMapValidationError, match="unknown"):
        validate_window_map(unknown, window)
    with pytest.raises(ContentMapValidationError, match="context-only"):
        validate_window_map(context_only, window)


def test_extract_content_map_reuses_project_cache(tmp_path):
    payload = {
        "content_type": "substantive",
        "units": [_unit("Alcohol exposure")],
        "non_content_reason": "",
    }
    client = FakeClient(payload)
    transcript = "[0.0s -> 5.0s] 儿童误饮白酒\n[5.0s -> 10.0s] 不要自行催吐"

    first = extract_content_map(
        transcript,
        source_id="BVcache",
        cache_dir=tmp_path,
        client=client,
    )
    second = extract_content_map(
        transcript,
        source_id="BVcache",
        cache_dir=tmp_path,
        client=client,
    )

    assert len(client.calls) == 1
    assert first == second
    assert first["units"][0]["unit_id"] == "W001-U01"
    assert first["units"][0]["source_refs"] == ["S0001"]
    assert next(tmp_path.rglob("content_map.json")).is_file()


def test_content_map_cache_invalidates_when_domains_change(tmp_path):
    payload = {
        "content_type": "substantive",
        "units": [_unit("Medical terminology")],
        "non_content_reason": "",
    }
    client = FakeClient(payload)
    transcript = "[0.0s -> 5.0s] 超声提示心包基业"

    extract_content_map(
        transcript,
        source_id="BVdomains",
        domains=[],
        cache_dir=tmp_path,
        client=client,
    )
    extract_content_map(
        transcript,
        source_id="BVdomains",
        domains=["medical"],
        cache_dir=tmp_path,
        client=client,
    )

    assert len(client.calls) == 2

def test_versioned_content_map_sidecar_cache(tmp_path):
    content_map = {"_cache_path": str(tmp_path)}
    manifest = {"version": "v1", "fingerprint": "abc"}
    overview = {"title": "Shared overview"}

    write_cached_artifact(content_map, "content_overview", manifest, overview)

    assert load_cached_artifact(content_map, "content_overview", manifest) == overview
    assert load_cached_artifact(
        content_map,
        "content_overview",
        {"version": "v2"},
    ) is None
