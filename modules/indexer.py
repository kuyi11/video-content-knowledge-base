import json
import gc
import hashlib
import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import faiss
import jieba
import numpy as np
from modules.embedding_runtime import SentenceTransformer
from whoosh.analysis import StopFilter, Token, Tokenizer
from whoosh.fields import ID, TEXT, Schema
from whoosh.index import create_in, open_dir
from whoosh.qparser import MultifieldParser

from config import EMBEDDING_BATCH_SIZE, TEMP_DIR
from modules.document_identity import make_document_id
from modules.file_lock import FileLock

logger = logging.getLogger(__name__)
from modules.chunk_splitter import CHUNKING_VERSION, Chunk, ChunkSplitter
from modules.vault_loader import Document

JIEBA_CACHE_DIR = TEMP_DIR / "jieba"
JIEBA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
jieba.dt.tmp_dir = str(JIEBA_CACHE_DIR)
JIEBA_DICT = Path(jieba.__file__).resolve().parent / "dict.txt"
if JIEBA_DICT.exists():
    jieba.set_dictionary(str(JIEBA_DICT))

STOP_WORDS = {
    "的", "是", "在", "了", "和", "就", "都", "也", "还", "这", "那",
    "有", "不", "到", "着", "说", "个", "我", "你", "他", "她", "它",
    "们", "很", "要", "会", "能", "把", "被", "让", "对",
}


class JiebaTokenizer(Tokenizer):
    def __call__(self, value, start_pos=0, start_char=0, mode="", **kwargs):
        pos = 0
        for word in jieba.cut(value):
            t = Token()
            t.text = word
            t.original = word
            t.pos = pos
            t.startchar = start_char + value.find(word, pos)
            t.endchar = t.startchar + len(word)
            pos += 1
            yield t


WHOOSH_SCHEMA = Schema(
    chunk_id=ID(stored=True, unique=True),
    document_id=ID(stored=True),
    video_id=ID(stored=True),
    profile=ID(stored=True),
    content=TEXT(stored=True, analyzer=JiebaTokenizer() | StopFilter(stoplist=STOP_WORDS)),
    section=ID(stored=True),
    source_path=ID(stored=True),
    chunk_type=ID(stored=True),
    domain=ID(stored=True),
    quality=ID(stored=True),
    review_status=ID(stored=True),
    source_of_truth=ID(stored=True),
    risk_level=ID(stored=True),
    answer_policy=ID(stored=True),
    source_refs=TEXT(stored=True),
)


@dataclass
class SearchResult:
    rank: int
    chunk_id: str
    video_id: str
    profile: str
    source_url: str
    content: str
    section: str
    start: float
    end: float
    score: float
    has_timestamp: bool = True
    source_path: str = ""
    chunk_type: str = "document"
    domain: str = ""
    quality: str = ""
    review_status: str = ""
    source_of_truth: bool | None = None
    risk_level: str = "low"
    answer_policy: str = ""
    source_refs: List[str] = None


