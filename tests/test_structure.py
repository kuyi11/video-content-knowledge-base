import json

import pytest
from modules.prompt_profiles import load_prompt_profile
from modules.structure import (
    SchemaValidationError,
    extract_content_overview,
    render_markdown,
    structure_content_map,
    validate_grounded_output,
    validate_structure_schema,
)


def v2_sample():
    return {
        "title": "Detailed Test",
        "summary": "A detailed summary.",
        "topics": ["topic1"],
        "key_points": ["point1"],
        "insights": ["insight1"],
        "knowledge_items": [
            {
                "name": "Concept A",
                "definition": "Definition A",
                "explanation": "Explanation A",
                "application": "Application A",
                "limitations": "Limitation A",
                "importance": "high",
                "confidence": "高",
                "source_refs": ["S001"],
            }
        ],
        "questions": [
            {
                "question": "What is A?",
                "question_type": "事实型",
                "answer": "A is explained in the video.",
                "quality": "strong",
                "quality_score": 4,
                "quality_reason": "The question is clear and answered.",
                "unresolved": "None",
                "source_refs": ["S001"],
            }
        ],
        "pros_cons": [
            {
                "subject": "Method A",
                "pros": ["Fast"],
                "cons": ["Costly"],
                "best_for": "Small cases",
                "not_for": "Large cases",
                "tradeoff": "Speed versus cost",
                "source_refs": ["S002"],
            }
        ],
        "evidence": [
            {
                "claim": "Claim A",
                "evidence_type": "案例",
                "evidence": "Case evidence",
                "confidence": "中",
                "counterpoint": "May not generalize",
            }
        ],
        "risks": [
            {
                "risk": "Risk A",
                "impact": "Possible misuse",
                "mitigation": "Check context",
                "confidence": "中",
            }
        ],
        "actions": ["Do A"],
        "tags": ["tag1", "tag2", "tag3"],
    }


def content_map_sample():
    unit_base = {
        "unit_type": "case",
        "importance": "major",
        "key_points": [],
        "recommendations": [],
        "uncertainties": [],
    }
    return {
        "version": "content-map-v1",
        "source_id": "BVgrounded",
        "duration": 1200.0,
        "sources": {
            "S0001": {"start": 0.0, "end": 100.0, "has_timestamp": True, "text": "酒精案例"},
            "S0002": {"start": 900.0, "end": 1000.0, "has_timestamp": True, "text": "寄生虫案例"},
        },
        "windows": [
            {
                "window_id": "W001",
                "start": 0.0,
                "end": 100.0,
                "primary_source_refs": ["S0001"],
                "content_type": "substantive",
                "non_content_reason": "",
                "unit_ids": ["W001-U01"],
            },
            {
                "window_id": "W002",
                "start": 900.0,
                "end": 1000.0,
                "primary_source_refs": ["S0002"],
                "content_type": "substantive",
                "non_content_reason": "",
                "unit_ids": ["W002-U01"],
            },
        ],
        "units": [
            {
                **unit_base,
                "unit_id": "W001-U01",
                "window_id": "W001",
                "title": "儿童酒精暴露",
                "summary": "讨论儿童误饮白酒的风险",
                "source_refs": ["S0001"],
                "start": 0.0,
                "end": 100.0,
            },
            {
                **unit_base,
                "unit_id": "W002-U01",
                "window_id": "W002",
                "title": "寄生虫相关心包积液",
                "summary": "讨论寄生虫相关积液的诊断",
                "source_refs": ["S0002"],
                "start": 900.0,
                "end": 1000.0,
            },
        ],
        "_overview": {
            "title": "两类儿科案例",
            "summary": "视频讨论儿童酒精暴露与寄生虫感染相关的心包积液。",
            "topic_groups": [
                {
                    "title": "儿童酒精暴露",
                    "summary": "酒精案例",
                    "window_ids": ["W001"],
                    "source_refs": ["S0001"],
                },
                {
                    "title": "寄生虫感染",
                    "summary": "心包积液案例",
                    "window_ids": ["W002"],
                    "source_refs": ["S0002"],
                },
            ],
            "tags": ["儿科", "酒精", "寄生虫"],
        },
    }


class FakeStructureClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return {"message": {"content": json.dumps(self.payload, ensure_ascii=False)}}


class SequentialStructureClient:
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "message": {
                "content": json.dumps(next(self.payloads), ensure_ascii=False),
            }
        }


