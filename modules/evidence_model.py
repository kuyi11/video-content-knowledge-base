"""Stable Claim/Evidence objects shared by grounding and API consumers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class EvidenceUnit:
    evidence_id: str
    document_id: str
    chunk_id: str
    video_id: str
    source_refs: tuple[str, ...]
    text: str
    chunk_type: str
    risk_level: str
    review_status: str
    source_of_truth: bool | None
    answer_policy: str
    retrieval_role: str
    evidence_fingerprint: str

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["source_refs"] = list(self.source_refs)
        return payload


def build_evidence_units(results) -> dict[str, EvidenceUnit]:
    """Build one auditable evidence unit per retrieved chunk."""
    units = {}
    for item in results:
        chunk_id = str(item.get("chunk_id", "")).strip()
        if not chunk_id or chunk_id in units:
            continue
        payload = {
            "document_id": str(item.get("document_id", "")),
            "chunk_id": chunk_id,
            "video_id": str(item.get("video_id", "")),
            "source_refs": tuple(
                dict.fromkeys(str(ref) for ref in item.get("source_refs") or [])
            ),
            "text": str(item.get("text", item.get("content", ""))),
            "chunk_type": str(item.get("chunk_type", "")),
            "risk_level": str(item.get("risk_level", "low")),
            "review_status": str(item.get("review_status", "")),
            "source_of_truth": item.get("source_of_truth"),
            "answer_policy": str(item.get("answer_policy", "")),
        }
        units[chunk_id] = EvidenceUnit(
            evidence_id=f"E:{chunk_id}",
            **payload,
            retrieval_role=str(item.get("retrieval_role", "primary")),
            evidence_fingerprint=str(item.get("evidence_fingerprint", ""))
            or evidence_fingerprint(payload),
        )
    return units


def evidence_fingerprint(payload: dict) -> str:
    """Hash evidence content and admission-relevant metadata, excluding query-time roles."""
    canonical = {
        "document_id": str(payload.get("document_id", "")),
        "chunk_id": str(payload.get("chunk_id", "")),
        "video_id": str(payload.get("video_id", "")),
        "source_refs": sorted({str(ref) for ref in payload.get("source_refs") or []}),
        "text": str(payload.get("text", payload.get("content", ""))),
        "chunk_type": str(payload.get("chunk_type", "")),
        "risk_level": str(payload.get("risk_level", "low")),
        "review_status": str(payload.get("review_status", "")),
        "source_of_truth": payload.get("source_of_truth"),
        "answer_policy": str(payload.get("answer_policy", "")),
    }
    encoded = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def allowed_for_answer(
    units: list[EvidenceUnit],
    *,
    citations_valid: bool,
    semantic_enabled: bool,
    semantic_verdict: str,
) -> bool:
    """Compute admission from citation, semantic, and summary provenance policy."""
    if not citations_valid or not units:
        return False
    if semantic_enabled and semantic_verdict != "supported":
        return False
    return all(
        unit.answer_policy != "summary_requires_raw_evidence"
        and unit.retrieval_role != "derived_summary"
        for unit in units
    )
