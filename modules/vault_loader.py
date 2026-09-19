import hashlib
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Iterable, List

import yaml
from modules.embedding_runtime import SentenceTransformer

from config import TEMP_DIR
from modules.document_identity import extract_video_id, make_document_id


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Document:
    video_id: str
    source_url: str
    content: str
    profile: str = "default"
    schema_version: str = "v1"
    prompt_version: str = ""
    document_id: str = ""
    segments: List[Segment] = field(default_factory=list)
    _seg_embs: object = field(default=None, repr=False)
    source_path: str = ""
    chunk_type: str = ""
    domain: str = ""
    quality: str = ""
    review_status: str = ""
    source_of_truth: bool | None = None
    risk_level: str = "low"
    answer_policy: str = ""
    source_refs: List[str] = field(default_factory=list)
    raw_source_path: str = ""
    content_hash: str = ""

    def __post_init__(self):
        if not self.document_id:
            self.document_id = make_document_id(self.video_id, self.profile)


class VaultLoader:
    def __init__(
        self,
        vault_path: Path | Iterable[Path],
        model: SentenceTransformer,
        *,
        embed_segments: bool = True,
    ):
        if isinstance(vault_path, (str, Path)):
            vault_path = [vault_path]
        self.vault_paths = tuple(Path(path) for path in vault_path)
        self.model = model
        self.embed_segments = embed_segments

    def _markdown_files(self):
        for vault_path in self.vault_paths:
            if vault_path.exists():
                for path in sorted(vault_path.rglob("*.md")):
                    if self._include_path(path):
                        yield path

    @staticmethod
    def _include_path(path: Path) -> bool:
        """Include knowledge notes and transcripts, excluding vault operations files."""
        parts = {part.casefold() for part in path.parts}
        name = path.name.casefold()
        if ".obsidian" in parts or "笔记" in parts:
            return False
        if name in {"仓库首页.md", "项目说明.md", "语言与术语核查清单.md"}:
            return False
        return True

    def load_all(self) -> List[Document]:
        candidates = {}
        for md_file in self._markdown_files():
            doc = self._parse_markdown(md_file)
            current = candidates.get(doc.document_id)
            if current is None or self._prefer_path(md_file, current[0], doc.document_id):
                candidates[doc.document_id] = (md_file, doc)

        documents = []
        for md_file, doc in candidates.values():
            doc.segments = self._parse_transcript(doc)
            if doc.segments and self.embed_segments:
                doc._seg_embs = self.model.encode(
                    [s.text for s in doc.segments],
                    normalize_embeddings=True,
                )
            documents.append(doc)
        return documents

    def load_one(self, video_id: str, profile: str = "default") -> Document | None:
        document_id = make_document_id(video_id, profile)
        selected = None
        for md_file in self._markdown_files():
            doc = self._parse_markdown(md_file)
            if doc.document_id != document_id:
                continue
            if selected is None or self._prefer_path(md_file, selected[0], document_id):
                selected = (md_file, doc)

        if selected is None:
            return None

        doc = selected[1]
        doc.segments = self._parse_transcript(doc)
        if doc.segments and self.embed_segments:
            doc._seg_embs = self.model.encode(
                [s.text for s in doc.segments],
                normalize_embeddings=True,
            )
        return doc

    @staticmethod
    def _prefer_path(candidate: Path, current: Path, document_id: str) -> bool:
        canonical_name = f"{document_id}.md"
        if candidate.name == canonical_name:
            return current.name != canonical_name
        if current.name == canonical_name:
            return False
        return candidate.stat().st_mtime_ns >= current.stat().st_mtime_ns

    def _parse_markdown(self, path: Path) -> Document:
        text = path.read_text(encoding="utf-8")
        frontmatter = {}
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                try:
                    parsed = yaml.safe_load(parts[1])
                    if isinstance(parsed, dict):
                        frontmatter = parsed
                except yaml.YAMLError:
                    frontmatter = {}
        source_url = str(frontmatter.get("source") or frontmatter.get("source_url") or "")
        body_video_id = extract_video_id(text)
        video_id = str(
            frontmatter.get("video_id")
            or extract_video_id(source_url)
            or body_video_id
            or frontmatter.get("id", path.stem)
        )
        profile = str(frontmatter.get("profile", "default"))
        declared_type = str(frontmatter.get("chunk_type") or frontmatter.get("document_kind") or "")
        has_transcript = bool(re.search(r"(?im)^##\s+Transcript\s*$", text))
        has_summary = bool(re.search(
            r"(?im)^##\s+(一句话结论|一句话总结|医疗内容总结|核心观点与事实|关键医学信息)",
            text,
        ))
        if declared_type in {"structured_summary", "rag_summary", "webclipper_summary"}:
            chunk_type = "structured_summary"
        elif declared_type == "raw_transcript" or "unprocessed" in path.parts or (has_transcript and not has_summary):
            chunk_type = "raw_transcript"
        elif path.parent.name == "videos":
            chunk_type = "structured_summary"
        else:
            chunk_type = declared_type or "document"
        source_of_truth = frontmatter.get("source_of_truth")
        if isinstance(source_of_truth, str):
            source_of_truth = source_of_truth.strip().lower() in {"1", "true", "yes"}
        if source_of_truth is None and chunk_type == "raw_transcript":
            source_of_truth = True
        source_refs = frontmatter.get("source_refs", [])
        if isinstance(source_refs, str):
            source_refs = [item.strip() for item in source_refs.split(",") if item.strip()]
        if not isinstance(source_refs, list):
            source_refs = []
        review_status = str(frontmatter.get("review_status", ""))
        quality = str(frontmatter.get("quality", ""))
        if review_status in {"reviewed", "approved", "human_reviewed"}:
            quality = "human_reviewed"
        elif chunk_type == "raw_transcript":
            quality = quality or "raw_unverified"
        elif chunk_type == "structured_summary":
            quality = "derived_pending_review" if quality in {"", "draft"} else quality
        return Document(
            video_id=video_id,
            source_url=source_url,
            content=text,
            profile=profile,
            schema_version=str(frontmatter.get("schema_version", "v1")),
            prompt_version=str(frontmatter.get("prompt_version", "")),
            source_path=str(path.resolve()),
            chunk_type=chunk_type,
            domain=str(frontmatter.get("domain", "")),
            quality=quality,
            review_status=review_status,
            source_of_truth=source_of_truth,
            risk_level=str(frontmatter.get("risk_level", "low")),
            answer_policy=str(frontmatter.get("answer_policy", "")),
            source_refs=[str(item) for item in source_refs],
            raw_source_path=str(frontmatter.get("raw_source_path", "")),
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )

    def _parse_transcript(self, doc: Document) -> List[Segment]:
        video_id = extract_video_id(doc.source_url) or extract_video_id(doc.video_id)
        if video_id:
            exact_matches = [
                TEMP_DIR / f"{video_id}_norm.txt",
                TEMP_DIR / f"{video_id}__transcript.txt",
                TEMP_DIR / f"{video_id}.txt",
            ]
            matches = [p for p in exact_matches if p.exists()]
            if not matches:
                matches = sorted(
                    TEMP_DIR.glob(f"{video_id}*.txt"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
            if matches:
                return self._parse_timestamped_text(matches[0].read_text(encoding="utf-8"))
        return self._parse_timestamped_text(doc.content, embedded=True)

    @staticmethod
    def _parse_timestamped_text(text: str, *, embedded: bool = False) -> List[Segment]:
        segments = []
        for line in text.splitlines():
            current = re.match(r"\[(\d+\.?\d*)s\s*->\s*(\d+\.?\d*)s\]\s*(.*)", line)
            if current:
                start, end, body = float(current.group(1)), float(current.group(2)), current.group(3)
            elif embedded:
                current = re.match(r"\s*(?:>\s*)?\*\*(\d{1,2}:\d{2}(?::\d{2})?)\*\*\s*[·|:-]\s*(.+)", line)
                if not current:
                    continue
                start = VaultLoader._parse_clock(current.group(1))
                end, body = start, current.group(2).strip()
            else:
                continue
            if body.strip():
                segments.append(Segment(start=start, end=end, text=body.strip()))
        for previous, current in zip(segments, segments[1:]):
            if previous.end <= previous.start:
                previous.end = current.start
        return segments

    @staticmethod
    def _parse_clock(value: str) -> float:
        parts = [int(part) for part in value.split(":")]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
