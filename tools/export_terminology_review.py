"""Export the human terminology checklist as machine-readable JSON."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import TERMINOLOGY_REVIEW_PATH, TERMINOLOGY_REVIEW_REPORT
from modules.medical_safety import export_review_rules


def main() -> int:
    payload = export_review_rules(TERMINOLOGY_REVIEW_PATH, TERMINOLOGY_REVIEW_REPORT)
    print(json.dumps({key: value for key, value in payload.items() if key != "rules"}, ensure_ascii=False, indent=2))
    return 0 if payload["rule_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