class TestValidateStructureSchema:
    def test_valid_data(self):
        data = {
            "title": "Test Title",
            "summary": "A brief summary.",
            "topics": ["topic1", "topic2"],
            "key_points": ["point1"],
            "insights": ["insight1"],
            "actions": ["action1"],
            "tags": ["tag1", "tag2", "tag3"],
        }
        validate_structure_schema(data)

    def test_missing_field(self):
        data = {
            "title": "Test",
            "summary": "Summary",
            "topics": [],
            "key_points": [],
            "actions": ["action1"],
            "tags": ["tag1", "tag2", "tag3"],
        }
        with pytest.raises(SchemaValidationError, match="Missing field"):
            validate_structure_schema(data)

    def test_unexpected_field(self):
        data = {
            "title": "Test",
            "summary": "Summary",
            "topics": [],
            "key_points": [],
            "insights": [],
            "actions": ["action1"],
            "tags": ["tag1", "tag2", "tag3"],
            "extra": "should not be here",
        }
        with pytest.raises(SchemaValidationError, match="Unexpected field"):
            validate_structure_schema(data)

    def test_title_not_str(self):
        data = {
            "title": 123,
            "summary": "Summary",
            "topics": [],
            "key_points": [],
            "insights": [],
            "actions": ["action1"],
            "tags": ["tag1", "tag2", "tag3"],
        }
        with pytest.raises(SchemaValidationError, match="title must be str"):
            validate_structure_schema(data)

    def test_summary_empty(self):
        data = {
            "title": "Test",
            "summary": "",
            "topics": [],
            "key_points": [],
            "insights": [],
            "actions": ["action1"],
            "tags": ["tag1", "tag2", "tag3"],
        }
        with pytest.raises(SchemaValidationError, match="summary cannot be empty"):
            validate_structure_schema(data)

    def test_tags_too_few(self):
        data = {
            "title": "Test",
            "summary": "Summary",
            "topics": [],
            "key_points": [],
            "insights": [],
            "actions": ["action1"],
            "tags": ["tag1"],
        }
        with pytest.raises(SchemaValidationError, match="at least 3"):
            validate_structure_schema(data)

    def test_tags_non_empty_strings(self):
        data = {
            "title": "Test",
            "summary": "Summary",
            "topics": [],
            "key_points": [],
            "insights": [],
            "actions": ["action1"],
            "tags": ["tag1", "", "tag3"],
        }
        with pytest.raises(SchemaValidationError, match="non-empty strings"):
            validate_structure_schema(data)

    def test_v2_valid_data(self):
        validate_structure_schema(v2_sample(), schema_version="v2")

    def test_v2_allows_empty_absent_counterpoint_and_unresolved(self):
        data = v2_sample()
        data["questions"][0]["unresolved"] = ""
        data["evidence"][0]["counterpoint"] = ""

        validate_structure_schema(data, schema_version="v2")

    def test_v2_profile_allows_unsupported_sections_to_be_empty(self):
        profile = load_prompt_profile("detailed")
        data = v2_sample()
        data["questions"] = []
        data["pros_cons"] = []
        data["evidence"] = []
        data["risks"] = []
        data["actions"] = []

        validate_structure_schema(
            data,
            schema_version=profile.schema_version,
            validation=profile.validation,
        )

    def test_v2_allows_unsupported_optional_descriptions_to_be_empty(self):
        data = v2_sample()
        data["knowledge_items"][0]["application"] = ""
        data["knowledge_items"][0]["limitations"] = ""
        data["pros_cons"][0]["best_for"] = ""
        data["pros_cons"][0]["not_for"] = ""
        data["pros_cons"][0]["tradeoff"] = ""
        data["risks"][0]["impact"] = ""
        data["risks"][0]["mitigation"] = ""

        validate_structure_schema(data, schema_version="v2")

    def test_v1_allows_no_actions_when_video_has_no_explicit_advice(self):
        data = {
            "title": "Test",
            "summary": "Summary",
            "topics": ["topic"],
            "key_points": ["point"],
            "insights": [],
            "actions": [],
            "tags": ["tag1", "tag2", "tag3"],
        }

        validate_structure_schema(data)

    def test_render_markdown_v2_sections(self):
        profile = load_prompt_profile("detailed")
        md = render_markdown(
            v2_sample(),
            "https://www.bilibili.com/video/BVdetail/",
            profile=profile.name,
            schema_version=profile.schema_version,
            prompt_version=profile.prompt_version,
        )
        assert "profile: detailed" in md
        assert "schema_version: v2" in md
        assert "id: BVdetail__detailed" in md
        assert "video_id: BVdetail" in md
        assert "## 具体知识点" in md
        assert "## 问题分析" in md
        assert "## 优缺点分析" in md
        assert "## 证据与结论" in md
        assert "## 风险与限制" in md


def test_content_map_synthesis_requires_and_covers_early_and_late_units(monkeypatch):
    result = {
        "title": "两类儿科案例",
        "summary": "视频主要涉及儿童酒精暴露、寄生虫感染。",
        "topics": ["儿童酒精暴露", "寄生虫感染"],
        "key_points": ["酒精案例", "心包积液案例"],
        "insights": [],
        "actions": [],
        "tags": ["儿科", "酒精", "寄生虫"],
    }
    client = FakeStructureClient(
        {
            "result": result,
            "covered_unit_ids": ["W001-U01", "W002-U01"],
            "omitted_units": [],
        }
    )
    monkeypatch.setattr("modules.structure._ollama_client", client)

    actual = structure_content_map(content_map_sample(), profile="default")

    assert actual == result
    assert "W001-U01" in client.calls[0]["messages"][1]["content"]
    assert "W002-U01" in client.calls[0]["messages"][1]["content"]
    result_schema = client.calls[0]["format"]["properties"]["result"]["properties"]
    assert result_schema["topics"]["maxItems"] == 8
    assert result_schema["tags"]["maxItems"] == 12


