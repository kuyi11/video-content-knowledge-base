import json

from modules.evidence_audit import build_evidence_audit, persist_evidence_audit
from modules.evidence_model import build_evidence_units
from tools.manage_evidence_audits import main


CHUNK = {
    "chunk_id": "raw-1",
    "document_id": "video__raw",
    "video_id": "video",
    "content": "原始证据",
    "source_refs": ["S0001"],
    "chunk_type": "raw_transcript",
    "answer_policy": "direct",
}


def test_management_tool_defaults_to_preview(tmp_path, monkeypatch, capsys):
    vector_dir = tmp_path / "vector"
    vector_dir.mkdir(parents=True)
    (vector_dir / "chunks.json").write_text(json.dumps([CHUNK]), encoding="utf-8")
    (vector_dir / "manifest.json").write_text(
        json.dumps({"generation_id": "generation-1"}), encoding="utf-8"
    )
    unit = next(iter(build_evidence_units([CHUNK]).values())).to_dict()
    record = build_evidence_audit(
        question="敏感问题",
        answer="答案",
        grounding={"claims": [], "evidence_units": [unit]},
        output_gate={"action": "allow"},
        index_generation_id="generation-1",
    )
    persist_evidence_audit(record, tmp_path)

    monkeypatch.setattr(
        "sys.argv",
        ["manage_evidence_audits.py", "--index-dir", str(tmp_path), "--retention-days", "1"],
    )

    assert main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["cleanup"]["mode"] == "preview"
    assert output["cleanup"]["deleted_count"] == 0
