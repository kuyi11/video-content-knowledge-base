from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from config import PROMPT_DIR
from modules.document_identity import validate_profile_name

logger = logging.getLogger(__name__)

DEFAULT_PROFILE_NAME = "default"
DEFAULT_SCHEMA_VERSION = "v1"


@dataclass(slots=True)
class PromptProfile:
    name: str
    description: str
    schema_version: str
    prompt_version: str
    system_prompt: str
    user_prompt_template: str
    temperature: float = 0.3
    validation: dict[str, Any] = field(default_factory=dict)
    profile_path: Path | None = None
    schema_path: Path | None = None
    json_schema: dict[str, Any] = field(default_factory=dict)

    def render_user_prompt(self, transcript_text: str) -> str:
        return self.user_prompt_template.format(transcript_text=transcript_text)


def _profiles_dir() -> Path:
    return PROMPT_DIR / "profiles"


def _schemas_dir() -> Path:
    return PROMPT_DIR / "schemas"


def available_profiles() -> list[str]:
    profiles_dir = _profiles_dir()
    if not profiles_dir.exists():
        return []
    return sorted(p.stem for p in profiles_dir.glob("*.yaml"))


def _profile_path(name: str) -> Path:
    return _profiles_dir() / f"{name}.yaml"


def _schema_path(schema_version: str) -> Path:
    return _schemas_dir() / f"{schema_version}.json"


def load_structure_schema(schema_version: str = DEFAULT_SCHEMA_VERSION) -> dict[str, Any]:
    schema_path = _schema_path(schema_version)
    if not schema_path.exists():
        raise FileNotFoundError(f"Prompt schema not found: {schema_path}")
    return json.loads(schema_path.read_text(encoding="utf-8"))


def load_prompt_profile(name: str = DEFAULT_PROFILE_NAME) -> PromptProfile:
    profile_path = _profile_path(name)
    if not profile_path.exists():
        choices = ", ".join(available_profiles()) or "(none)"
        raise FileNotFoundError(f"Prompt profile not found: {profile_path}. Available: {choices}")

    raw = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid prompt profile: {profile_path}")

    system_prompt = raw.get("system_prompt")
    user_prompt_template = raw.get("user_prompt_template")
    if not system_prompt or not user_prompt_template:
        raise ValueError(f"Prompt profile missing required prompt text: {profile_path}")

    schema_version = str(raw.get("schema_version", DEFAULT_SCHEMA_VERSION))
    schema_path = _schema_path(schema_version)
    json_schema = load_structure_schema(schema_version)

    validation = raw.get("validation") or {}
    if not isinstance(validation, dict):
        raise ValueError(f"Prompt profile validation must be a mapping: {profile_path}")

    temperature = float(raw.get("temperature", 0.3))
    profile_name = validate_profile_name(str(raw.get("name", name)))
    return PromptProfile(
        name=profile_name,
        description=str(raw.get("description", "")),
        schema_version=schema_version,
        prompt_version=str(raw.get("prompt_version", name)),
        system_prompt=str(system_prompt).strip(),
        user_prompt_template=str(user_prompt_template).strip(),
        temperature=temperature,
        validation=validation,
        profile_path=profile_path,
        schema_path=schema_path,
        json_schema=json_schema,
    )
