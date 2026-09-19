"""Step 3: 结构化转录文本"""
import argparse
import json
import re
import time
from pathlib import Path

from config import SUMMARY_PROFILES, TEMP_DIR
from modules.content_map import extract_content_map
from modules.prompt_profiles import DEFAULT_PROFILE_NAME, available_profiles, load_prompt_profile
from modules.structure import (
    extract_content_overview,
    render_markdown,
    structure_content_map,
)


def load_transcript(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    pure = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    return "\n".join(pure)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run structure step only")
    parser.add_argument(
        "--profile",
        default=None,
        help="single profile compatibility option; prefer --profiles",
    )
    parser.add_argument(
        "--profiles",
        default=None,
        help=(
            "comma-separated summary profiles. "
            f"Available: {', '.join(available_profiles())}. "
            f"Default: {', '.join(SUMMARY_PROFILES)}"
        ),
    )
    parser.add_argument("video_url")
    parser.add_argument("transcript_path", nargs="?")
    args = parser.parse_args()

    url = args.video_url
    match = re.search(r"(BV[\w]+)", url)
    if not match:
        print(f"Cannot extract video ID from URL: {url}")
        raise SystemExit(1)
    video_id = match.group(1)
    transcript_path = Path(args.transcript_path) if args.transcript_path else TEMP_DIR / f"{video_id}_norm.txt"
    if args.profile and args.profiles:
        parser.error("Use either --profile or --profiles, not both")
    selected_names = list(dict.fromkeys(
        [name.strip() for name in args.profiles.split(",") if name.strip()]
        if args.profiles
        else [args.profile] if args.profile
        else list(SUMMARY_PROFILES)
    ))
    if not selected_names:
        parser.error("At least one summary profile is required")
    profiles = [load_prompt_profile(name) for name in selected_names]

    print("Loading transcript...")
    transcript = load_transcript(str(transcript_path))
    print(f"Transcript: {len(transcript)} chars")

    print(f"Extracting shared content map for profiles={','.join(p.name for p in profiles)}...")
    t0 = time.time()
    content_map = extract_content_map(transcript, source_id=video_id)
    overview = extract_content_overview(content_map)
    overview_path = TEMP_DIR / f"{video_id}__content_overview.json"
    with open(overview_path, "w", encoding="utf-8") as f:
        json.dump(overview, f, ensure_ascii=False, indent=2)
    print(f"Shared overview: {overview_path}")
    failures = {}
    for profile in profiles:
        try:
            structured = structure_content_map(
                content_map,
                profile=profile,
                source_id=video_id,
            )
            json_path = TEMP_DIR / f"{video_id}__{profile.name}_structured.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(structured, f, ensure_ascii=False, indent=2)

            md = render_markdown(
                structured,
                url,
                profile=profile.name,
                schema_version=profile.schema_version,
                prompt_version=profile.prompt_version,
                content_map=content_map,
            )
            md_path = TEMP_DIR / f"{video_id}__{profile.name}_structured.md"
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(md)

            print(f"[{profile.name}] JSON: {json_path}")
            print(f"[{profile.name}] Markdown: {md_path}")
            print(f"[{profile.name}] Title: {structured['title']}")
            print(f"[{profile.name}] Summary: {structured['summary']}")
        except Exception as exc:
            failures[profile.name] = str(exc)
            print(f"[{profile.name}] ERROR: {exc}")

    elapsed = time.time() - t0
    print(f"Done ({elapsed:.0f}s)")
    if failures:
        print(f"Failed profiles: {json.dumps(failures, ensure_ascii=False)}")
        raise SystemExit(1)