class HybridIndex:
    INDEX_VERSION = 5
    FILTER_FIELDS = frozenset({
        "video_id", "profile", "chunk_type", "domain", "quality",
        "review_status", "source_of_truth", "risk_level",
    })
    MAX_FILTER_SCAN = 10000

    def __init__(self, index_dir: Path, model: SentenceTransformer, *, load_index: bool = True):
        self.index_dir = Path(index_dir)
        self.vector_dir = self.index_dir / "vector"
        self.keyword_dir = self.index_dir / "keyword" / "whoosh"
        self.model = model
        self.faiss_index: Optional[faiss.Index] = None
        self.id_map: Dict[int, str] = {}
        self.chunks: Dict[str, Chunk] = {}
        self.whoosh_ix = None
        self._lock_path = self.index_dir / ".index.lock"

        with FileLock(self._lock_path):
            self.index_dir.mkdir(parents=True, exist_ok=True)
            self._recover_interrupted_swap()
            self.vector_dir.mkdir(parents=True, exist_ok=True)
            self.keyword_dir.mkdir(parents=True, exist_ok=True)
            self._needs_build = not self._load_if_exists() if load_index else True

    def _load_if_exists(self) -> bool:
        manifest_path = self.vector_dir / "manifest.json"
        kw_manifest_path = self.keyword_dir / "manifest.json"
        faiss_path = str(self.vector_dir / "faiss.index")
        id_map_path = self.vector_dir / "id_map.json"
        chunks_path = self.vector_dir / "chunks.json"

        if not (
            Path(faiss_path).exists()
            and id_map_path.exists()
            and chunks_path.exists()
            and manifest_path.exists()
            and kw_manifest_path.exists()
            and self.keyword_dir.exists()
        ):
            return False

        try:
            vm = json.loads(manifest_path.read_text(encoding="utf-8"))
            km = json.loads(kw_manifest_path.read_text(encoding="utf-8"))
            if vm.get("version") != self.INDEX_VERSION or km.get("version") != self.INDEX_VERSION:
                logger.info("Index format changed, rebuild required")
                return False
            if (
                vm.get("chunking_version") != CHUNKING_VERSION
                or km.get("chunking_version") != CHUNKING_VERSION
            ):
                logger.info("Chunking strategy changed, rebuild required")
                return False
            if vm.get("chunk_count") != km.get("chunk_count") or vm.get("generation_id") != km.get("generation_id"):
                logger.warning("FAISS/Whoosh manifest mismatch, need rebuild")
                return False
            if vm.get("model_id") != self._model_id():
                logger.warning("Embedding model changed, need rebuild")
                return False
        except (OSError, json.JSONDecodeError, KeyError, TypeError, AttributeError):
            return False

        try:
            self.faiss_index = faiss.read_index(faiss_path)
            with id_map_path.open(encoding="utf-8") as id_map_file:
                self.id_map = {int(k): v for k, v in json.load(id_map_file).items()}
            payload = json.loads(chunks_path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise ValueError("chunks.json must contain a list")
            restored = [self._chunk_from_dict(item) for item in payload]
            self.chunks = {c.chunk_id: c for c in restored}
            expected_dim = self._model_dimension()
            if vm.get("embedding_dim") != self.faiss_index.d:
                raise RuntimeError("FAISS dimension does not match manifest")
            if expected_dim is not None and expected_dim != self.faiss_index.d:
                raise RuntimeError("FAISS dimension does not match embedding model")
            if self.faiss_index.ntotal != len(self.id_map) or len(self.id_map) != len(self.chunks):
                raise RuntimeError("FAISS/id_map/chunk count mismatch")
            if set(self.id_map.values()) != set(self.chunks):
                raise RuntimeError("FAISS id_map/chunk IDs mismatch")
            self.whoosh_ix = open_dir(str(self.keyword_dir))
            with self.whoosh_ix.searcher() as searcher:
                if searcher.doc_count() != len(self.chunks):
                    raise RuntimeError("Whoosh/chunk count mismatch")
            return True
        except Exception as exc:
            logger.warning("Invalid persisted index, need rebuild: %s", exc)
            self._clear_loaded_state()
            return False

    def _clear_loaded_state(self):
        if self.whoosh_ix is not None:
            try:
                self.whoosh_ix.close()
            except Exception:
                pass
        self.whoosh_ix = None
        self.faiss_index = None
        self.id_map = {}
        self.chunks = {}

    def _model_dimension(self) -> int | None:
        getter = getattr(self.model, "get_embedding_dimension", None)
        if getter is None:
            getter = getattr(self.model, "get_sentence_embedding_dimension", None)
        if getter is None:
            return None
        try:
            value = getter()
            return int(value) if value is not None else None
        except (TypeError, ValueError, AttributeError):
            return None

    def _model_id(self) -> str:
        model_path = (
            getattr(self.model, "model_name_or_path", None)
            or getattr(self.model, "_model_name", None)
            or getattr(self.model, "model_name", None)
        )
        if model_path is None:
            from config import BGE_MODEL_PATH

            model_path = BGE_MODEL_PATH
        model_path_text = str(model_path).rstrip("\\/")
        model_name = Path(model_path_text).name or model_path_text
        config_path = Path(model_path_text) / "config.json"
        config_digest = ""
        try:
            config_digest = hashlib.sha256(config_path.read_bytes()).hexdigest()[:16]
        except OSError:
            pass
        suffix = f"@{config_digest}" if config_digest else ""
        return f"{type(self.model).__name__}:{model_name}{suffix}"

    def _manifest(self, chunks: List[Chunk], dim: int = None, generation_id: str = None) -> dict:
        if generation_id is None:
            generation_id = self._new_generation_id()
        inventory = {}
        for chunk in chunks:
            key = chunk.document_id or chunk.chunk_id
            inventory[key] = {
                "document_id": chunk.document_id,
                "source_path": chunk.source_path,
                "content_hash": getattr(chunk, "content_hash", ""),
                "chunk_type": chunk.chunk_type,
                "domain": chunk.domain,
                "quality": chunk.quality,
                "review_status": chunk.review_status,
                "source_of_truth": chunk.source_of_truth,
            }
        m = {
            "version": self.INDEX_VERSION,
            "chunk_count": len(chunks),
            "model": type(self.model).__name__,
            "model_id": self._model_id(),
            "chunking_version": CHUNKING_VERSION,
            "generation_id": generation_id,
            "source_inventory": sorted(inventory.values(), key=lambda item: item["document_id"]),
        }
        if dim is not None:
            m["embedding_dim"] = dim
        return m

    def _new_generation_id(self) -> str:
        return str(time.time_ns())

    def build(self, documents: List[Document], force: bool = False):
        with FileLock(self._lock_path):
            if not force and not self._needs_build:
                logger.info("[SKIP] Index already exists")
                return
            splitter = ChunkSplitter(self.model)
            chunks = splitter.split(documents)
            logger.info("[BUILD] %d chunks from %d documents", len(chunks), len(documents))
            self._build_atomic(chunks)
            self.write_health_report(documents)

    def write_health_report(self, documents: List[Document]) -> dict:
        report = self.health_check(documents)
        report["checked_at_ns"] = time.time_ns()
        path = self.index_dir / "index-health.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(path))
        return report

    def health_check(self, documents: Optional[List[Document]] = None) -> dict:
        """Compare persisted index metadata with the current source documents."""
        manifest_path = self.vector_dir / "manifest.json"
        report = {
            "status": "unavailable",
            "index_exists": all(
                path.exists()
                for path in (
                    self.vector_dir / "faiss.index",
                    self.vector_dir / "chunks.json",
                    self.vector_dir / "id_map.json",
                    self.keyword_dir / "manifest.json",
                )
            ),
            "indexed_chunk_count": len(self.chunks),
            "indexed_document_count": len({c.document_id for c in self.chunks.values()}),
            "missing_from_index": [],
            "extra_in_index": [],
            "changed_sources": [],
            "metadata_errors": [],
        }
        if not report["index_exists"]:
            report["metadata_errors"].append("missing index artifacts")
        if not manifest_path.exists():
            report["metadata_errors"].append("missing vector manifest")
            report["status"] = "stale"
            return report
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            keyword_manifest = json.loads(
                (self.keyword_dir / "manifest.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            report["metadata_errors"].append(f"invalid manifest: {exc}")
            report["status"] = "stale"
            return report
        if not isinstance(manifest, dict) or not isinstance(keyword_manifest, dict):
            report["metadata_errors"].append("manifest must be a JSON object")
            report["status"] = "stale"
            return report
        inventory = manifest.get("source_inventory", [])
        if not isinstance(inventory, list):
            report["metadata_errors"].append("source inventory must be a list")
            report["status"] = "stale"
            return report
        indexed = {
            item.get("document_id"): item
            for item in inventory
            if isinstance(item, dict) and item.get("document_id")
        }
        report["indexed_chunk_count"] = int(manifest.get("chunk_count", 0))
        report["indexed_document_count"] = len(indexed)
        for key in ("version", "chunking_version", "generation_id", "chunk_count", "model_id", "embedding_dim"):
            if manifest.get(key) != keyword_manifest.get(key):
                report["metadata_errors"].append(f"vector/keyword {key} mismatch")
        if manifest.get("version") != self.INDEX_VERSION:
            report["metadata_errors"].append(
                f"index version {manifest.get('version')} != expected {self.INDEX_VERSION}"
            )
        if manifest.get("chunking_version") != CHUNKING_VERSION:
            report["metadata_errors"].append(
                "chunking version "
                f"{manifest.get('chunking_version')} != expected {CHUNKING_VERSION}"
            )
        if not manifest.get("source_inventory"):
            report["metadata_errors"].append("missing source inventory")
        current = {}
        expected_chunks = ChunkSplitter(self.model).split(documents or [])
        for chunk in expected_chunks:
            current[chunk.document_id] = {
                "document_id": chunk.document_id,
                "source_path": chunk.source_path,
                "content_hash": chunk.content_hash,
                "chunk_type": chunk.chunk_type,
                "domain": chunk.domain,
                "quality": chunk.quality,
                "review_status": chunk.review_status,
                "source_of_truth": chunk.source_of_truth,
            }
        if documents is not None:
            report["missing_from_index"] = sorted(set(current) - set(indexed))
            report["extra_in_index"] = sorted(set(indexed) - set(current))
            for document_id in sorted(set(current) & set(indexed)):
                current_record = current[document_id]
                indexed_record = indexed[document_id]
                # A Docker volume can expose the same source under a different
                # absolute path. Identity and content metadata remain stable.
                comparable_fields = (
                    "document_id",
                    "content_hash",
                    "chunk_type",
                    "domain",
                    "quality",
                    "review_status",
                    "source_of_truth",
                )
                if any(
                    current_record.get(field) != indexed_record.get(field)
                    for field in comparable_fields
                ):
                    report["changed_sources"].append(document_id)
            report["current_document_count"] = len(current)
        report["status"] = "ok" if not any(
            (report["missing_from_index"], report["extra_in_index"], report["changed_sources"], report["metadata_errors"])
        ) else "stale"
        return report

    def add_documents(self, documents: List[Document]):
        with FileLock(self._lock_path):
            self._add_documents_locked(documents)

    def _add_documents_locked(self, documents: List[Document]):
        if not documents:
            logger.info("[ADD] No documents supplied, skipping")
            return
        if self.faiss_index is None or self.whoosh_ix is None:
            raise RuntimeError("Index not built. Call build() first.")

        splitter = ChunkSplitter(self.model)
        document_ids = {d.document_id for d in documents}
        replacement_chunks = splitter.split(documents)
        tmp_vector, tmp_keyword = self._prepare_tmp_dirs(create_keyword=False)
        swap_completed = False

        try:
            staged_index = self._mutable_faiss_for_update()
            staged_id_map = dict(self.id_map)
            remove_vector_ids = [
                vector_id for vector_id, chunk_id in staged_id_map.items()
                if self.chunks.get(chunk_id) and self.chunks[chunk_id].document_id in document_ids
            ]

            if remove_vector_ids:
                removed = staged_index.remove_ids(np.asarray(remove_vector_ids, dtype="int64"))
                if int(removed) != len(remove_vector_ids):
                    raise RuntimeError("FAISS remove_ids did not remove all stale chunks")
                for vector_id in remove_vector_ids:
                    staged_id_map.pop(vector_id, None)

            if replacement_chunks:
                embs = self._encode_chunks(replacement_chunks)
                new_vector_ids = self._next_vector_ids(len(replacement_chunks), staged_id_map)
                staged_index.add_with_ids(embs, new_vector_ids)
                for vector_id, chunk in zip(new_vector_ids.tolist(), replacement_chunks):
                    staged_id_map[int(vector_id)] = chunk.chunk_id

            remaining_chunks = [c for c in self.chunks.values() if c.document_id not in document_ids]
            all_chunks = remaining_chunks + replacement_chunks
            chunks_dict = {c.chunk_id: c for c in all_chunks}
            stored_chunks = list(chunks_dict.values())
            generation_id = self._new_generation_id()

            self._write_vector_store(staged_index, staged_id_map, stored_chunks, tmp_vector, generation_id)
            self._copy_and_update_keyword_store(
                tmp_keyword,
                document_ids,
                replacement_chunks,
                stored_chunks,
                generation_id,
                staged_index.d,
            )
            self._validate_staged_index(tmp_vector, tmp_keyword, stored_chunks, staged_id_map)

            self._close_whoosh()
            self._atomic_swap()
            swap_completed = True

            self.faiss_index = staged_index
            self.id_map = staged_id_map
            self.chunks = chunks_dict
            self.whoosh_ix = open_dir(str(self.keyword_dir))

            logger.info("[UPSERT] %d documents (total: %d chunks)", len(documents), len(stored_chunks))

        except Exception as e:
            shutil.rmtree(str(tmp_vector), ignore_errors=True)
            shutil.rmtree(str(tmp_keyword), ignore_errors=True)
            if not swap_completed:
                self._reload_faiss_from_disk()
            self._reopen_whoosh_if_available()
            raise RuntimeError(f"Incremental index update failed: {e}") from e

    def _build_atomic(self, chunks: List[Chunk]):
        for chunk in chunks:
            if not chunk.document_id:
                chunk.document_id = make_document_id(chunk.video_id, chunk.profile)
        tmp_vector, tmp_keyword = self._prepare_tmp_dirs(create_keyword=True)

        try:
            embs = self._encode_chunks(chunks)
            dim = embs.shape[1]
            faiss_index = self._new_faiss_index(dim)
            vector_ids = np.arange(len(chunks), dtype="int64")
            faiss_index.add_with_ids(embs, vector_ids)
            id_map = {int(i): c.chunk_id for i, c in zip(vector_ids.tolist(), chunks)}
            chunks_dict = {c.chunk_id: c for c in chunks}
            stored_chunks = list(chunks_dict.values())

            generation_id = self._new_generation_id()
            self._write_vector_store(faiss_index, id_map, stored_chunks, tmp_vector, generation_id)
            self._write_full_keyword_store(stored_chunks, tmp_keyword, generation_id, dim)
            self._validate_staged_index(tmp_vector, tmp_keyword, stored_chunks, id_map)
            gc.collect()

            self._close_whoosh()
            self._atomic_swap()

            self.faiss_index = faiss_index
            self.id_map = id_map
            self.chunks = chunks_dict
            self.whoosh_ix = open_dir(str(self.keyword_dir))

            logger.info("[FAISS] %d vectors indexed", len(stored_chunks))
            logger.info("[Whoosh] %d documents indexed", len(stored_chunks))

        except Exception as e:
            shutil.rmtree(str(tmp_vector), ignore_errors=True)
            shutil.rmtree(str(tmp_keyword), ignore_errors=True)
            self._reopen_whoosh_if_available()
            raise RuntimeError(f"Atomic build failed: {e}") from e

    def _prepare_tmp_dirs(self, create_keyword: bool):
        self._recover_interrupted_swap()
        tmp_vector = self.index_dir / ".tmp_vector"
        tmp_keyword = self.index_dir / ".tmp_keyword"
        shutil.rmtree(str(tmp_vector), ignore_errors=True)
        shutil.rmtree(str(tmp_keyword), ignore_errors=True)
        tmp_vector.mkdir(parents=True, exist_ok=True)
        if create_keyword:
            tmp_keyword.mkdir(parents=True, exist_ok=True)
        return tmp_vector, tmp_keyword

    def _encode_chunks(self, chunks: List[Chunk]) -> np.ndarray:
        if not chunks:
            raise RuntimeError("No chunks to index")
        texts = [c.content for c in chunks]
        embs = self.model.encode(texts, normalize_embeddings=True, batch_size=EMBEDDING_BATCH_SIZE)
        embs = np.asarray(embs, dtype="float32")
        if embs.ndim != 2 or embs.shape[0] != len(chunks):
            raise RuntimeError("Embedding model returned invalid shape")
        return embs

    def _new_faiss_index(self, dim: int):
        return faiss.IndexIDMap2(faiss.IndexFlatIP(dim))

    def _copy_faiss_as_id_map(self):
        if self.faiss_index is None:
            raise RuntimeError("FAISS index is not loaded")
        cloned = faiss.clone_index(self.faiss_index)
        if isinstance(cloned, (faiss.IndexIDMap, faiss.IndexIDMap2)):
            return cloned
        return self._convert_flat_to_id_map(cloned)

    def _mutable_faiss_for_update(self):
        if self.faiss_index is None:
            raise RuntimeError("FAISS index is not loaded")
        if isinstance(self.faiss_index, (faiss.IndexIDMap, faiss.IndexIDMap2)):
            return self.faiss_index
        return self._copy_faiss_as_id_map()

    def _reload_faiss_from_disk(self):
        path = self.vector_dir / "faiss.index"
        if not path.exists():
            return
        try:
            self.faiss_index = faiss.read_index(str(path))
        except (OSError, RuntimeError):
            self.faiss_index = None

    def _convert_flat_to_id_map(self, index):
        converted = self._new_faiss_index(index.d)
        vectors = []
        ids = []
        for vector_id in sorted(self.id_map):
            if 0 <= vector_id < index.ntotal:
                vectors.append(index.reconstruct(int(vector_id)))
                ids.append(vector_id)
        if len(ids) != len(self.id_map):
            raise RuntimeError("Legacy FAISS/id_map mismatch")
        if vectors:
            converted.add_with_ids(np.asarray(vectors, dtype="float32"), np.asarray(ids, dtype="int64"))
        return converted

    def _next_vector_ids(self, count: int, id_map: Dict[int, str]) -> np.ndarray:
        start = max(id_map.keys(), default=-1) + 1
        return np.arange(start, start + count, dtype="int64")

    def _write_vector_store(self, faiss_index, id_map: Dict[int, str], chunks: List[Chunk], vector_dir: Path, generation_id: str):
        vector_dir.mkdir(parents=True, exist_ok=True)
        faiss.write_index(faiss_index, str(vector_dir / "faiss.index"))
        with (vector_dir / "id_map.json").open("w", encoding="utf-8") as f:
            json.dump({str(k): v for k, v in sorted(id_map.items())}, f, ensure_ascii=False, indent=2)
        (vector_dir / "chunks.json").write_text(
            json.dumps([self._chunk_to_dict(chunk) for chunk in chunks], ensure_ascii=False),
            encoding="utf-8",
        )
        manifest = self._manifest(chunks, faiss_index.d, generation_id=generation_id)
        manifest["vector_count"] = int(faiss_index.ntotal)
        manifest["id_map_count"] = len(id_map)
        manifest["index_type"] = type(faiss_index).__name__
        (vector_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _chunk_to_dict(chunk: Chunk) -> dict:
        return {
            "chunk_id": chunk.chunk_id,
            "video_id": chunk.video_id,
            "source_url": chunk.source_url,
            "content": chunk.content,
            "section": chunk.section,
            "start": chunk.start,
            "end": chunk.end,
            "has_timestamp": chunk.has_timestamp,
            "document_id": chunk.document_id,
            "profile": chunk.profile,
            "source_path": chunk.source_path,
            "chunk_type": chunk.chunk_type,
            "domain": chunk.domain,
            "quality": chunk.quality,
            "review_status": chunk.review_status,
            "source_of_truth": chunk.source_of_truth,
            "risk_level": chunk.risk_level,
            "answer_policy": chunk.answer_policy,
            "source_refs": list(chunk.source_refs or []),
            "raw_source_path": chunk.raw_source_path,
            "content_hash": chunk.content_hash,
        }

    @staticmethod
    def _chunk_from_dict(payload: dict) -> Chunk:
        if not isinstance(payload, dict):
            raise ValueError("chunk entry must be an object")
        required = {"chunk_id", "video_id", "content", "section"}
        if not required.issubset(payload):
            raise ValueError("chunk entry is missing required fields")
        return Chunk(
            chunk_id=str(payload["chunk_id"]),
            video_id=str(payload["video_id"]),
            source_url=str(payload.get("source_url", "")),
            content=str(payload["content"]),
            section=str(payload["section"]),
            start=float(payload.get("start", 0.0)),
            end=float(payload.get("end", 0.0)),
            has_timestamp=bool(payload.get("has_timestamp", True)),
            document_id=str(payload.get("document_id", "")),
            profile=str(payload.get("profile", "default")),
            source_path=str(payload.get("source_path", "")),
            chunk_type=str(payload.get("chunk_type", "document")),
            domain=str(payload.get("domain", "")),
            quality=str(payload.get("quality", "")),
            review_status=str(payload.get("review_status", "")),
            source_of_truth=payload.get("source_of_truth"),
            risk_level=str(payload.get("risk_level", "low")),
            answer_policy=str(payload.get("answer_policy", "")),
            source_refs=[str(item) for item in payload.get("source_refs", [])],
            raw_source_path=str(payload.get("raw_source_path", "")),
            content_hash=str(payload.get("content_hash", "")),
        )

    def _write_full_keyword_store(
        self,
        chunks: List[Chunk],
        keyword_dir: Path,
        generation_id: str,
        embedding_dim: int,
    ):
        keyword_dir.mkdir(parents=True, exist_ok=True)
        whoosh_ix = create_in(str(keyword_dir), WHOOSH_SCHEMA)
        writer = whoosh_ix.writer()
        try:
            for c in chunks:
                writer.add_document(
                    chunk_id=c.chunk_id, document_id=c.document_id,
                    video_id=c.video_id, profile=c.profile,
                    content=c.content, section=c.section,
                    source_path=c.source_path, chunk_type=c.chunk_type, domain=c.domain,
                    quality=c.quality, review_status=c.review_status,
                    source_of_truth=str(c.source_of_truth).lower(), risk_level=c.risk_level,
                    answer_policy=c.answer_policy, source_refs=" ".join(c.source_refs or []),
                )
            writer.commit()
        finally:
            whoosh_ix.close()
        self._write_keyword_manifest(keyword_dir, chunks, generation_id, embedding_dim)

    def _copy_and_update_keyword_store(
        self,
        keyword_dir: Path,
        remove_document_ids: set[str],
        add_chunks: List[Chunk],
        all_chunks: List[Chunk],
        generation_id: str,
        embedding_dim: int,
    ):
        self._clone_keyword_store(keyword_dir)
        whoosh_ix = open_dir(str(keyword_dir))
        writer = whoosh_ix.writer()
        try:
            for document_id in remove_document_ids:
                writer.delete_by_term("document_id", document_id)
            for c in add_chunks:
                writer.update_document(
                    chunk_id=c.chunk_id, document_id=c.document_id,
                    video_id=c.video_id, profile=c.profile,
                    content=c.content, section=c.section,
                    source_path=c.source_path, chunk_type=c.chunk_type, domain=c.domain,
                    quality=c.quality, review_status=c.review_status,
                    source_of_truth=str(c.source_of_truth).lower(), risk_level=c.risk_level,
                    answer_policy=c.answer_policy, source_refs=" ".join(c.source_refs or []),
                )
            writer.commit()
        finally:
            whoosh_ix.close()
        self._write_keyword_manifest(keyword_dir, all_chunks, generation_id, embedding_dim)

    def _clone_keyword_store(self, keyword_dir: Path):
        """Clone immutable Whoosh segments cheaply, with a copy fallback."""
        keyword_dir.mkdir(parents=True, exist_ok=True)
        for source in self.keyword_dir.iterdir():
            if source.name == "manifest.json" or "lock" in source.name.lower():
                continue
            target = keyword_dir / source.name
            if source.is_dir():
                shutil.copytree(str(source), str(target))
                continue
            try:
                os.link(str(source), str(target))
            except OSError:
                shutil.copy2(str(source), str(target))

    def _write_keyword_manifest(
        self,
        keyword_dir: Path,
        chunks: List[Chunk],
        generation_id: str,
        embedding_dim: int,
    ):
        manifest = self._manifest(chunks, dim=embedding_dim, generation_id=generation_id)
        (keyword_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    def _validate_staged_index(self, vector_dir: Path, keyword_dir: Path, chunks: List[Chunk], id_map: Dict[int, str]):
        if len(id_map) != len(chunks):
            raise RuntimeError("FAISS id_map/chunk count mismatch")
        if set(id_map.values()) != {c.chunk_id for c in chunks}:
            raise RuntimeError("FAISS id_map/chunk IDs mismatch")

        test_faiss = faiss.read_index(str(vector_dir / "faiss.index"))
        if test_faiss.ntotal != len(chunks):
            raise RuntimeError("FAISS validation failed")
        expected_dim = self._model_dimension()
        if expected_dim is not None and test_faiss.d != expected_dim:
            raise RuntimeError("FAISS dimension does not match embedding model")

        test_whoosh = open_dir(str(keyword_dir))
        try:
            with test_whoosh.searcher() as searcher:
                if searcher.doc_count() != len(chunks):
                    raise RuntimeError("Whoosh validation failed")
        finally:
            test_whoosh.close()

        self._validate_manifest_pair(vector_dir / "manifest.json", keyword_dir / "manifest.json")

    def _validate_manifest_pair(self, vector_manifest: Path, keyword_manifest: Path):
        vm = json.loads(vector_manifest.read_text(encoding="utf-8"))
        km = json.loads(keyword_manifest.read_text(encoding="utf-8"))
        for key in (
            "version",
            "chunking_version",
            "generation_id",
            "chunk_count",
            "model_id",
            "embedding_dim",
        ):
            if vm.get(key) != km.get(key):
                raise RuntimeError(f"FAISS/Whoosh manifest {key} mismatch")

    def _close_whoosh(self):
        if self.whoosh_ix is not None:
            try:
                self.whoosh_ix.close()
            finally:
                self.whoosh_ix = None

    def _reopen_whoosh_if_available(self):
        if self.whoosh_ix is None and self.keyword_dir.exists():
            try:
                self.whoosh_ix = open_dir(str(self.keyword_dir))
            except Exception:
                self.whoosh_ix = None

    def _atomic_swap(self):
        """Commit staged vector and keyword stores with a recoverable journal."""
        tmp_vector = self.index_dir / ".tmp_vector"
        tmp_keyword = self.index_dir / ".tmp_keyword"
        if not tmp_vector.exists() or not tmp_keyword.exists():
            raise RuntimeError("Missing staged index directories")

        swap_id = self._new_generation_id()
        vector_bak = self.index_dir / f".bak_vector_{swap_id}"
        keyword_bak = self.index_dir / f".bak_keyword_{swap_id}"
        journal = {
            "stage": "started",
            "vector_bak": str(vector_bak),
            "keyword_bak": str(keyword_bak),
            "tmp_vector": str(tmp_vector),
            "tmp_keyword": str(tmp_keyword),
        }
        self._write_swap_journal(journal)

        try:
            if self.vector_dir.exists():
                os.rename(str(self.vector_dir), str(vector_bak))
            journal["stage"] = "vector_backed_up"
            self._write_swap_journal(journal)

            if self.keyword_dir.exists():
                os.rename(str(self.keyword_dir), str(keyword_bak))
            journal["stage"] = "keyword_backed_up"
            self._write_swap_journal(journal)

            os.rename(str(tmp_vector), str(self.vector_dir))
            journal["stage"] = "vector_live"
            self._write_swap_journal(journal)

            os.rename(str(tmp_keyword), str(self.keyword_dir))
            journal["stage"] = "swapped"
            self._write_swap_journal(journal)
            self._validate_manifest_pair(self.vector_dir / "manifest.json", self.keyword_dir / "manifest.json")
        except Exception as e:
            self._rollback_swap(journal)
            raise RuntimeError(f"Atomic swap failed: {e}") from e

        shutil.rmtree(str(vector_bak), ignore_errors=True)
        shutil.rmtree(str(keyword_bak), ignore_errors=True)
        self._swap_journal_path().unlink(missing_ok=True)

    def _swap_journal_path(self) -> Path:
        return self.index_dir / ".swap_journal.json"

    def _write_swap_journal(self, journal: dict):
        path = self._swap_journal_path()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(journal, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(path))

    def _recover_interrupted_swap(self):
        path = self._swap_journal_path()
        if not path.exists():
            return
        try:
            journal = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            path.unlink(missing_ok=True)
            return

        if journal.get("stage") == "swapped":
            shutil.rmtree(journal.get("vector_bak", ""), ignore_errors=True)
            shutil.rmtree(journal.get("keyword_bak", ""), ignore_errors=True)
            shutil.rmtree(journal.get("tmp_vector", ""), ignore_errors=True)
            shutil.rmtree(journal.get("tmp_keyword", ""), ignore_errors=True)
            path.unlink(missing_ok=True)
            return

        self._rollback_swap(journal)

    def _rollback_swap(self, journal: dict):
        vector_bak = Path(journal.get("vector_bak", ""))
        keyword_bak = Path(journal.get("keyword_bak", ""))

        if vector_bak.exists():
            shutil.rmtree(str(self.vector_dir), ignore_errors=True)
            os.rename(str(vector_bak), str(self.vector_dir))
        if keyword_bak.exists():
            shutil.rmtree(str(self.keyword_dir), ignore_errors=True)
            self.keyword_dir.parent.mkdir(parents=True, exist_ok=True)
            os.rename(str(keyword_bak), str(self.keyword_dir))

        shutil.rmtree(journal.get("tmp_vector", ""), ignore_errors=True)
        shutil.rmtree(journal.get("tmp_keyword", ""), ignore_errors=True)
        self._swap_journal_path().unlink(missing_ok=True)

    def query(
        self,
        text: str,
        top_k: int = 5,
        filters: Optional[dict] = None,
    ) -> List[SearchResult]:
        q_emb = self.model.encode([text], normalize_embeddings=True)
        with FileLock(self._lock_path):
            return self._query_locked(text, q_emb, top_k, filters=filters)

    def _query_locked(
        self,
        text: str,
        q_emb: np.ndarray,
        top_k: int,
        *,
        filters: Optional[dict] = None,
    ) -> List[SearchResult]:
        if self.faiss_index is None or self.whoosh_ix is None:
            raise RuntimeError("Index not built. Call build() first.")

        self._validate_filters(filters)
        if filters and len(self.chunks) > self.MAX_FILTER_SCAN:
            raise ValueError(
                f"filtered query exceeds maximum scan size ({self.MAX_FILTER_SCAN}); "
                "build a metadata-specific index or narrow the corpus"
            )
        candidate_k = len(self.chunks) if filters else max(12, top_k * 4)
        vec_hits = self._query_vector(q_emb, top_k=candidate_k)
        kw_hits = self._query_keyword(text, top_k=candidate_k)
        merged = self._rrf_fusion(vec_hits, kw_hits, k=60)
        if filters:
            merged = [
                (cid, score) for cid, score in merged
                if (chunk := self.chunks.get(cid)) and self._matches_filters(chunk, filters)
            ]
        unique = self._deduplicate(merged, top_k)

        results = []
        for i, (cid, score) in enumerate(unique):
            c = self.chunks.get(cid)
            if not c:
                continue
            results.append(
                SearchResult(
                    rank=i + 1, chunk_id=cid, video_id=c.video_id,
                    profile=c.profile,
                    source_url=c.source_url, content=c.content,
                    section=c.section, start=c.start, end=c.end,
                    score=round(score, 4), has_timestamp=c.has_timestamp,
                    source_path=c.source_path, chunk_type=c.chunk_type, domain=c.domain,
                    quality=c.quality, review_status=c.review_status,
                    source_of_truth=c.source_of_truth, risk_level=c.risk_level,
                    answer_policy=c.answer_policy, source_refs=list(c.source_refs or []),
                )
            )
        return results

    def _validate_filters(self, filters: Optional[dict]) -> None:
        if filters is None:
            return
        if not isinstance(filters, dict):
            raise ValueError("metadata filters must be a mapping")
        unknown = sorted(set(filters) - self.FILTER_FIELDS)
        if unknown:
            raise ValueError(f"unsupported metadata filter: {unknown[0]}")

    @staticmethod
    def _matches_filters(chunk: Chunk, filters: dict) -> bool:
        for field, expected in filters.items():
            accepted = expected if isinstance(expected, (list, tuple, set, frozenset)) else (expected,)
            if getattr(chunk, field) not in accepted:
                return False
        return True

    def _query_vector(self, embedding: np.ndarray, top_k: int):
        D, I = self.faiss_index.search(embedding.astype("float32"), top_k)
        return [(self.id_map[int(i)], float(D[0][j]))
                for j, i in enumerate(I[0]) if i >= 0 and int(i) in self.id_map]

    def _query_keyword(self, text: str, top_k: int):
        query_str = " ".join(jieba.cut(text))
        with self.whoosh_ix.searcher() as searcher:
            parser = MultifieldParser(["content", "section"], self.whoosh_ix.schema)
            query = parser.parse(query_str)
            results = searcher.search(query, limit=top_k)
            return [(r["chunk_id"], r.score) for r in results]

    def _rrf_fusion(self, vec_hits, kw_hits, k=60):
        scores: Dict[str, float] = {}
        for rank, (cid, _) in enumerate(vec_hits):
            scores[cid] = scores.get(cid, 0) + 1.0 / (k + rank + 1)
        for rank, (cid, _) in enumerate(kw_hits):
            scores[cid] = scores.get(cid, 0) + 1.0 / (k + rank + 1)
        if not scores:
            return []
        merged = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        max_score = merged[0][1]
        return [(cid, s / max_score) for cid, s in merged]

    def _deduplicate(self, merged, top_k):
        seen = set()
        seen_evidence = set()
        result = []
        for cid, score in merged:
            if cid in seen:
                continue
            seen.add(cid)
            chunk = self.chunks.get(cid)
            if chunk is None:
                continue
            if chunk.source_refs:
                evidence_key = (
                    chunk.video_id,
                    chunk.section.casefold(),
                    tuple(chunk.source_refs),
                )
            else:
                evidence_key = (
                    chunk.video_id,
                    chunk.section.casefold(),
                    " ".join(chunk.content.split()),
                )
            if evidence_key in seen_evidence:
                continue
            seen_evidence.add(evidence_key)
            result.append((cid, score))
            if len(result) >= top_k:
                break
        return result
