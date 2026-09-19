import sys
from pathlib import Path

from config import TEMP_DIR


if len(sys.argv) < 2:
    print(f"Usage: check_text.py <transcript_path> (default directory: {TEMP_DIR})")
    sys.exit(1)

path = Path(sys.argv[1])
with path.open("rb") as f:
    f.readline()  # header
    f.readline()  # blank
    line = f.readline()
print(f"Raw bytes: {line[:80]}")
print(f"Decoded: {line.decode('utf-8')}")
print(f"Chinese chars count: {sum(1 for c in line.decode('utf-8') if '\u4e00' <= c <= '\u9fff')}")
