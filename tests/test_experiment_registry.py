import json
from pathlib import Path

from tools.validate_experiments import validate_document, validate_registry


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "eval" / "experiment_registry.json"
DOCUMENT_PATH = PROJECT_ROOT / "eval" / "EXPERIMENT_LOG.md"


def test_committed_experiment_registry_is_valid():
    records = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))

    assert validate_registry(records, PROJECT_ROOT) == []
    assert validate_document(records, DOCUMENT_PATH) == []


def test_registry_validator_detects_incorrect_delta(tmp_path):
    evidence = tmp_path / "baseline.json"
    evidence.write_text("{}", encoding="utf-8")
    record = {
        "id": "EXP-20260918-invalid-delta",
        "date": "2026-09-18",
        "title": "Invalid delta",
        "category": "retrieval",
        "design": "controlled_ab",
        "status": "needs_replication",
        "hypothesis": "Test validation",
        "corpus": {
            "document_count": 1,
            "chunk_count": 1,
            "question_count": 1,
            "answerable_count": 1,
        },
        "control": {"name": "control", "metrics": {"recall_at_5": 0.5}},
        "variant": {"name": "variant", "metrics": {"recall_at_5": 0.75}},
        "delta": {"recall_at_5": 0.5},
        "decision": "none",
        "limitations": ["synthetic fixture"],
        "evidence": ["baseline.json"],
        "commit": "2332690",
    }

    errors = validate_registry([record], tmp_path)

    assert any("expected 0.25" in error for error in errors)
