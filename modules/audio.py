"""
Step 1: 音频获取模块

主方案（当前可用）：
  1. yt-dlp 下载 bestaudio → WAV / 16kHz / mono / 16bit

规划中（接口已预留，尚未实现）：
  2. Playwright 抓取浏览器音频流
  3. VB-Cable 系统音频录制（兜底）

所有音频强制归一化为统一标准：
  - 格式: WAV
  - 采样率: 16000 Hz
  - 声道: 单声道 (mono)
  - 位深: 16-bit
  - 归一化: 峰值归一化到 [-1.0, 1.0]
"""

import logging
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import ffmpeg

from config import (
    TEMP_DIR,
    FFMPEG_BIN,
    AUDIO_SAMPLE_RATE,
    AUDIO_CHANNELS,
    AUDIO_SAMPLE_FMT,
    AUDIO_FORMAT,
)

logger = logging.getLogger(__name__)


# ============================================================
# 方案 1：yt-dlp（主方案）
# ============================================================

def download_audio_ytdlp(url: str, output_dir: Path = None) -> Path:
    """
    使用 yt-dlp 从视频 URL 下载音频。

    下载参数（锁定，不可修改）：
      -x                : 仅提取音频
      --audio-format wav: 输出 WAV 格式
      --audio-quality 0 : 最佳音质
      --postprocessor-args "ffmpeg: -ac 1 -ar 16000 -sample_fmt s16"
                         : 强制单声道 / 16kHz / 16bit

    Args:
        url: 视频 URL（YouTube / Bilibili / 其他 yt-dlp 支持的平台）
        output_dir: 输出目录，默认 TEMP_DIR

    Returns:
        下载的音频文件路径（WAV）

    Raises:
        RuntimeError: yt-dlp 下载失败时抛出
    """
    output_dir = Path(output_dir) if output_dir else TEMP_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    # 检查磁盘空间（预留 200MB）
    usage = shutil.disk_usage(output_dir)
    estimated = 200 * 1024 * 1024
    if usage.free < estimated:
        raise RuntimeError(
            f"Insufficient disk space: {usage.free // (1024*1024)} MB free, "
            f"need ~{estimated // (1024*1024)} MB"
        )

    # yt-dlp 输出模板：使用随机 ID 避免冲突
    output_template = str(output_dir / "%(id)s.%(ext)s")

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "-x",                              # 仅提取音频
        "--audio-format", AUDIO_FORMAT,    # WAV
        "--audio-quality", "0",            # 最佳音质
    ]
    if FFMPEG_BIN:
        cmd.extend(["--ffmpeg-location", str(FFMPEG_BIN)])
    cmd.extend(
        [
            "--postprocessor-args",
            f"ffmpeg: -ac {AUDIO_CHANNELS} -ar {AUDIO_SAMPLE_RATE} -sample_fmt {AUDIO_SAMPLE_FMT}",
            "--output", output_template,
            "--print", "after_move:filepath",  # 输出最终文件路径
            "--rm-cache-dir",
            "--no-playlist",                   # 不下载播放列表
            url,
        ]
    )

    logger.info("[yt-dlp] 开始下载: %s", url)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=600,  # 10 分钟超时
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"yt-dlp 下载失败 (exit code {result.returncode}):\n"
            f"STDERR: {result.stderr[-500:]}"
        )

    # 从 stdout 解析实际输出文件路径（--print after_move:filepath）
    audio_path = _parse_destination(result.stderr)
    if not audio_path:
        stdout_lines = [l.strip() for l in result.stdout.split("\n") if l.strip()]
        if stdout_lines:
            p = Path(stdout_lines[-1])
            if p.exists():
                audio_path = p
    if not audio_path:
        # 按输出模板预测路径
        import re as _re
        bv_match = _re.search(r"(BV[\w]+)", url)
        if bv_match:
            expected = output_dir / f"{bv_match.group(1)}.wav"
            if expected.exists():
                audio_path = expected
    if not audio_path:
        raise RuntimeError(
            f"无法从 yt-dlp 输出中解析文件路径。\n"
            f"STDERR: {result.stderr[-500:]}"
        )

    logger.info("[yt-dlp] 下载完成: %s", audio_path)
    return audio_path


def _parse_destination(stderr: str) -> Path | None:
    """从 yt-dlp 输出中解析 'Destination:' 行，得到输出文件路径。"""
    for line in stderr.split("\n"):
        if "Destination:" in line:
            path_str = line.split("Destination:")[1].strip()
            p = Path(path_str)
            if p.exists():
                return p
    return None


# ============================================================
# 归一化处理（所有方案共用）
# ============================================================

