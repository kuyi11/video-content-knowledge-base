"""
视频内容知识库 — 全局配置
"""

import logging
import os
import shutil
from pathlib import Path
from urllib.parse import urlparse

# === 路径配置 ===
PROJECT_ROOT = Path(__file__).resolve().parent
TEMP_DIR = PROJECT_ROOT / "temp"
STRUCTURE_CACHE_DIR = TEMP_DIR / "structure_cache"
VAULT_DIR = Path(os.environ.get("VAULT_DIR", str(PROJECT_ROOT / "vault" / "videos")))
INDEX_DIR = Path(os.environ.get("INDEX_DIR", str(PROJECT_ROOT / "index")))
VECTOR_INDEX_DIR = INDEX_DIR / "vector"
KEYWORD_INDEX_DIR = INDEX_DIR / "keyword"
PROMPT_DIR = PROJECT_ROOT / "prompts"
QUERY_REWRITE_PATH = PROMPT_DIR / "query_rewrites.yaml"
QUERY_REWRITE_ENABLED = os.environ.get("QUERY_REWRITE_ENABLED", "false").strip().lower() in {
    "1", "true", "yes", "on",
}
TOOL_DIR = Path(os.environ.get("TOOL_DIR", "D:/tool"))

# === ffmpeg 路径（mamba 环境提供） ===
def _resolve_ffmpeg_bin() -> str:
    env_path = os.environ.get("FFMPEG_BIN")
    if env_path:
        return env_path

    project_bin = PROJECT_ROOT / ".venv" / "Library" / "bin" / "ffmpeg.exe"
    if project_bin.exists():
        return str(project_bin)

    pyvenv_cfg = PROJECT_ROOT / ".venv" / "pyvenv.cfg"
    if pyvenv_cfg.exists():
        for line in pyvenv_cfg.read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("home") and "=" in line:
                home = Path(line.split("=", 1)[1].strip())
                candidate = home / "Library" / "bin" / "ffmpeg.exe"
                if candidate.exists():
                    return str(candidate)

    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        candidate = Path(conda_prefix) / "Library" / "bin" / "ffmpeg.exe"
        if candidate.exists():
            return str(candidate)

    found = shutil.which("ffmpeg")
    if found:
        return found

    return "ffmpeg"


def _resolve_ffprobe_bin() -> str:
    env_path = os.environ.get("FFPROBE_BIN")
    if env_path:
        return env_path

    ffmpeg_bin = Path(FFMPEG_BIN)
    if ffmpeg_bin.name.lower().startswith("ffmpeg"):
        candidate = ffmpeg_bin.with_name("ffprobe.exe")
        if candidate.exists():
            return str(candidate)
        candidate = ffmpeg_bin.with_name("ffprobe")
        if candidate.exists():
            return str(candidate)

    found = shutil.which("ffprobe")
    if found:
        return found

    return "ffprobe"


FFMPEG_BIN = _resolve_ffmpeg_bin()
FFPROBE_BIN = _resolve_ffprobe_bin()

# === 音频标准（锁定） ===
AUDIO_SAMPLE_RATE = 16000       # Hz
AUDIO_CHANNELS = 1               # mono
AUDIO_SAMPLE_FMT = "s16"         # 16-bit
AUDIO_FORMAT = "wav"

# === ASR 配置 ===
WHISPER_MODEL = "medium"         # 或 "large-v2"
WHISPER_MODEL_PATH = os.environ.get(
    "WHISPER_MODEL_PATH",
    str(TOOL_DIR / "faster-whisper-medium"),
)


def _ctranslate2_cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except (ImportError, OSError, RuntimeError):
        return False


def _resolve_whisper_runtime() -> tuple[str, str]:
    requested_device = os.environ.get("WHISPER_DEVICE", "auto").strip().lower()
    if requested_device in {"", "auto"}:
        device = "cuda" if _ctranslate2_cuda_available() else "cpu"
    elif requested_device in {"cuda", "cpu"}:
        device = requested_device
    else:
        raise ValueError("WHISPER_DEVICE must be 'auto', 'cuda', or 'cpu'")

    requested_compute = os.environ.get("WHISPER_COMPUTE_TYPE", "auto").strip().lower()
    if requested_compute in {"", "auto"}:
        compute_type = "float16" if device == "cuda" else "int8"
    else:
        compute_type = requested_compute
    return device, compute_type


WHISPER_DEVICE, WHISPER_COMPUTE_TYPE = _resolve_whisper_runtime()
WHISPER_LANGUAGE = os.environ.get("WHISPER_LANGUAGE", "auto")
SEGMENT_MINUTES = int(os.environ.get("SEGMENT_MINUTES", "8"))
SEGMENT_OVERLAP = int(os.environ.get("SEGMENT_OVERLAP", "10"))
MAX_DIRECT_DURATION = int(os.environ.get("MAX_DIRECT_DURATION", "600"))

