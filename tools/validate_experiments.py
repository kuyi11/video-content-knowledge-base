"""Validate the machine-readable RAG experiment registry."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = PROJECT_ROOT / "eval" / "experiment_registry.json"
DEFAULT_DOCUMENT = PROJECT_ROOT / "eval" / "EXPERIMENT_LOG.md"
ID_RE = re.compile(r"^EXP-\d{8}-[a-z0-9-]+$")
COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
DESIGNS = {"controlled_ab", "historical_ab", "bundled_regression"}
STATUSES = {"adopted", "adopted_opt_in", "rejected_default", "needs_replication"}
REQUIRED_FIELDS = {
    "id",
    "date",
    "title",
    "category",
    "design",
    "status",
    "hypothesis",
    "corpus",
    "control",
    "variant",
    "delta",
    "decision",
    "limitations",
    "evidence",
    "commit",
}


def load_registry(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("experiment registry root must be a JSON array")
    return data


def validate_registry(records: list[dict], project_root: Path) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for position, record in enumerate(records, start=1):
        prefix = str(record.get("id") or f"record[{position}]")
        missing = sorted(REQUIRED_FIELDS - set(record))
        if missing:
            errors.append(f"{prefix}: missing fields: {', '.join(missing)}")
            continue
        experiment_id = record["id"]
        if not ID_RE.fullmatch(experiment_id):
            errors.append(f"{prefix}: invalid experiment id")
        if experiment_id in seen:
            errors.append(f"{prefix}: duplicate experiment id")
        seen.add(experiment_id)
        if record["design"] not in DESIGNS:
            errors.append(f"{prefix}: unsupported design {record['design']!r}")
        if record["status"] not in STATUSES:
            errors.append(f"{prefix}: unsupported status {record['status']!r}")
        if not COMMIT_RE.fullmatch(str(record["commit"])):
            errors.append(f"{prefix}: invalid commit hash")
        if not isinstance(record["limitations"], list) or not record["limitations"]:
            errors.append(f"{prefix}: limitations must be a non-empty list")

        corpus = record["corpus"]
        for field in ("document_count", "chunk_count", "question_count", "answerable_count"):
            if not isinstance(corpus.get(field), int) or corpus[field] < 1:
                errors.append(f"{prefix}: corpus.{field} must be a positive integer")

        control_metrics = record["control"].get("metrics", {})
        variant_metrics = record["variant"].get("metrics", {})
        for metric, claimed_delta in record["delta"].items():
            control_value = control_metrics.get(metric)
            variant_value = variant_metrics.get(metric)
            if not all(isinstance(value, (int, float)) for value in (control_value, variant_value, claimed_delta)):
                errors.append(f"{prefix}: {metric} delta requires numeric control and variant values")
                continue
            actual_delta = round(variant_value - control_value, 4)
            if abs(actual_delta - claimed_delta) > 0.0001:
                errors.append(
                    f"{prefix}: {metric} delta is {claimed_delta}, expected {actual_delta}"
                )

        evidence = record["evidence"]
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{prefix}: evidence must be a non-empty list")
        else:
            for relative_path in evidence:
                path = Path(relative_path)
                if path.is_absolute() or not (project_root / path).is_file():
                    errors.append(f"{prefix}: missing repository evidence {relative_path!r}")
    return errors


def validate_document(records: list[dict], document_path: Path) -> list[str]:
    text = document_path.read_text(encoding="utf-8")
    return [
        f"{record['id']}: missing from {document_path.name}"
        for record in records
        if record.get("id") and record["id"] not in text
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--document", type=Path, default=DEFAULT_DOCUMENT)
    args = parser.parse_args()

    records = load_registry(args.registry)
    errors = validate_registry(records, PROJECT_ROOT)
    errors.extend(validate_document(records, args.document))
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"Validated {len(records)} experiments: {args.registry}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
