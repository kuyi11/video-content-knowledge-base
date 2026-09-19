"""Canonical identities shared by vault, pipeline, and index storage."""

import re


VIDEO_ID_PATTERN = re.compile(r"(BV[\w]+)")
PROFILE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def extract_video_id(url: str) -> str | None:
    match = VIDEO_ID_PATTERN.search(url or "")
    return match.group(1) if match else None


def validate_profile_name(profile: str) -> str:
    profile = str(profile).strip()
    if not profile or not PROFILE_PATTERN.fullmatch(profile):
        raise ValueError(
            "Profile name must contain only letters, numbers, '.', '_' or '-': "
            f"{profile!r}"
        )
    return profile


def make_document_id(video_id: str, profile: str) -> str:
    return f"{video_id}__{validate_profile_name(profile)}"
