"""Parse timestamped transcripts into grounded source blocks and LLM windows."""

from __future__ import annotations

import re
from dataclasses import dataclass


TIMESTAMP_RE = re.compile(
    r"^\[(?P<start>\d+(?:\.\d+)?)s\s*->\s*(?P<end>\d+(?:\.\d+)?)s\]\s*(?P<text>.*)$"
)


@dataclass(frozen=True, slots=True)
class TranscriptLine:
    start: float
    end: float
    text: str
    has_timestamp: bool = True


@dataclass(frozen=True, slots=True)
class SourceBlock:
    ref_id: str
    start: float
    end: float
    text: str
    has_timestamp: bool = True


@dataclass(frozen=True, slots=True)
class TranscriptWindow:
    window_id: str
    blocks: tuple[SourceBlock, ...]
    primary_ref_ids: tuple[str, ...]

    @property
    def start(self) -> float:
        return self.blocks[0].start if self.blocks else 0.0

    @property
    def end(self) -> float:
        return self.blocks[-1].end if self.blocks else 0.0

    @property
    def allowed_ref_ids(self) -> tuple[str, ...]:
        return tuple(block.ref_id for block in self.blocks)


def parse_transcript_lines(transcript_text: str) -> list[TranscriptLine]:
    timestamped = []
    plain = []
    for raw_line in transcript_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = TIMESTAMP_RE.match(line)
        if match:
            text = match.group("text").strip()
            if text:
                timestamped.append(
                    TranscriptLine(
                        start=float(match.group("start")),
                        end=float(match.group("end")),
                        text=text,
                    )
                )
        else:
            plain.append(line)

    if timestamped:
        return timestamped
    return [TranscriptLine(0.0, 0.0, line, has_timestamp=False) for line in plain]


def build_source_blocks(
    lines: list[TranscriptLine],
    *,
    max_chars: int,
    max_seconds: float,
    max_gap: float,
) -> list[SourceBlock]:
    if not lines:
        return []

    groups: list[list[TranscriptLine]] = []
    current: list[TranscriptLine] = []
    current_chars = 0

    for line in lines:
        separator_chars = 1 if current else 0
        gap = line.start - current[-1].end if current and line.has_timestamp else 0.0
        duration = line.end - current[0].start if current and line.has_timestamp else 0.0
        should_flush = bool(current) and (
            current_chars + separator_chars + len(line.text) > max_chars
            or gap > max_gap
            or duration > max_seconds
        )
        if should_flush:
            groups.append(current)
            current = []
            current_chars = 0

        current.append(line)
        current_chars += (1 if current_chars else 0) + len(line.text)

    if current:
        groups.append(current)

    blocks = []
    for index, group in enumerate(groups, 1):
        has_timestamp = all(line.has_timestamp for line in group)
        blocks.append(
            SourceBlock(
                ref_id=f"S{index:04d}",
                start=group[0].start,
                end=group[-1].end,
                text="\n".join(line.text for line in group),
                has_timestamp=has_timestamp,
            )
        )
    return blocks


def build_transcript_windows(
    blocks: list[SourceBlock],
    *,
    target_seconds: float,
    max_chars: int,
    overlap_seconds: float,
) -> list[TranscriptWindow]:
    if not blocks:
        return []

    primary_groups: list[tuple[int, int]] = []
    start = 0
    while start < len(blocks):
        end = start
        chars = 0
        while end < len(blocks):
            block = blocks[end]
            next_chars = chars + (1 if chars else 0) + len(block.text)
            duration = block.end - blocks[start].start
            exceeds_chars = end > start and next_chars > max_chars
            exceeds_duration = (
                end > start
                and block.has_timestamp
                and duration > target_seconds
            )
            if exceeds_chars or exceeds_duration:
                break
            chars = next_chars
            end += 1

        primary_groups.append((start, max(start + 1, end)))
        start = max(start + 1, end)

    windows = []
    for index, (primary_start, primary_end) in enumerate(primary_groups, 1):
        overlap_start = primary_start
        if primary_start < len(blocks) and blocks[primary_start].has_timestamp:
            primary_time = blocks[primary_start].start
            while overlap_start > 0:
                previous = blocks[overlap_start - 1]
                if primary_time - previous.end > overlap_seconds:
                    break
                overlap_start -= 1

        window_blocks = tuple(blocks[overlap_start:primary_end])
        primary_refs = tuple(block.ref_id for block in blocks[primary_start:primary_end])
        windows.append(
            TranscriptWindow(
                window_id=f"W{index:03d}",
                blocks=window_blocks,
                primary_ref_ids=primary_refs,
            )
        )
    return windows


def render_transcript_window(window: TranscriptWindow) -> str:
    primary_refs = set(window.primary_ref_ids)
    parts = []
    for block in window.blocks:
        role = "PRIMARY" if block.ref_id in primary_refs else "CONTEXT_ONLY"
        if block.has_timestamp:
            location = f"{block.start:.1f}s-{block.end:.1f}s"
        else:
            location = "N/A"
        parts.append(f"[{block.ref_id} | {location} | {role}]\n{block.text}")
    return "\n\n".join(parts)


def format_time(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
