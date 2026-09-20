"""Machine-readable terminology review rules and medical answer gating."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


ISSUE_RE = re.compile(r"〔(?P<kind>[^：〕]+)：(?P<terms>[^〕]+)〕")
HEADER_RE = re.compile(r"^#{2,3}\s+(?P<title>.+?)\s*$")
PENDING_ITEM_RE = re.compile(r"^-\s+`(?P<terms>[^`]+)`")
TERM_SPLIT_RE = re.compile(r"\s*(?:、|，|,|/|\s+或\s+)\s*")
BLOCKING_MARKERS = ("转写错误", "待核", "信息缺失", "疾病名", "机制表述")
HIGH_RISK_RE = re.compile(
    r"剂量|用药|停药|加药|处方|诊断|确诊|治疗|手术|急救|胸痛|呼吸困难|"
    r"大出血|昏迷|怀孕|妊娠|儿童|婴儿|HIV|艾滋|梅毒|淋病|癌|肿瘤|脑梗|心梗"
)


@dataclass(frozen=True)
class ReviewRule:
    issue_type: str
    severity: str
    terms: tuple[str, ...]
    section: str
    line_number: int
    note: str


@dataclass(frozen=True)
class SafetyDecision:
    action: str
    risk_level: str
    reasons: tuple[str, ...] = ()
    matched_terms: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


def _split_terms(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(term.strip() for term in TERM_SPLIT_RE.split(value) if term.strip()))


def parse_review_checklist(text: str) -> list[ReviewRule]:
    rules: list[ReviewRule] = []
    section = ""
    for line_number, line in enumerate(text.splitlines(), start=1):
        if header := HEADER_RE.match(line):
            section = header.group("title")
        for match in ISSUE_RE.finditer(line):
            issue_type = match.group("kind").strip()
            severity = "block" if any(marker in issue_type for marker in BLOCKING_MARKERS) else "warn"
            rules.append(
                ReviewRule(
                    issue_type=issue_type,
                    severity=severity,
                    terms=_split_terms(match.group("terms")),
                    section=section,
                    line_number=line_number,
                    note=line.strip(),
                )
            )
        if section == "仍需人工确认" and (pending := PENDING_ITEM_RE.match(line)):
            rules.append(
                ReviewRule(
                    issue_type="人工确认",
                    severity="block",
                    terms=_split_terms(pending.group("terms")),
                    section=section,
                    line_number=line_number,
                    note=line.strip(),
                )
            )
    return rules


def load_review_rules(path: Path) -> list[ReviewRule]:
    if not path.exists():
        return []
    return parse_review_checklist(path.read_text(encoding="utf-8"))


def export_review_rules(checklist_path: Path, output_path: Path) -> dict:
    rules = load_review_rules(checklist_path)
    payload = {
        "source_path": str(checklist_path.resolve()),
        "source_mtime_ns": checklist_path.stat().st_mtime_ns if checklist_path.exists() else None,
        "rule_count": len(rules),
        "blocking_rule_count": sum(rule.severity == "block" for rule in rules),
        "warning_rule_count": sum(rule.severity == "warn" for rule in rules),
        "rules": [asdict(rule) for rule in rules],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(output_path))
    return payload


def _matched_terms(texts: Iterable[str], rules: Iterable[ReviewRule], severity: str) -> tuple[str, ...]:
    haystack = "\n".join(texts).casefold()
    matches = []
    for rule in rules:
        if rule.severity != severity:
            continue
        for term in rule.terms:
            if len(term) >= 2 and term.casefold() in haystack:
                matches.append(term)
    return tuple(dict.fromkeys(matches))


def evaluate_medical_safety(
    question: str,
    results: list[dict],
    rules: Iterable[ReviewRule],
) -> SafetyDecision:
    medical_context = any(
        result.get("domain") == "medical" or result.get("risk_level") == "high"
        for result in results
    )
    high_risk = bool(HIGH_RISK_RE.search(question)) or any(
        result.get("risk_level") == "high" for result in results
    )
    if not medical_context and not high_risk:
        return SafetyDecision(action="allow", risk_level="low")

    texts = [question, *(str(result.get("text", "")) for result in results)]
    blocking_terms = _matched_terms(texts, rules, "block")
    if blocking_terms:
        return SafetyDecision(
            action="block",
            risk_level="high" if high_risk else "medium",
            reasons=("retrieved evidence contains terminology pending human review",),
            matched_terms=blocking_terms,
        )

    has_reviewed = any(result.get("quality") == "human_reviewed" for result in results)
    has_raw_evidence = any(result.get("chunk_type") == "raw_transcript" for result in results)
    has_summary_only = any(
        result.get("answer_policy") == "summary_requires_raw_evidence"
        and not result.get("source_of_truth", False)
        for result in results
    )
    if high_risk and has_summary_only and not has_raw_evidence:
        return SafetyDecision(
            action="block",
            risk_level="high",
            reasons=("derived summary requires raw evidence for high-risk answers",),
        )
    if high_risk and not (has_reviewed or has_raw_evidence):
        return SafetyDecision(
            action="block",
            risk_level="high",
            reasons=("high-risk medical answer lacks raw or human-reviewed evidence",),
        )

    warning_terms = _matched_terms(texts, rules, "warn")
    if high_risk or warning_terms or any(
        result.get("quality") == "derived_pending_review" for result in results
    ):
        reasons = []
        if high_risk and not has_reviewed:
            reasons.append("medical evidence has not been human reviewed")
        if warning_terms:
            reasons.append("retrieved evidence contains ambiguous terminology")
        if not reasons:
            reasons.append("retrieved summary is pending review")
        return SafetyDecision(
            action="warn",
            risk_level="high" if high_risk else "medium",
            reasons=tuple(reasons),
            matched_terms=warning_terms,
        )
    return SafetyDecision(action="allow", risk_level="low")
