import re
import hashlib
from dataclasses import dataclass, field
from typing import List

import numpy as np
from modules.embedding_runtime import SentenceTransformer

from modules.vault_loader import Document, Segment
from modules.document_identity import make_document_id

MIN_CHARS = 80
MAX_CHARS = 600
TOP_K_ALIGN = 8
CONTIGUOUS_GAP = 20  # seconds
SOURCE_REF_RE = re.compile(r"\bS\d{4}\b")
CHUNKING_VERSION = "evidence-v2"


@dataclass
class Chunk:
    chunk_id: str
    video_id: str
    source_url: str
    content: str
    section: str
    start: float
    end: float
    has_timestamp: bool = True
    document_id: str = ""
    profile: str = "default"
    source_path: str = ""
    chunk_type: str = "document"
    domain: str = ""
    quality: str = ""
    review_status: str = ""
    source_of_truth: bool | None = None
    risk_level: str = "low"
    answer_policy: str = ""
    source_refs: List[str] = field(default_factory=list)
    raw_source_path: str = ""
    content_hash: str = ""


class ChunkSplitter:
    def __init__(self, model: SentenceTransformer):
        self.model = model

    def split(self, documents: List[Document]) -> List[Chunk]:
        all_chunks = []
        for doc in documents:
            doc_chunks = self._split_one(doc)
            all_chunks.extend(doc_chunks)
        for i, ch in enumerate(all_chunks):
            document_id = ch.document_id or make_document_id(ch.video_id, ch.profile)
            ch.document_id = document_id
            digest = hashlib.sha256(
                f"{document_id}\n{ch.section}\n{ch.content}".encode("utf-8")
            ).hexdigest()[:16]
            ch.chunk_id = f"{document_id}_{digest}"
        return all_chunks

    def _split_one(self, doc: Document) -> List[Chunk]:
        if doc.chunk_type == "raw_transcript" and doc.segments:
            return self._split_raw_transcript(doc)
        sections = self._split_by_headings(doc.content)
        raw_chunks = []
        for section_title, section_text in sections:
            if section_title.casefold() == "transcript":
                continue
            if not section_text.strip():
                continue
            body = section_text.strip()
            section_content = "## " + section_title + "\n" + body
            # Section provenance must come from that section. Document-level refs
            # can span unrelated headings and would over-credit source coverage.
            section_refs = self._refs_for_text(section_content, [])
            if len(body) < MIN_CHARS and raw_chunks and not section_refs:
                prev = raw_chunks[-1]
                prev.content += "\n\n" + section_content
                prev.section += " + " + section_title
            else:
                raw_chunks.append(
                    Chunk(
                        chunk_id="", video_id=doc.video_id, source_url=doc.source_url,
                        content=section_content, section=section_title,
                        start=0.0, end=0.0, document_id=doc.document_id, profile=doc.profile,
                        source_path=doc.source_path, chunk_type=doc.chunk_type, domain=doc.domain,
                        quality=doc.quality, review_status=doc.review_status,
                        source_of_truth=doc.source_of_truth, risk_level=doc.risk_level,
                        answer_policy=doc.answer_policy,
                        source_refs=section_refs,
                        raw_source_path=doc.raw_source_path,
                        content_hash=doc.content_hash,
                    )
                )

        final_chunks = []
        for ch in raw_chunks:
            if len(ch.content) <= MAX_CHARS:
                final_chunks.append(ch)
            else:
                sub_chunks = self._split_long(ch.content)
                final_chunks.extend([
                    Chunk(chunk_id="", video_id=ch.video_id, source_url=ch.source_url,
                          content=sc, section=ch.section, start=0.0, end=0.0,
                          document_id=ch.document_id, profile=ch.profile,
                          source_path=ch.source_path, chunk_type=ch.chunk_type, domain=ch.domain,
                          quality=ch.quality, review_status=ch.review_status,
                          source_of_truth=ch.source_of_truth, risk_level=ch.risk_level,
                          answer_policy=ch.answer_policy,
                          source_refs=self._refs_for_text(sc, ch.source_refs),
                          raw_source_path=ch.raw_source_path, content_hash=ch.content_hash)
                    for sc in sub_chunks
                ])

        for ch in final_chunks:
            self._align_time(ch, doc)

        # Web-clipped notes may contain both a structured summary and a raw
        # transcript. Keep the summary chunks for discovery and emit the
        # transcript separately so provenance policy can admit primary evidence.
        if doc.chunk_type == "structured_summary" and doc.segments:
            raw_doc = Document(
                video_id=doc.video_id,
                source_url=doc.source_url,
                content="",
                profile=f"{doc.profile}_raw",
                document_id=f"{doc.document_id}__raw",
                segments=doc.segments,
                source_path=doc.source_path,
                chunk_type="raw_transcript",
                domain=doc.domain,
                quality="raw_unverified",
                review_status=doc.review_status,
                source_of_truth=True,
                risk_level=doc.risk_level,
                content_hash=doc.content_hash,
            )
            final_chunks.extend(self._split_raw_transcript(raw_doc))

        return final_chunks

    def _split_raw_transcript(self, doc: Document) -> List[Chunk]:
        chunks = []
        current_lines = []
        current_refs = []
        current_start = 0.0
        current_end = 0.0
        for index, segment in enumerate(doc.segments, start=1):
            ref = f"S{index:04d}"
            line = f"[{ref} {segment.start:.2f}s-{segment.end:.2f}s] {segment.text}"
            if current_lines and len("\n".join((*current_lines, line))) > MAX_CHARS:
                chunks.append(self._raw_chunk(doc, current_lines, current_refs, current_start, current_end))
                current_lines, current_refs = [], []
            if not current_lines:
                current_start = segment.start
            current_lines.append(line)
            current_refs.append(ref)
            current_end = segment.end
        if current_lines:
            chunks.append(self._raw_chunk(doc, current_lines, current_refs, current_start, current_end))
        return chunks

    @staticmethod
    def _raw_chunk(doc: Document, lines: list[str], refs: list[str], start: float, end: float) -> Chunk:
        return Chunk(
            chunk_id="", video_id=doc.video_id, source_url=doc.source_url,
            content="\n".join(lines), section="Transcript", start=start, end=end,
            has_timestamp=True, document_id=doc.document_id, profile=doc.profile,
            source_path=doc.source_path, chunk_type=doc.chunk_type, domain=doc.domain,
            quality=doc.quality or "raw_unverified", review_status=doc.review_status,
            source_of_truth=doc.source_of_truth, risk_level=doc.risk_level,
            answer_policy=doc.answer_policy, source_refs=list(refs),
            raw_source_path=doc.raw_source_path, content_hash=doc.content_hash,
        )

    @staticmethod
    def _refs_for_text(text: str, fallback: List[str]) -> List[str]:
        refs = list(dict.fromkeys(SOURCE_REF_RE.findall(text)))
        return refs or list(dict.fromkeys(fallback))

    def _split_by_headings(self, content: str):
        matches = list(re.finditer(r"^(#{2,3})\s+(.+?)\s*$", content, flags=re.MULTILINE))
        result = []
        parent_title = ""
        for index, match in enumerate(matches):
            level, title = match.group(1), match.group(2).strip()
            body_start = match.end()
            body_end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
            body = content[body_start:body_end].strip()
            if level == "##":
                parent_title = title
                section_title = title
            else:
                section_title = f"{parent_title} > {title}" if parent_title else title
            result.append((section_title, body))
        return result

    def _split_long(self, text: str) -> List[str]:
        sentences = re.split(r"(?<=[。！？.\n])", text)
        sentences = [s.strip() for s in sentences if s.strip()]
        chunks = []
        current = ""
        last_sentence = ""
        for s in sentences:
            if len(s) > MAX_CHARS:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.extend(
                    s[start:start + MAX_CHARS]
                    for start in range(0, len(s), MAX_CHARS)
                )
                last_sentence = ""
                continue
            if len(current) + len(s) <= MAX_CHARS:
                current += s
                last_sentence = s
            else:
                if current:
                    chunks.append(current)
                overlap = last_sentence if len(last_sentence) + len(s) <= MAX_CHARS else ""
                current = overlap + s
                last_sentence = s
        if current:
            chunks.append(current)
        return chunks

    def _align_time(self, chunk: Chunk, doc: Document):
        if not doc.segments or doc._seg_embs is None:
            chunk.has_timestamp = False
            return
        c_emb = self.model.encode([chunk.content], normalize_embeddings=True)[0]
        scores = c_emb @ doc._seg_embs.T
        top_k = min(TOP_K_ALIGN, len(doc.segments))
        indices = np.argsort(scores)[-top_k:]
        valid = self._keep_contiguous(indices, doc.segments)
        if valid:
            chunk.start = doc.segments[valid[0]].start
            chunk.end = doc.segments[valid[-1]].end

    def _keep_contiguous(self, indices: np.ndarray, segments: List[Segment]):
        sorted_idx = sorted(int(i) for i in indices)
        groups = []
        current = [sorted_idx[0]]
        for i in sorted_idx[1:]:
            if segments[i].start - segments[current[-1]].end < CONTIGUOUS_GAP:
                current.append(i)
            else:
                groups.append(current)
                current = [i]
        groups.append(current)
        return max(groups, key=len) if groups else []
