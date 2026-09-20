import json

from modules.evidence_audit import (
    build_evidence_audit,
    evaluate_evidence_freshness,
    prune_expired_audits,
    persist_evidence_audit,
    scan_evidence_audits,
    write_audit_scan_report,
)
from modules.evidence_model import build_evidence_units


CHUNK = {
    "chunk_id": "raw-1",
    "document_id": "video__raw",
    "video_id": "video",
    "content": "原始证据",
    "source_refs": ["S0001"],
    "chunk_type": "raw_transcript",
    "risk_level": "low",
    "review_status": "approved",
    "source_of_truth": True,
    "answer_policy": "direct",
}


def _record():
    unit = next(iter(build_evidence_units([CHUNK]).values())).to_dict()
    return build_evidence_audit(
        question="问题",
        answer="答案 [raw-1 | S0001]",
        grounding={
            "claims": [{"claim_id": "C0001", "evidence_unit_ids": ["E:raw-1"]}],
            "evidence_units": [unit],
        },
        output_gate={"action": "allow"},
        index_generation_id="generation-1",
    )


def test_persisted_audit_is_atomic_and_replayable(tmp_path):
    record = _record()

    path = persist_evidence_audit(record, tmp_path)

    assert path.parent == tmp_path / "claim_evidence"
    assert json.loads(path.read_text(encoding="utf-8"))["audit_id"] == record["audit_id"]
    assert not path.with_suffix(".tmp").exists()


def test_freshness_distinguishes_generation_change_from_evidence_change():
    record = _record()

    unchanged = evaluate_evidence_freshness(
        record, [CHUNK], current_generation_id="generation-2"
    )
    changed = evaluate_evidence_freshness(
        record, [{**CHUNK, "content": "已经修改"}], current_generation_id="generation-2"
    )
    missing = evaluate_evidence_freshness(
        record, [], current_generation_id="generation-2"
    )

    assert unchanged["status"] == "generation_changed"
    assert changed["status"] == "invalidated"
    assert changed["changed_evidence_unit_ids"] == ["E:raw-1"]
    assert missing["missing_evidence_unit_ids"] == ["E:raw-1"]


def test_evidence_fingerprint_includes_admission_policy_but_not_query_role():
    primary = next(iter(build_evidence_units([CHUNK]).values()))
    backtrace = next(
        iter(build_evidence_units([{**CHUNK, "retrieval_role": "raw_backtrace"}]).values())
    )
    restricted = next(
        iter(
            build_evidence_units(
                [{**CHUNK, "answer_policy": "summary_requires_raw_evidence"}]
            ).values()
        )
    )

    assert primary.evidence_fingerprint == backtrace.evidence_fingerprint
    assert primary.evidence_fingerprint != restricted.evidence_fingerprint


def test_audit_scan_is_non_sensitive_and_reports_affected_claims(tmp_path):
    audit_dir = tmp_path / "claim_evidence"
    record = _record()
    record["created_at_ns"] = 1
    persist_evidence_audit(record, tmp_path)

    report = scan_evidence_audits(
        audit_dir,
        [{**CHUNK, "content": "已经修改"}],
        current_generation_id="generation-2",
        retention_days=1,
        now_ns=86_400 * 1_000_000_000 * 3,
    )

    assert report["status"] == "attention_required"
    assert report["counts"]["invalidated"] == 1
    assert report["counts"]["expired"] == 1
    entry = report["records"][0]
    assert entry["affected_claim_ids"] == ["C0001"]
    assert "question" not in json.dumps(report, ensure_ascii=False)
    report_path = write_audit_scan_report(report, tmp_path)
    assert report_path.exists()


def test_prune_expired_audits_requires_explicit_apply_and_safe_scope(tmp_path):
    audit_dir = tmp_path / "claim_evidence"
    record = _record()
    record["created_at_ns"] = 1
    path = persist_evidence_audit(record, tmp_path)
    report = scan_evidence_audits(
        audit_dir,
        [CHUNK],
        current_generation_id="generation-1",
        retention_days=1,
        now_ns=86_400 * 1_000_000_000 * 3,
    )

    assert path.exists()
    assert prune_expired_audits(report, audit_dir) == [path.name]
    assert not path.exists()
