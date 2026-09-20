"""Scan Claim/Evidence history and optionally delete records past retention."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import CLAIM_EVIDENCE_RETENTION_DAYS, INDEX_DIR
from modules.evidence_audit import (
    load_index_evidence,
    prune_expired_audits,
    scan_evidence_audits,
    write_audit_scan_report,
)


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index-dir", type=Path, default=INDEX_DIR)
    parser.add_argument(
        "--retention-days",
        type=_nonnegative_int,
        default=CLAIM_EVIDENCE_RETENTION_DAYS,
        help="Records at least this old are expired; 0 retains indefinitely",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete expired records; omission performs a safe preview",
    )
    args = parser.parse_args()

    chunks, generation = load_index_evidence(args.index_dir)
    audit_dir = args.index_dir / "claim_evidence"
    report = scan_evidence_audits(
        audit_dir,
        chunks,
        current_generation_id=generation,
        retention_days=args.retention_days,
    )
    deleted = prune_expired_audits(report, audit_dir) if args.apply else []
    if deleted:
        report = scan_evidence_audits(
            audit_dir,
            chunks,
            current_generation_id=generation,
            retention_days=args.retention_days,
        )
    report["cleanup"] = {
        "mode": "apply" if args.apply else "preview",
        "deleted_count": len(deleted),
        "deleted_files": deleted,
    }
    report_path = write_audit_scan_report(report, args.index_dir)
    output = {**report, "report_path": str(report_path)}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
