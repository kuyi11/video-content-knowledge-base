"""Rebuild the hybrid index from all configured vault sources."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import torch

from config import (
    BGE_MODEL_PATH,
    CLAIM_EVIDENCE_RETENTION_DAYS,
    EXTERNAL_VAULT_PATHS,
    INDEX_DIR,
    TEMP_DIR,
    VAULT_DIR,
)
from modules.embedding_runtime import SentenceTransformer
from modules.evidence_audit import (
    load_index_evidence,
    scan_evidence_audits,
    write_audit_scan_report,
)
from modules.indexer import HybridIndex
from modules.vault_loader import VaultLoader


def main() -> int:
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = SentenceTransformer(BGE_MODEL_PATH, device=device)
    documents = VaultLoader(
        (VAULT_DIR, *EXTERNAL_VAULT_PATHS), model, temp_dir=TEMP_DIR
    ).load_all()
    if not documents:
        raise RuntimeError("No indexable documents found")
    index = HybridIndex(INDEX_DIR, model=model)
    index.build(documents, force=True)
    report = index.write_health_report(documents)
    current_chunks, generation = load_index_evidence(INDEX_DIR)
    audit_report = scan_evidence_audits(
        INDEX_DIR / "claim_evidence",
        current_chunks,
        current_generation_id=generation,
        retention_days=CLAIM_EVIDENCE_RETENTION_DAYS,
    )
    audit_report_path = write_audit_scan_report(audit_report, INDEX_DIR)
    summary = {
        "device": device,
        "document_count": len(documents),
        "chunk_count": len(index.chunks),
        "chunk_types": dict(Counter(c.chunk_type for c in index.chunks.values())),
        "quality": dict(Counter(c.quality for c in index.chunks.values())),
        "health": report,
        "evidence_audit": {
            "status": audit_report["status"],
            "counts": audit_report["counts"],
            "report_path": str(audit_report_path),
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
