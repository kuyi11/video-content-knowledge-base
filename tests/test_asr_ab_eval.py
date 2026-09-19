from pathlib import Path

from tools.asr_ab_eval import build_jobs


def test_build_jobs_creates_isolated_four_way_matrix(tmp_path):
    audio = tmp_path / "BVdemo_norm.wav"
    jobs = build_jobs(audio, tmp_path / "evaluation", ["fitness", "nutrition"])

    assert [job["label"] for job in jobs] == ["P0", "P1", "W0", "W1"]
    assert [job["backend"] for job in jobs] == [
        "paraformer",
        "paraformer",
        "faster_whisper",
        "faster_whisper",
    ]
    assert [job["domains"] for job in jobs] == [
        [],
        ["fitness", "nutrition"],
        [],
        ["fitness", "nutrition"],
    ]
    assert len({job["output_path"] for job in jobs}) == 4
    assert all(Path(job["output_path"]).parent.name == "evaluation" for job in jobs)