def normalize_audio(input_path: Path, output_path: Path | None = None) -> Path:
    """
    峰值归一化 + 格式标准化。

    通过 ffmpeg 执行：
      - loudnorm: EBU R128 响度标准化（I=-16, TP=-1.5, LRA=11）
      - 输出: 16000Hz / mono / 16bit WAV

    Args:
        input_path: 输入音频文件
        output_path: 输出路径，默认覆盖为 {name}_norm.wav

    Returns:
        归一化后的音频路径
    """
    if output_path is None:
        output_path = input_path.with_stem(input_path.stem + "_norm")

    logger.info("[normalize] 归一化: %s -> %s", input_path, output_path)

    try:
        (
            ffmpeg
            .input(str(input_path))
            .output(
                str(output_path),
                ac=AUDIO_CHANNELS,
                ar=AUDIO_SAMPLE_RATE,
                sample_fmt=AUDIO_SAMPLE_FMT,
                af="loudnorm=I=-16:TP=-1.5:LRA=11",  # EBU R128
            )
            .overwrite_output()
            .run(cmd=str(FFMPEG_BIN), capture_stdout=True, capture_stderr=True)
        )
    except ffmpeg.Error as e:
        raise RuntimeError(
            f"ffmpeg 归一化失败:\nSTDERR: {e.stderr.decode() if e.stderr else 'N/A'}"
        )

    logger.info("[normalize] 归一化完成: %s", output_path)
    return output_path


# ============================================================
# 方案 2：Playwright 抓流（fallback，待实现）
# ============================================================

async def capture_audio_playwright(url: str, output_path: Path) -> Path:
    """
    通过 Playwright 打开页面，从 <video> 元素捕获音频流。

    注意：
      - 需要 Playwright 浏览器：playwright install chromium
      - CHROME 启动参数需要：--enable-features=AllowAudioCapture
      - 此方案实现复杂且不稳定，当前为占位接口

    Raises:
        NotImplementedError: 待实现
    """
    raise NotImplementedError("Playwright 音频抓流方案尚未实现")


# ============================================================
# 方案 3：VB-Cable 系统音频录制（兜底，待实现）
# ============================================================
# 前置步骤（严格顺序）:
#   Step A: 切换默认音频设备到 "CABLE Input"
#           PowerShell: Set-DefaultAudioDevice -Name "CABLE Input"
#   Step B: 启动浏览器播放视频
#   Step C: 录制 "CABLE Output" 设备
#
# ffmpeg 录制命令:
#   ffmpeg -f dshow -i audio="CABLE Output" -ac 1 -ar 16000 -sample_fmt s16 output.wav
# ============================================================


def record_system_audio(output_path: Path, timeout_sec: int = 3600) -> subprocess.Popen:
    """
    VB-Cable 系统音频录制（仅 Windows）。

    前置条件：
      1. 安装 VB-Cable (https://vb-audio.com/Cable/)
      2. 切换默认音频输出到 "CABLE Input"
      3. 打开浏览器播放视频
      4. 调用此函数录制 "CABLE Output"

    Args:
        output_path: 输出 WAV 路径
        timeout_sec: 最大录制时长（秒），默认 1 小时

    Returns:
        ffmpeg 子进程对象（需要调用者管理生命周期）

    Raises:
        NotImplementedError: 完整的录制控制需配合设备切换和静音检测
    """
    raise NotImplementedError(
        "VB-Cable 系统音频录制待实现。"
        "需要: 1) 安装 VB-Cable, 2) 集成设备切换脚本, 3) 静音检测自动停止"
    )


# ============================================================
# 主入口：三级 fallback 调度
# ============================================================

def get_audio(url: str) -> Path:
    """
    音频获取主入口，按优先级依次尝试三级方案。

    Args:
        url: 视频 URL

    Returns:
        归一化后的 WAV 文件路径

    Raises:
        RuntimeError: 全部方案失败
    """
    errors = []

    # ── 方案 1：yt-dlp ──
    try:
        raw_path = download_audio_ytdlp(url)
        normalized = normalize_audio(raw_path)
        # 清理原始文件（保留归一化后的）
        if raw_path != normalized:
            raw_path.unlink(missing_ok=True)
        return normalized
    except Exception as e:
        errors.append(("yt-dlp", str(e)))
        logger.warning("[audio] yt-dlp 失败: %s", e)

    # ── 方案 2：Playwright 抓流 ──
    try:
        import asyncio
        fallback_path = TEMP_DIR / f"fallback_playwright_{uuid.uuid4().hex[:8]}.wav"
        asyncio.run(capture_audio_playwright(url, fallback_path))
        return normalize_audio(fallback_path)
    except NotImplementedError:
        errors.append(("Playwright", "方案尚未实现"))
    except Exception as e:
        errors.append(("Playwright", str(e)))
        logger.warning("[audio] Playwright 失败: %s", e)

    # ── 方案 3：VB-Cable ──
    try:
        fallback_path = TEMP_DIR / f"fallback_vbcable_{uuid.uuid4().hex[:8]}.wav"
        proc = record_system_audio(fallback_path)
        proc.wait(timeout=3600)
        return normalize_audio(fallback_path)
    except NotImplementedError:
        errors.append(("VB-Cable", "方案尚未实现"))
    except Exception as e:
        errors.append(("VB-Cable", str(e)))
        logger.warning("[audio] VB-Cable 失败: %s", e)

    # ── 全部失败 ──
    raise RuntimeError(
        f"所有音频获取方案均失败（共 {len(errors)} 个）。\n" +
        "\n".join(f"  [{i+1}] {name}: {err}" for i, (name, err) in enumerate(errors))
    )


# ============================================================
# 自测入口
# ============================================================

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python modules/audio.py <视频URL>")
        sys.exit(1)

    url = sys.argv[1]
    try:
        result = get_audio(url)
        print(f"\n✅ 音频就绪: {result}")
    except RuntimeError as e:
        print(f"\n❌ 音频获取失败: {e}")
        sys.exit(1)
