import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pipeline import Pipeline
from modules.obsidian_writer import write_to_obsidian
from modules.prompt_profiles import load_prompt_profile


def test_write_to_obsidian_honors_custom_output_dir(tmp_path):
    custom_vault = tmp_path / "custom-vault"
    path = write_to_obsidian(
        "# note",
        "https://www.bilibili.com/video/BVcustom/",
        output_dir=custom_vault,
    )

    assert Path(path).parent == custom_vault
    assert Path(path).name == "BVcustom__default.md"
    assert Path(path).read_text(encoding="utf-8") == "# note"


def test_write_to_obsidian_can_overwrite_existing_note(tmp_path):
    custom_vault = tmp_path / "custom-vault"
    url = "https://www.bilibili.com/video/BVcustom/"
    path = write_to_obsidian("# old", url, output_dir=custom_vault)
    write_to_obsidian("# new", url, output_dir=custom_vault, overwrite=True)

    assert Path(path).read_text(encoding="utf-8") == "# new"


def test_write_to_obsidian_keeps_profiles_separate(tmp_path):
    custom_vault = tmp_path / "custom-vault"
    url = "https://www.bilibili.com/video/BVcustom/?from=feed"

    default_path = write_to_obsidian("# default", url, output_dir=custom_vault)
    detailed_path = write_to_obsidian(
        "# detailed",
        url,
        output_dir=custom_vault,
        profile="detailed",
    )

    assert Path(default_path).name == "BVcustom__default.md"
    assert Path(detailed_path).name == "BVcustom__detailed.md"
    assert Path(default_path).read_text(encoding="utf-8") == "# default"
    assert Path(detailed_path).read_text(encoding="utf-8") == "# detailed"


def test_pipeline_state_is_atomic_and_incomplete_index_is_retryable(tmp_path):
    vault_path = tmp_path / "vault" / "videos"
    index_path = tmp_path / "index"
    pipeline = Pipeline(vault_path=vault_path, index_dir=index_path)

    md_path = vault_path / "video.md"
    md_path.write_text("# note", encoding="utf-8")
    pipeline._mark_completed("BVretry", str(md_path))

    state_path = index_path / "pipeline_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["version"] == 2
    assert state["videos"]["BVretry"]["default"]["status"] == "completed"
    assert not state_path.with_suffix(".tmp").exists()
    assert pipeline._already_processed("BVretry") is False


def test_pipeline_state_records_prompt_profile(tmp_path):
    vault_path = tmp_path / "vault" / "videos"
    index_path = tmp_path / "index"
    pipeline = Pipeline(vault_path=vault_path, index_dir=index_path)
    profile = load_prompt_profile("detailed")

    md_path = vault_path / "video.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("# note", encoding="utf-8")
    pipeline._mark_completed("BVprofile", str(md_path), profile)

    state = json.loads((index_path / "pipeline_state.json").read_text(encoding="utf-8"))
    profile_state = state["videos"]["BVprofile"]["detailed"]
    assert profile_state["profile"] == "detailed"
    assert profile_state["schema_version"] == "v2"
    assert profile_state["prompt_version"] == profile.prompt_version


def test_pipeline_state_keeps_profiles_separate_and_migrates_legacy_state(tmp_path):
    vault_path = tmp_path / "vault" / "videos"
    index_path = tmp_path / "index"
    pipeline = Pipeline(vault_path=vault_path, index_dir=index_path)
    default_profile = load_prompt_profile("default")
    detailed_profile = load_prompt_profile("detailed")

    legacy_path = vault_path / "legacy.md"
    legacy_path.write_text("# legacy", encoding="utf-8")
    (index_path / "pipeline_state.json").write_text(
        json.dumps(
            {
                "BVlegacy": {
                    "status": "completed",
                    "md_path": str(legacy_path),
                    "profile": "default",
                    "schema_version": "v1",
                    "prompt_version": default_profile.prompt_version,
                }
            }
        ),
        encoding="utf-8",
    )

    migrated = pipeline._load_state()
    assert migrated["videos"]["BVlegacy"]["default"]["status"] == "completed"

    detailed_path = vault_path / "BVlegacy__detailed.md"
    detailed_path.write_text("# detailed", encoding="utf-8")
    pipeline._mark_completed("BVlegacy", str(detailed_path), detailed_profile)

    state = json.loads((index_path / "pipeline_state.json").read_text(encoding="utf-8"))
    assert set(state["videos"]["BVlegacy"]) == {"default", "detailed"}


def test_pipeline_state_concurrent_writes_keep_all_profiles(tmp_path):
    vault_path = tmp_path / "vault" / "videos"
    index_path = tmp_path / "index"
    default_profile = load_prompt_profile("default")
    detailed_profile = load_prompt_profile("detailed")
    default_note = vault_path / "BVparallel__default.md"
    detailed_note = vault_path / "BVparallel__detailed.md"
    default_note.parent.mkdir(parents=True, exist_ok=True)
    default_note.write_text("# default", encoding="utf-8")
    detailed_note.write_text("# detailed", encoding="utf-8")

    def mark(profile, note):
        pipeline = Pipeline(vault_path=vault_path, index_dir=index_path)
        pipeline._mark_completed("BVparallel", str(note), profile)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(mark, default_profile, default_note),
            executor.submit(mark, detailed_profile, detailed_note),
        ]
        for future in futures:
            future.result()

    state = json.loads((index_path / "pipeline_state.json").read_text(encoding="utf-8"))
    assert set(state["videos"]["BVparallel"]) == {"default", "detailed"}
