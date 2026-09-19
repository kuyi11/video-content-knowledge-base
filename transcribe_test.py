import time
import sys
from pathlib import Path
from faster_whisper import WhisperModel

from config import (
    TEMP_DIR,
    WHISPER_MODEL_PATH,
    WHISPER_DEVICE,
    WHISPER_COMPUTE_TYPE,
    WHISPER_LANGUAGE,
)


def main():
    if len(sys.argv) < 2:
        print(f"Usage: transcribe_test.py <audio_path> (default directory: {TEMP_DIR})")
        return 1

    audio_path = Path(sys.argv[1])
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    model = WhisperModel(
        WHISPER_MODEL_PATH,
        device=WHISPER_DEVICE,
        compute_type=WHISPER_COMPUTE_TYPE,
    )

    t0 = time.time()
    segments, info = model.transcribe(str(audio_path), language=WHISPER_LANGUAGE)

    output_path = audio_path.with_suffix(".txt")
    lines = [f"# Language: {info.language} (probability: {info.language_probability:.4f})", ""]
    for seg in segments:
        lines.append(f"[{seg.start:.1f}s -> {seg.end:.1f}s] {seg.text}")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    elapsed = time.time() - t0
    print(f"Saved {len(lines)-2} segments to {output_path} ({elapsed:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