# === ASR backend configuration ===
ASR_BACKEND = os.environ.get("ASR_BACKEND", "faster_whisper").strip().lower() or "faster_whisper"
ASR_POLICY = os.environ.get("ASR_POLICY", "manual").strip().lower() or "manual"
ASR_HOTWORDS_MAX = int(os.environ.get("ASR_HOTWORDS_MAX", "80"))
WHISPER_HOTWORDS_ENABLED = os.environ.get("WHISPER_HOTWORDS_ENABLED", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
ASR_SECOND_PASS = os.environ.get("ASR_SECOND_PASS", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
PARAFORMER_VAD_MAX_SEGMENT_MS = int(os.environ.get("PARAFORMER_VAD_MAX_SEGMENT_MS", "20000"))
PARAFORMER_DEVICE = os.environ.get("PARAFORMER_DEVICE", "cuda").strip().lower() or "cuda"
PARAFORMER_MODEL_PATH = os.environ.get(
    "PARAFORMER_MODEL_PATH",
    str(TOOL_DIR / "funasr" / "contextual-paraformer-zh"),
)
PARAFORMER_VAD_PATH = os.environ.get(
    "PARAFORMER_VAD_PATH",
    str(TOOL_DIR / "funasr" / "fsmn-vad"),
)
PARAFORMER_PUNC_PATH = os.environ.get(
    "PARAFORMER_PUNC_PATH",
    str(TOOL_DIR / "funasr" / "ct-punc"),
)


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_csv(value: str, default: tuple[str, ...]) -> tuple[str, ...]:
    values = tuple(item.strip() for item in value.split(",") if item.strip())
    return values or default


# Subtitle extraction is dependency-free beyond yt-dlp. OCR is intentionally
# outside this pipeline and is not enabled by these settings.
SUBTITLE_ENABLED = _parse_bool(os.environ.get("SUBTITLE_ENABLED", "true"), True)
SUBTITLE_ALLOW_ANY_LANGUAGE = _parse_bool(
    os.environ.get("SUBTITLE_ALLOW_ANY_LANGUAGE", "true"),
)
SUBTITLE_LANGUAGES = _parse_csv(
    os.environ.get("SUBTITLE_LANGUAGES", "zh-CN,zh-Hans,zh-Hant,zh,en,en-US,ja,ko"),
    ("zh-CN", "zh-Hans", "zh-Hant", "zh", "en", "en-US", "ja", "ko"),
)

# === Embedding 配置 ===
BGE_MODEL_PATH = os.environ.get(
    "BGE_MODEL_PATH",
    str(TOOL_DIR / "bge-m3"),
)
EMBEDDING_BATCH_SIZE = int(os.environ.get("EMBEDDING_BATCH_SIZE", "32"))

# === LLM 配置 ===
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", os.environ.get("LLM_BASE_URL", "http://localhost:11434"))
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", os.environ.get("LLM_MODEL", "qwen2.5:7b"))
QUERY_LOGGING_ENABLED = os.environ.get("QUERY_LOGGING_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
CLAIM_EVIDENCE_AUDIT_ENABLED = os.environ.get(
    "CLAIM_EVIDENCE_AUDIT_ENABLED", "false"
).strip().lower() in {"1", "true", "yes", "on"}
CLAIM_EVIDENCE_RETENTION_DAYS = max(
    0, int(os.environ.get("CLAIM_EVIDENCE_RETENTION_DAYS", "0"))
)
API_TOKEN = os.environ.get("API_TOKEN", "").strip()
ALLOW_REMOTE_LLM = os.environ.get("ALLOW_REMOTE_LLM", "false").strip().lower() in {"1", "true", "yes", "on"}
LLM_NUM_CTX = int(os.environ.get("LLM_NUM_CTX", "16384"))
LLM_NUM_PREDICT = int(os.environ.get("LLM_NUM_PREDICT", "4096"))
LLM_KEEP_ALIVE = os.environ.get("LLM_KEEP_ALIVE", "15m")
LLM_TIMEOUT_SECONDS = max(1.0, float(os.environ.get("LLM_TIMEOUT_SECONDS", "180")))
RERANKER_MODEL_PATH = os.environ.get("RERANKER_MODEL_PATH", "").strip()
RERANKER_CANDIDATE_K = max(5, int(os.environ.get("RERANKER_CANDIDATE_K", "20")))
RERANKER_DEVICE = os.environ.get("RERANKER_DEVICE", "").strip() or None
QUERY_PLANNER_ENABLED = os.environ.get("QUERY_PLANNER_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
QUERY_PLANNER_MODEL = os.environ.get("QUERY_PLANNER_MODEL", OLLAMA_MODEL)
QUERY_PLANNER_MAX_SUBQUERIES = max(1, min(5, int(os.environ.get("QUERY_PLANNER_MAX_SUBQUERIES", "3"))))
QUERY_MULTI_HOP_ENABLED = os.environ.get("QUERY_MULTI_HOP_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
QUERY_MULTI_HOP_MAX_HOPS = max(1, min(3, int(os.environ.get("QUERY_MULTI_HOP_MAX_HOPS", "2"))))
SEMANTIC_GROUNDING_MAX_CLAIMS = max(0, int(os.environ.get("SEMANTIC_GROUNDING_MAX_CLAIMS", "8")))
QUERY_CACHE_ENABLED = os.environ.get("QUERY_CACHE_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
QUERY_CACHE_SIZE = max(0, int(os.environ.get("QUERY_CACHE_SIZE", "128")))
API_RATE_LIMIT_PER_MINUTE = max(0, int(os.environ.get("API_RATE_LIMIT_PER_MINUTE", "60")))
API_MAX_CONCURRENT_QUERIES = max(1, int(os.environ.get("API_MAX_CONCURRENT_QUERIES", "4")))
# Optional accounting rates used for operational estimates only. Ollama does not
# expose provider billing, so these are disabled by default and never represent
# an invoice amount.
LLM_INPUT_COST_PER_1K = max(0.0, float(os.environ.get("LLM_INPUT_COST_PER_1K", "0")))
LLM_OUTPUT_COST_PER_1K = max(0.0, float(os.environ.get("LLM_OUTPUT_COST_PER_1K", "0")))
LLM_MAP_TEMPERATURE = float(os.environ.get("LLM_MAP_TEMPERATURE", "0.1"))
LLM_DIRECT_MAX_CHARS = int(os.environ.get("LLM_DIRECT_MAX_CHARS", "10000"))
LLM_WINDOW_SECONDS = int(os.environ.get("LLM_WINDOW_SECONDS", "240"))
LLM_WINDOW_MAX_CHARS = int(os.environ.get("LLM_WINDOW_MAX_CHARS", "4800"))
LLM_WINDOW_OVERLAP_SECONDS = int(os.environ.get("LLM_WINDOW_OVERLAP_SECONDS", "20"))
LLM_MAP_MAX_UNITS = int(os.environ.get("LLM_MAP_MAX_UNITS", "12"))
SOURCE_BLOCK_MAX_CHARS = int(os.environ.get("SOURCE_BLOCK_MAX_CHARS", "400"))
SOURCE_BLOCK_MAX_SECONDS = int(os.environ.get("SOURCE_BLOCK_MAX_SECONDS", "30"))
SOURCE_BLOCK_MAX_GAP = float(os.environ.get("SOURCE_BLOCK_MAX_GAP", "5"))


def _parse_summary_profiles(value: str) -> tuple[str, ...]:
    names = []
    for raw_name in value.split(","):
        name = raw_name.strip()
        if name and name not in names:
            names.append(name)
    return tuple(names or ["default"])


SUMMARY_PROFILES = _parse_summary_profiles(os.environ.get("SUMMARY_PROFILES", "default"))
def _is_local_llm_host(host: str) -> bool:
    """Allow loopback endpoints and Docker's host gateway without opt-in."""
    parsed = urlparse(host)
    return parsed.hostname in {"localhost", "127.0.0.1", "::1", "host.docker.internal"}


if OLLAMA_HOST and not ALLOW_REMOTE_LLM and not _is_local_llm_host(OLLAMA_HOST):
    raise ValueError(
        "OLLAMA_HOST is not local. Set ALLOW_REMOTE_LLM=true only after confirming "
        "that sending prompts and retrieved context to this endpoint is permitted."
    )


LLM_CONFIG = {
    "host": OLLAMA_HOST.rstrip("/"),
    "model": OLLAMA_MODEL,
    "num_ctx": LLM_NUM_CTX,
    "num_predict": LLM_NUM_PREDICT,
    "keep_alive": LLM_KEEP_ALIVE,
    "timeout_seconds": LLM_TIMEOUT_SECONDS,
}

if OLLAMA_HOST and not _is_local_llm_host(OLLAMA_HOST):
    logging.getLogger(__name__).warning("Remote OLLAMA_HOST explicitly enabled: %s", OLLAMA_HOST)

# === Obsidian 配置 ===
VAULT_VIDEOS_PATH = VAULT_DIR

# Optional read-only external Obsidian vaults. Paths are separated with ';'
# on Windows so a vault can be indexed without copying or modifying it.
_external_vaults_raw = os.environ.get("OBSIDIAN_EXTERNAL_VAULTS", "")
EXTERNAL_VAULT_PATHS = tuple(
    Path(item.strip())
    for item in _external_vaults_raw.split(";")
    if item.strip()
)
TERMINOLOGY_REVIEW_PATH = Path(
    os.environ.get(
        "TERMINOLOGY_REVIEW_PATH",
        str((EXTERNAL_VAULT_PATHS[0] if EXTERNAL_VAULT_PATHS else VAULT_DIR) / "语言与术语核查清单.md"),
    )
)
TERMINOLOGY_REVIEW_REPORT = INDEX_DIR / "terminology-review.json"

# 确保关键目录存在
for d in [
    TEMP_DIR,
    STRUCTURE_CACHE_DIR,
    VAULT_DIR,
    VECTOR_INDEX_DIR,
    KEYWORD_INDEX_DIR,
    PROMPT_DIR / "profiles",
    PROMPT_DIR / "schemas",
]:
    d.mkdir(parents=True, exist_ok=True)

# === 日志配置 ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
