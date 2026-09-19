"""Audit claim evidence against raw transcript source locations.

This is a provenance audit, not a medical truth judge. It deliberately separates
source location from text matching so a reviewer can inspect fuzzy ASR matches.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

VIDEO_ID_RE = re.compile(r"(?<![0-9A-Za-z])BV[0-9A-Za-z]+(?=__|\b|$)")
SOURCE_REF_RE = re.compile(r"\bS(\d{4})\b")
RAW_CHUNK_RE = re.compile(r"__raw_(\d{4})\b")
TIMED_LINE_RE = re.compile(r"^\[(\d+(?:\.\d+)?)s\s*->\s*(\d+(?:\.\d+)?)s\]\s*(.+)$")
EMBEDDED_LINE_RE = re.compile(
    r"^\s*\*\*(\d{1,2}:\d{2}(?::\d{2})?)\*\*\s*[·|:-]\s*(.+)$"
)
SOURCE_RANGE_RE = re.compile(
    r"\b(S\d{4})\s*\(\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*-\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*\)"
)


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", re.sub(r"[^0-9A-Za-z\u3400-\u9fff]+", "", text.casefold()))


def parse_clock(value: str) -> float:
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def parse_segments(path: Path) -> list[dict]:
    segments = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = TIMED_LINE_RE.match(line) or EMBEDDED_LINE_RE.match(line)
        if not match:
            continue
        if line.lstrip().startswith("["):
            start, end, text = float(match.group(1)), float(match.group(2)), match.group(3)
        else:
            start = parse_clock(match.group(1))
            end, text = start, match.group(2).strip()
        if text.strip():
            segments.append({"start": start, "end": end, "text": text.strip()})
    for previous, current in zip(segments, segments[1:]):
        if previous["end"] <= previous["start"]:
            previous["end"] = current["start"]
    return segments


def video_id_from_text(text: str) -> str:
    match = VIDEO_ID_RE.search(text)
    return match.group(0) if match else ""


def raw_video_paths(raw_root: Path) -> dict[str, Path]:
    paths = {}
    for path in raw_root.rglob("*.md"):
        match = re.search(r"(?m)^video_id:\s*([^\s]+)", path.read_text(encoding="utf-8"))
        if match:
            paths.setdefault(match.group(1).strip(), path)
    return paths


def candidate_structured_videos(
    evidence_text: str,
    source_refs: list[str],
    structured_root: Path,
) -> list[str]:
    normalized_evidence = normalize(evidence_text)
    candidates = []
    for path in structured_root.glob("BV*.md"):
        text = path.read_text(encoding="utf-8")
        refs = set(SOURCE_REF_RE.findall(text))
        has_ref = any(ref[1:] in refs or ref in refs for ref in source_refs)
        if not has_ref:
            continue
        normalized_text = normalize(text)
        if normalized_evidence and normalized_evidence in normalized_text:
            score = 1.0
        else:
            evidence_grams = {normalized_evidence[i : i + 2] for i in range(max(0, len(normalized_evidence) - 1))}
            text_grams = {normalized_text[i : i + 2] for i in range(max(0, len(normalized_text) - 1))}
            score = len(evidence_grams & text_grams) / len(evidence_grams) if evidence_grams else 0.0
        video_id = video_id_from_text(path.name)
        if video_id:
            candidates.append((score, video_id))
    if not candidates:
        # Some structured exports omit a source ref from Markdown while the
        # normalized content map still contains the exact evidence text.
        candidates = []
        for path in structured_root.glob("BV*.md"):
            normalized_text = normalize(path.read_text(encoding="utf-8"))
            if normalized_evidence and normalized_evidence in normalized_text:
                candidates.append((1.0, video_id_from_text(path.name)))
            elif normalized_evidence:
                evidence_grams = {
                    normalized_evidence[i : i + 2]
                    for i in range(max(0, len(normalized_evidence) - 1))
                }
                text_grams = {
                    normalized_text[i : i + 2]
                    for i in range(max(0, len(normalized_text) - 1))
                }
                score = len(evidence_grams & text_grams) / len(evidence_grams)
                candidates.append((score, video_id_from_text(path.name)))
    if not candidates:
        return []
    best_score = max(score for score, _ in candidates)
    return sorted({video_id for score, video_id in candidates if score == best_score})


def source_window(
    segments: list[dict],
    source_refs: list[str],
    chunk_id: str,
    source_ranges: dict[str, tuple[float, float]] | None = None,
) -> list[dict]:
    if source_ranges:
        selected = []
        for source_ref in source_refs:
            time_range = source_ranges.get(source_ref)
            if not time_range:
                continue
            start, end = time_range
            selected.extend(
                segment for segment in segments
                if segment["end"] >= start and segment["start"] <= end
            )
        if selected:
            return selected
    indices = [int(match.group(1)) for match in SOURCE_REF_RE.finditer(" ".join(source_refs))]
    if indices and max(indices) <= len(segments):
        selected = [segments[index - 1] for index in indices]
        return selected
    raw_match = RAW_CHUNK_RE.search(chunk_id)
    if raw_match and segments:
        stamp = raw_match.group(1)
        seconds = int(stamp[:2]) * 60 + int(stamp[2:])
        nearest = min(segments, key=lambda item: abs(item["start"] - seconds))
        return [nearest]
    return []


def source_ranges(
    video_id: str,
    structured_root: Path,
    temp_root: Path | None = None,
) -> dict[str, tuple[float, float]]:
    ranges: dict[str, tuple[float, float]] = {}
    for path in structured_root.glob(f"{video_id}*.md"):
        text = path.read_text(encoding="utf-8")
        for match in SOURCE_RANGE_RE.finditer(text):
            ranges.setdefault(
                match.group(1),
                (parse_clock(match.group(2)), parse_clock(match.group(3))),
            )
    cache_root = (temp_root or structured_root.parent.parent / "temp") / "structure_cache" / video_id
    for path in cache_root.glob("*/content_map.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for source_ref, item in payload.get("sources", {}).items():
            if isinstance(item, dict) and "start" in item and "end" in item:
                ranges.setdefault(source_ref, (float(item["start"]), float(item["end"])))
    return ranges


def transcript_path(
    video_id: str,
    chunk_id: str,
    source_refs: list[str],
    raw_paths: dict[str, Path],
    temp_root: Path,
) -> Path | None:
    """Choose the transcript whose clock matches the evidence identifier.

    Raw chunk IDs encode the timestamp from the Obsidian source document, while
    structured source refs encode ranges from the normalized transcript. Mixing
    these two clocks can produce a plausible but incorrect citation.
    """
    temp_paths = sorted(temp_root.glob(f"{video_id}*.txt"))
    raw_path = raw_paths.get(video_id)
    if source_refs:
        return temp_paths[0] if temp_paths else raw_path
    if RAW_CHUNK_RE.search(chunk_id):
        return raw_path or (temp_paths[0] if temp_paths else None)
    return temp_paths[0] if temp_paths else raw_path


def audit_case(case: dict, raw_paths: dict[str, Path], temp_root: Path, structured_root: Path) -> dict:
    evidence_results = []
    for evidence in case["evidence"]:
        chunk_id = str(evidence["chunk_id"])
        source_refs = [str(item) for item in evidence.get("source_refs", [])]
        video_id = video_id_from_text(chunk_id)
        candidates = [video_id] if video_id else candidate_structured_videos(
            str(evidence["text"]), source_refs, structured_root
        )
        locations = []
        best_similarity = 0.0
        best_text = ""
        for candidate in candidates:
            path = transcript_path(candidate, chunk_id, source_refs, raw_paths, temp_root)
            if not path or not path.exists():
                continue
            segments = parse_segments(path)
            window = source_window(
                segments,
                source_refs,
                chunk_id,
                source_ranges(candidate, structured_root, temp_root),
            )
            window_text = "".join(segment["text"] for segment in window)
            score = SequenceMatcher(None, normalize(str(evidence["text"])), normalize(window_text)).ratio()
            if score > best_similarity:
                best_similarity, best_text = score, window_text
            locations.append(
                {
                    "video_id": candidate,
                    "source_path": str(path),
                    "source_refs": source_refs,
                    "segments": window,
                    "text_similarity": round(score, 4),
                }
            )
        exact_match = bool(best_text and normalize(str(evidence["text"])) in normalize(best_text))
        if exact_match:
            status = "verified_text_match"
        elif locations:
            status = "located_needs_human"
        else:
            status = "unresolved"
        evidence_results.append(
            {
                "chunk_id": chunk_id,
                "source_refs": source_refs,
                "status": status,
                "candidate_video_ids": candidates,
                "locations": locations,
                "best_text_similarity": round(best_similarity, 4),
                "best_source_text": best_text,
            }
        )
    statuses = {item["status"] for item in evidence_results}
    case_status = "verified_text_match" if statuses == {"verified_text_match"} else (
        "located_needs_human" if statuses & {"verified_text_match", "located_needs_human"} else "unresolved"
    )
    return {"id": case["id"], "claim": case["claim"], "status": case_status, "evidence": evidence_results}


def audit_claims(labels_path: Path, raw_root: Path, temp_root: Path, structured_root: Path) -> dict:
    cases = [json.loads(line) for line in labels_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    raw_paths = raw_video_paths(raw_root)
    details = [audit_case(case, raw_paths, temp_root, structured_root) for case in cases]
    counts = {}
    for detail in details:
        counts[detail["status"]] = counts.get(detail["status"], 0) + 1
    return {
        "run": {
            "labels": labels_path.as_posix(),
            "raw_root": raw_root.as_posix(),
            "temp_root": temp_root.as_posix(),
            "structured_root": structured_root.as_posix(),
            "audit_version": "source-provenance-v1",
        },
        "summary": {"case_count": len(details), "case_status_counts": dict(sorted(counts.items()))},
        "cases": details,
    }


def filter_reviewed_cases(report: dict, reviewed_ids: set[str]) -> dict:
    """Return a provenance report containing only cases not yet human-reviewed."""
    cases = [case for case in report["cases"] if case["id"] not in reviewed_ids]
    counts: dict[str, int] = {}
    for case in cases:
        counts[case["status"]] = counts.get(case["status"], 0) + 1
    filtered = dict(report)
    filtered["summary"] = {
        "case_count": len(cases),
        "case_status_counts": dict(sorted(counts.items())),
    }
    filtered["run"] = {**report["run"], "excluded_reviewed_case_count": len(reviewed_ids)}
    filtered["cases"] = cases
    return filtered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, default=PROJECT_ROOT / "eval" / "claim_labels.jsonl")
    parser.add_argument("--raw-root", type=Path, default=PROJECT_ROOT / "vault" / "raw_transcripts")
    parser.add_argument("--temp-root", type=Path, default=PROJECT_ROOT / "temp")
    parser.add_argument("--structured-root", type=Path, default=PROJECT_ROOT / "vault" / "videos")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "eval" / "reports" / "claim-source-audit.json")
    parser.add_argument(
        "--baseline-output",
        type=Path,
        help="Optional compact JSON baseline to commit alongside the ignored detailed report.",
    )
    parser.add_argument(
        "--review-record",
        type=Path,
        help="JSON human-review record; used with --remaining-only to exclude reviewed IDs.",
    )
    parser.add_argument(
        "--remaining-only",
        action="store_true",
        help="Write only cases absent from the supplied human-review record.",
    )
    args = parser.parse_args()
    report = audit_claims(args.labels, args.raw_root, args.temp_root, args.structured_root)
    if args.remaining_only:
        if not args.review_record:
            parser.error("--remaining-only requires --review-record")
        review = json.loads(args.review_record.read_text(encoding="utf-8"))
        reviewed_ids = {str(item["id"]) for item in review.get("results", []) if item.get("id")}
        report = filter_reviewed_cases(report, reviewed_ids)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.baseline_output:
        args.baseline_output.parent.mkdir(parents=True, exist_ok=True)
        baseline = {
            "run": report["run"],
            "summary": report["summary"],
            "evidence_status_counts": {
                status: sum(
                    1
                    for case in report["cases"]
                    for evidence in case["evidence"]
                    if evidence["status"] == status
                )
                for status in ("verified_text_match", "located_needs_human", "unresolved")
            },
        }
        args.baseline_output.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
