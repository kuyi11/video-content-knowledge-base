"""Check whether a persisted Claim/Evidence audit still matches the current index."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import INDEX_DIR
from modules.evidence_audit import evaluate_evidence_freshness, load_index_evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record", type=Path, help="Path to one claim_evidence audit JSON")
    parser.add_argument("--index-dir", type=Path, default=INDEX_DIR)
    args = parser.parse_args()

    record = json.loads(args.record.read_text(encoding="utf-8"))
    chunks, generation = load_index_evidence(args.index_dir)
    report = evaluate_evidence_freshness(
        record,
        chunks,
        current_generation_id=generation,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] in {"current", "generation_changed", "not_applicable"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
