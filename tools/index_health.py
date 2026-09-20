"""Compare the current vault inventory with the persisted hybrid index."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import EXTERNAL_VAULT_PATHS, INDEX_DIR, TEMP_DIR, VAULT_DIR
from modules.indexer import HybridIndex
from modules.vault_loader import VaultLoader


class InventoryModel:
    """Minimal model adapter: inventory checks do not need semantic embeddings."""

    model_name_or_path = "inventory-only"

    def encode(self, texts, normalize_embeddings=True, **kwargs):
        return [[0.0] for _ in texts]

    def get_sentence_embedding_dimension(self):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index-dir", type=Path, default=INDEX_DIR)
    parser.add_argument("--vault", action="append", type=Path, default=[])
    args = parser.parse_args()
    vaults = tuple(args.vault) or (VAULT_DIR, *EXTERNAL_VAULT_PATHS)
    model = InventoryModel()
    documents = VaultLoader(
        vaults, model, embed_segments=False, temp_dir=TEMP_DIR
    ).load_all()
    index = HybridIndex(args.index_dir, model=model, load_index=False)
    report = index.write_health_report(documents)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
