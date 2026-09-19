"""Add canonical frontmatter to raw transcript Markdown files."""

from __future__ import annotations

import argparse
import html
import re
from pathlib import Path


BVID_RE = re.compile(r"bvid=([^&\" ]+)", re.IGNORECASE)
SUFFIX_RE = re.compile(r"__medical__unprocessed(?: \d+)?$", re.IGNORECASE)


def normalize_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        return False
    match = BVID_RE.search(text)
    if not match:
        raise ValueError(f"No BVID found in iframe: {path}")
    video_id = html.unescape(match.group(1))
    title = SUFFIX_RE.sub("", path.stem)
    frontmatter = (
        "---\n"
        f"title: {title}\n"
        f"video_id: {video_id}\n"
        f"source_url: https://www.bilibili.com/video/{video_id}\n"
        "transcript_source: platform_caption\n"
        "chunk_type: raw_transcript\n"
        "domain: medical\n"
        "quality: raw_unverified\n"
        "review_status: pending\n"
        "source_of_truth: true\n"
        "---\n\n"
    )
    path.write_text(frontmatter + text, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    files = sorted(args.directory.rglob("*.md"))
    changed = sum(normalize_file(path) for path in files)
    print(f"normalized={changed} files={len(files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
