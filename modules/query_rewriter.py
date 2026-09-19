"""Deterministic, auditable query expansion for retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class RewriteResult:
    original: str
    rewritten: str
    applied_rules: tuple[str, ...] = ()
    expansions: tuple[str, ...] = ()


class QueryRewriter:
    def __init__(self, rules: list[dict] | None = None, *, version: str = "none"):
        self.rules = tuple(rules or [])
        self.version = version

    @classmethod
    def from_path(cls, path: Path) -> "QueryRewriter":
        if not path.exists():
            return cls()
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rules = payload.get("rules", [])
        if not isinstance(rules, list):
            raise ValueError("query rewrite rules must be a list")
        return cls(rules, version=str(payload.get("version", "unknown")))

    def rewrite(self, query: str) -> RewriteResult:
        original = re.sub(r"\s+", " ", query).strip()
        folded = original.casefold()
        applied = []
        expansions = []
        for rule in self.rules:
            aliases = tuple(str(item).strip() for item in rule.get("aliases", []) if str(item).strip())
            if not aliases or not any(alias.casefold() in folded for alias in aliases):
                continue
            rule_id = str(rule.get("id", "")).strip()
            if rule_id:
                applied.append(rule_id)
            for expansion in rule.get("expansions", []):
                expansion = str(expansion).strip()
                if expansion and expansion.casefold() not in folded:
                    expansions.append(expansion)
        unique_expansions = tuple(dict.fromkeys(expansions))
        rewritten = " ".join((original, *unique_expansions)) if unique_expansions else original
        return RewriteResult(
            original=original,
            rewritten=rewritten,
            applied_rules=tuple(dict.fromkeys(applied)),
            expansions=unique_expansions,
        )