def test_content_map_builds_one_shared_overview_before_profile_synthesis(monkeypatch):
    content_map = content_map_sample()
    overview = content_map.pop("_overview")
    result = {
        "title": "临时标题",
        "summary": "临时摘要。",
        "topics": ["临时主题"],
        "key_points": ["临时要点"],
        "insights": [],
        "actions": [],
        "tags": ["临时1", "临时2", "临时3"],
    }
    client = SequentialStructureClient(
        [
            overview,
            {
                "result": result,
                "covered_unit_ids": ["W001-U01", "W002-U01"],
                "omitted_units": [],
            },
        ]
    )
    monkeypatch.setattr("modules.structure._ollama_client", client)

    actual = structure_content_map(content_map, profile="default")

    assert len(client.calls) == 2
    assert content_map["_overview"] == overview
    assert actual["title"] == overview["title"]
    assert actual["topics"] == ["儿童酒精暴露", "寄生虫感染"]
    assert "共享全局主题概览" in client.calls[1]["messages"][1]["content"]


def test_overview_normalizes_duplicate_and_missing_window_assignments(monkeypatch):
    content_map = content_map_sample()
    content_map.pop("_overview")
    raw_overview = {
        "title": "两类儿科案例",
        "summary": "视频讨论儿童酒精暴露与寄生虫感染相关的心包积液。",
        "topic_groups": [
            {
                "title": "儿童酒精暴露",
                "summary": "酒精案例",
                "window_ids": ["W001"],
                "source_refs": ["S0002"],
            },
            {
                "title": "寄生虫感染",
                "summary": "心包积液案例",
                "window_ids": ["W001"],
                "source_refs": ["S0001"],
            },
        ],
        "tags": ["儿科", "酒精", "寄生虫"],
    }
    client = FakeStructureClient(raw_overview)
    monkeypatch.setattr("modules.structure._ollama_client", client)

    actual = extract_content_overview(content_map, max_retries=1)

    assert actual["topic_groups"][0]["window_ids"] == ["W001"]
    assert actual["topic_groups"][0]["source_refs"] == ["S0001"]
    assert actual["topic_groups"][1]["window_ids"] == ["W002"]
    assert actual["topic_groups"][1]["source_refs"] == ["S0002"]


def test_content_map_synthesis_final_retry_uses_program_outline_for_required_units(monkeypatch):
    result = {
        "title": "Only ending",
        "summary": "只讨论结尾。",
        "topics": ["结尾"],
        "key_points": ["结尾"],
        "insights": [],
        "actions": [],
        "tags": ["tag1", "tag2", "tag3"],
    }
    client = FakeStructureClient(
        {
            "result": result,
            "covered_unit_ids": ["W002-U01"],
            "omitted_units": [{"unit_id": "W001-U01", "reason": "忽略开头"}],
        }
    )
    monkeypatch.setattr("modules.structure._ollama_client", client)

    actual = structure_content_map(content_map_sample(), profile="default", max_retries=1)

    assert actual["title"] == "两类儿科案例"
    assert actual["summary"] == "视频主要涉及儿童酒精暴露、寄生虫感染。"
    assert actual["topics"] == ["儿童酒精暴露", "寄生虫感染"]
    assert actual["key_points"] == ["酒精案例", "心包积液案例"]


def test_grounding_rejects_external_video_and_social_cta():
    with pytest.raises(SchemaValidationError, match="external URL"):
        validate_grounded_output({"actions": [], "summary": "https://example.com"})
    with pytest.raises(SchemaValidationError, match="unrelated video ID"):
        validate_grounded_output({"actions": [], "summary": "参考 BV1Other"}, source_id="BVgrounded")
    with pytest.raises(SchemaValidationError, match="call to action"):
        validate_grounded_output({"actions": ["点赞并关注"], "summary": "内容"})


def test_render_markdown_includes_program_generated_content_outline():
    data = {
        "title": "Outline",
        "summary": "Summary",
        "topics": [],
        "key_points": [],
        "insights": [],
        "actions": [],
        "tags": ["tag1", "tag2", "tag3"],
    }
    md = render_markdown(
        data,
        "https://www.bilibili.com/video/BVgrounded/",
        content_map=content_map_sample(),
    )

    assert "## 内容脉络" in md
    assert "儿童酒精暴露" in md
    assert "寄生虫相关心包积液" in md
    assert "S0001 (00:00-01:40)" in md
