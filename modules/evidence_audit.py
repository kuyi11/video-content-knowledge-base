"""Persist Claim/Evidence snapshots and detect invalidated historical evidence."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from modules.evidence_model import build_evidence_units


SCHEMA_VERSION = "claim-evidence-audit-v1"
NANOSECONDS_PER_DAY = 86_400 * 1_000_000_000


def build_evidence_audit(
    *,
    question: str,
    answer: str,
    grounding: dict,
    output_gate: dict,
    index_generation_id: str,
) -> dict:
    """Create an immutable snapshot suitable for replay and freshness checks."""
    return {
        "schema_version": SCHEMA_VERSION,
        "audit_id": uuid.uuid4().hex,
        "created_at_ns": time.time_ns(),
        "index_generation_id": index_generation_id,
        "question": question,
        "answer": answer,
        "claims": grounding.get("claims", []),
        "evidence_units": grounding.get("evidence_units", []),
        "output_gate": output_gate,
    }


def persist_evidence_audit(record: dict, index_dir: Path) -> Path:
    """Atomically persist one audit record without rewriting existing history."""
    audit_dir = index_dir / "claim_evidence"
    audit_dir.mkdir(parents=True, exist_ok=True)
    path = audit_dir / f"{record['audit_id']}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))
    return path


def evaluate_evidence_freshness(
    record: dict,
    current_chunks: list[dict],
    *,
    current_generation_id: str,
) -> dict:
    """Compare an audit snapshot with current chunks using stable evidence fingerprints."""
    current_units = build_evidence_units(current_chunks)
    current_by_id = {unit.evidence_id: unit for unit in current_units.values()}
    missing = []
    changed = []
    unchanged = []
    for historical in record.get("evidence_units") or []:
        evidence_id = historical.get("evidence_id", "")
        current = current_by_id.get(evidence_id)
        if current is None:
            missing.append(evidence_id)
        elif current.evidence_fingerprint != historical.get("evidence_fingerprint"):
            changed.append(evidence_id)
        else:
            unchanged.append(evidence_id)

    generation_changed = (
        str(record.get("index_generation_id", "")) != str(current_generation_id)
    )
    if missing or changed:
        status = "invalidated"
    elif generation_changed:
        status = "generation_changed"
    elif not record.get("evidence_units"):
        status = "not_applicable"
    else:
        status = "current"
    return {
        "status": status,
        "audit_id": record.get("audit_id"),
        "recorded_generation_id": str(record.get("index_generation_id", "")),
        "current_generation_id": str(current_generation_id),
        "generation_changed": generation_changed,
        "unchanged_evidence_unit_ids": sorted(unchanged),
        "changed_evidence_unit_ids": sorted(changed),
        "missing_evidence_unit_ids": sorted(missing),
    }


def load_index_evidence(index_dir: Path) -> tuple[list[dict], str]:
    """Load the lightweight chunk snapshot needed for audit validation."""
    vector_dir = index_dir / "vector"
    chunks = json.loads((vector_dir / "chunks.json").read_text(encoding="utf-8"))
    manifest = json.loads((vector_dir / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(chunks, list) or not isinstance(manifest, dict):
        raise ValueError("index evidence files have an invalid shape")
    return chunks, str(manifest.get("generation_id", ""))


def scan_evidence_audits(
    audit_dir: Path,
    current_chunks: list[dict],
    *,
    current_generation_id: str,
    retention_days: int = 0,
    now_ns: int | None = None,
) -> dict:
    """Scan all audit snapshots without returning their sensitive question or answer text."""
    now_ns = time.time_ns() if now_ns is None else now_ns
    retention_days = max(0, int(retention_days))
    entries = []
    for path in sorted(audit_dir.glob("*.json")) if audit_dir.exists() else []:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION:
                raise ValueError("unsupported audit schema")
            freshness = evaluate_evidence_freshness(
                record,
                current_chunks,
                current_generation_id=current_generation_id,
            )
            created_at_ns = int(record.get("created_at_ns", 0))
            age_days = max(0.0, (now_ns - created_at_ns) / NANOSECONDS_PER_DAY)
            expired = retention_days > 0 and age_days >= retention_days
            invalid_ids = set(freshness["changed_evidence_unit_ids"]) | set(
                freshness["missing_evidence_unit_ids"]
            )
            affected_claim_ids = sorted(
                str(claim.get("claim_id"))
                for claim in record.get("claims") or []
                if invalid_ids.intersection(claim.get("evidence_unit_ids") or [])
                and claim.get("claim_id")
            )
            entries.append(
                {
                    "file": path.name,
                    "audit_id": record.get("audit_id"),
                    "status": freshness["status"],
                    "age_days": round(age_days, 3),
                    "expired": expired,
                    "affected_claim_ids": affected_claim_ids,
                    "changed_evidence_unit_ids": freshness[
                        "changed_evidence_unit_ids"
                    ],
                    "missing_evidence_unit_ids": freshness[
                        "missing_evidence_unit_ids"
                    ],
                }
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            entries.append(
                {
                    "file": path.name,
                    "audit_id": None,
                    "status": "corrupt",
                    "expired": False,
                    "error": type(exc).__name__,
                }
            )

    counts = {
        status: sum(entry["status"] == status for entry in entries)
        for status in (
            "current",
            "generation_changed",
            "invalidated",
            "not_applicable",
            "corrupt",
        )
    }
    counts["total"] = len(entries)
    counts["expired"] = sum(bool(entry.get("expired")) for entry in entries)
    return {
        "schema_version": "claim-evidence-lifecycle-v1",
        "status": (
            "attention_required"
            if counts["invalidated"] or counts["corrupt"] or counts["expired"]
            else "ok"
        ),
        "scanned_at_ns": now_ns,
        "current_generation_id": current_generation_id,
        "retention_days": retention_days,
        "counts": counts,
        "records": entries,
    }


def write_audit_scan_report(report: dict, index_dir: Path) -> Path:
    """Atomically write the latest non-sensitive lifecycle report."""
    path = index_dir / "evidence-audit-health.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))
    return path


def prune_expired_audits(report: dict, audit_dir: Path) -> list[str]:
    """Delete only expired JSON files directly contained in the configured audit directory."""
    root = audit_dir.resolve()
    deleted = []
    for entry in report.get("records") or []:
        if not entry.get("expired"):
            continue
        name = str(entry.get("file", ""))
        candidate = (audit_dir / name).resolve()
        if candidate.parent != root or candidate.suffix.lower() != ".json":
            raise ValueError(f"unsafe audit cleanup target: {name}")
        if candidate.exists():
            candidate.unlink()
            deleted.append(name)
    return deleted
