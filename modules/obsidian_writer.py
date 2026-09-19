import logging
from pathlib import Path

from config import VAULT_VIDEOS_PATH
from modules.document_identity import extract_video_id, make_document_id

logger = logging.getLogger(__name__)


def write_to_obsidian(
    markdown_content: str,
    url: str,
    output_dir: Path = None,
    overwrite: bool = False,
    profile: str = "default",
) -> str:
    """
    写入 Obsidian vault。

    Args:
        markdown_content: Markdown 内容
        url: 视频 URL（用于生成唯一 ID）
        output_dir: 输出目录，默认为 config.VAULT_VIDEOS_PATH
        overwrite: 文件存在时是否覆盖
        profile: 结构化提示词 profile

    Returns:
        写入的文件路径
    """
    video_id = extract_video_id(url)
    if not video_id:
        raise ValueError(f"Cannot extract video ID from URL: {url}")
    md_id = make_document_id(video_id, profile)
    out_dir = Path(output_dir) if output_dir else VAULT_VIDEOS_PATH
    file_path = out_dir / f"{md_id}.md"

    file_path.parent.mkdir(parents=True, exist_ok=True)

    if file_path.exists() and not overwrite:
        logger.info("[SKIP] %s already exists (duplicate URL: %s)", file_path, url)
        return str(file_path)

    file_path.write_text(markdown_content, encoding="utf-8")
    logger.info("[WRITE] %s", file_path)
    return str(file_path)
