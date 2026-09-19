# 视频内容知识库 — 技术实现细节

> 本文档描述每个步骤的具体命令、代码结构和配置参数。

---

## 环境依赖总览

```txt
# Python 3.10+
torch>=2.0.0
faster-whisper>=1.0.0
yt-dlp>=2024.0.0
ffmpeg-python>=0.2.0
playwright>=1.40.0
faiss-cpu>=1.7.0          # 或 faiss-gpu
sentence-transformers>=2.2.0
whoosh>=2.7.0
openai>=1.0.0
```

**系统工具（需单独安装）：**
- ffmpeg（PATH 可调用）
- yt-dlp（或 pip 安装）
- VB-Cable（仅 Windows 兜底方案需要）
- Playwright browsers（`playwright install chromium`）

---

## Step 1：音频获取 — 实现细节

### 1.1 yt-dlp 方案（优先级 1）

**具体命令：**

```bash
yt-dlp -x --audio-format wav \
  --audio-quality 0 \
  --postprocessor-args "ffmpeg: -ac 1 -ar 16000 -sample_fmt s16" \
  --output "temp/%(id)s.%(ext)s" \
  --rm-cache-dir \
  "{video_url}"
```

**参数说明：**

| 参数 | 作用 |
|------|------|
| `-x` | 提取音频 |
| `--audio-format wav` | 输出 WAV 格式 |
| `--audio-quality 0` | 最佳音频质量 |
| `-ac 1` | 强制单声道 |
| `-ar 16000` | 强制 16kHz 采样率 |
| `-sample_fmt s16` | 强制 16-bit |

**Python 调用代码结构：**

```python
import subprocess
import ffmpeg

def download_audio_ytdlp(url: str, output_dir: str) -> str:
    """
    返回: 音频文件路径
    失败: 抛出异常
    """
    output_template = f"{output_dir}/%(id)s.%(ext)s"
    cmd = [
        "yt-dlp", "-x", "--audio-format", "wav",
        "--audio-quality", "0",
        "--postprocessor-args", "ffmpeg: -ac 1 -ar 16000 -sample_fmt s16",
        "--output", output_template,
        "--rm-cache-dir",
        url
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr}")

    # 从输出解析实际文件名
    # yt-dlp 输出中会打印 [ExtractAudio] Destination: temp/xxx.wav
    for line in result.stderr.split("\n"):
        if "Destination:" in line:
            return line.split("Destination:")[1].strip()

    raise RuntimeError("Could not find output file from yt-dlp output")
```

### 1.2 归一化处理

**下载后必须执行归一化：**

```python
import ffmpeg

def normalize_audio(input_path: str, output_path: str):
    """
    峰值归一化到 [-1.0, 1.0]
    """
    (
        ffmpeg
        .input(input_path)
        .output(
            output_path,
            ac=1,           # 单声道
            ar=16000,       # 16kHz
            sample_fmt='s16', # 16-bit
            af='volume=1.0'  # 确保归一化（实际由 loudnorm 或 peak 控制）
        )
        .overwrite_output()
        .run(capture_stdout=True, capture_stderr=True)
    )
```

**更精确的归一化（推荐）：**

```python
def normalize_audio_peak(input_path: str, output_path: str):
    """
    先检测峰值，再归一化
    """
    # 1. 检测音频峰值
    probe = ffmpeg.probe(input_path)
    stream = next(s for s in probe['streams'] if s['codec_type'] == 'audio')

    # 2. 使用 loudnorm 或 volume filter 做峰值归一化
    (
        ffmpeg
        .input(input_path)
        .output(
            output_path,
            ac=1,
            ar=16000,
            sample_fmt='s16',
            af='loudnorm=I=-16:TP=-1.5:LRA=11'  # EBU R128 响度标准化
        )
        .overwrite_output()
        .run(capture_stdout=True, capture_stderr=True)
    )
```

### 1.3 Playwright 抓流方案（优先级 2）

```python
import asyncio
from playwright.async_api import async_playwright

async def capture_audio_playwright(url: str, output_path: str):
    """
    使用 Playwright 打开视频页面，通过 CDP 捕获音频流。
    注意：需要浏览器启动时开启 --enable-features=AllowAudioCapture
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            args=[
                '--autoplay-policy=no-user-gesture-required',
                '--enable-features=AllowAudioCapture'
            ]
        )
        page = await browser.new_page()
        await page.goto(url)

        # 查找 <video> 元素
        video = await page.query_selector('video')
        if not video:
            raise RuntimeError("No <video> element found")

        # Playwright 没有直接抓音频流的 API，
        # 实际实现需要结合 CDP (Chrome DevTools Protocol)
        # 捕获 MediaStream 然后通过 ffmpeg 转码
        # 这里使用替代方案：录制屏幕音频 + ffmpeg 提取

        # 替代方案：使用浏览器录制 MediaRecorder API
        await page.evaluate("""
            async () => {
                const stream = document.querySelector('video').captureStream();
                const recorder = new MediaRecorder(stream, {
                    mimeType: 'audio/webm'
                });
                const chunks = [];
                recorder.ondataavailable = e => chunks.push(e.data);
                recorder.onstop = () => {
                    const blob = new Blob(chunks, {type: 'audio/webm'});
                    // 通过某个方式传回给 Python
                };
                recorder.start();
                // 等待视频播放完成或超时
                await new Promise(r => setTimeout(r, 60000));
                recorder.stop();
            }
        """)

        await browser.close()
```

> **实际工程建议：** Playwright 方案实现复杂且不稳定，推荐将 yt-dlp 作为主要方案，仅在 yt-dlp 明确无法处理时（如某些直播流、DRM 保护内容）才使用此方案。

### 1.4 VB-Cable 系统录制（兜底方案）

**前置步骤（严格顺序）：**

```
Step A: 切换默认音频设备到 "CABLE Input" (VB-Cable)
         PowerShell: Set-DefaultAudioDevice -Name "CABLE Input"
Step B: 启动浏览器播放视频
Step C: 启动 ffmpeg 录制 "CABLE Output" 设备
```

**ffmpeg 录制命令（Windows）：**

```bash
ffmpeg -f dshow -i audio="CABLE Output" \
  -ac 1 -ar 16000 -sample_fmt s16 \
  -t 3600 \
  "{output_path}.wav"
```

**监听检测：** 每 5 秒检测音频能量，若连续 30 秒静音，判定视频已结束，停止录制。

```python
import ffmpeg
import numpy as np

def record_system_audio(output_path: str, timeout: int = 3600):
    """
    录制系统音频（VB-Cable 兜底方案）
    """
    process = (
        ffmpeg
        .input('audio=CABLE Output', f='dshow')
        .output(
            output_path,
            ac=1,
            ar=16000,
            sample_fmt='s16',
            t=timeout
        )
        .overwrite_output()
        .run_async(pipe_stderr=True)
    )
    return process
```

---

## Step 2：ASR 转录 — 实现细节

### 2.1 模型加载

```python
from faster_whisper import WhisperModel

# 加载模型（GPU）
model = WhisperModel(
    "medium",                    # 或 "large-v2"
    device="cuda",
    compute_type="float16",      # 半精度加速
    cpu_threads=4,
    num_workers=2
)
```

### 2.2 音频切片（>10分钟必须执行）

```python
import ffmpeg
import math

def slice_audio(input_path: str, output_dir: str, segment_minutes: int = 8, overlap_seconds: int = 10):
    """
    将长音频切为多段，每段 segment_minutes 分钟，段间 overlap 秒。

    返回: 切片文件路径列表 [(start_time, end_time, path), ...]
    """
    probe = ffmpeg.probe(input_path)
    duration = float(next(s for s in probe['streams'] if s['codec_type'] == 'audio')['duration'])

    segment_seconds = segment_minutes * 60
    slices = []
    start = 0.0

    while start < duration:
        end = min(start + segment_seconds, duration)
        output_file = f"{output_dir}/segment_{start:.0f}_{end:.0f}.wav"

        (
            ffmpeg
            .input(input_path, ss=start, t=end - start)
            .output(output_file, ac=1, ar=16000, sample_fmt='s16')
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )

        slices.append((start, end, output_file))
        start = end - overlap_seconds  # 10秒重叠

    return slices
```

### 2.3 执行转录

```python
def transcribe_segment(model: WhisperModel, audio_path: str, language: str = "zh") -> dict:
    """
    转录单个音频段。

    返回: {"segments": [{"start": 0.0, "end": 5.2, "text": "..."}]}
    """
    segments, info = model.transcribe(
        audio_path,
        language=language,
        vad_filter=True,           # 开启 VAD 去静音
        vad_parameters={
            "threshold": 0.5,
            "min_speech_duration_ms": 250,
            "max_speech_duration_s": 30,
            "min_silence_duration_ms": 500,
        },
        beam_size=5,
        best_of=5,
        temperature=[0.0, 0.2, 0.4],
        condition_on_previous_text=True,
        compression_ratio_threshold=2.4,
        log_prob_threshold=-1.0,
        no_speech_threshold=0.6,
    )

    result = {"segments": []}
    for seg in segments:
        result["segments"].append({
            "start": round(seg.start, 2),
            "end": round(seg.end, 2),
            "text": seg.text.strip()
        })

    return result
```

### 2.4 长视频完整转录流程

```python
def transcribe_long_audio(model: WhisperModel, audio_path: str, output_dir: str) -> dict:
    """
    完整转录流程：检查时长 → 切片(如需) → 逐段转录 → 合并
    """
    probe = ffmpeg.probe(audio_path)
    duration = float(next(s for s in probe['streams'] if s['codec_type'] == 'audio')['duration'])

    all_segments = []

    if duration <= 600:  # ≤ 10分钟
        result = transcribe_segment(model, audio_path)
        all_segments = result["segments"]
    else:
        # 切片
        slices = slice_audio(audio_path, output_dir, segment_minutes=8, overlap_seconds=10)

        # 逐段转录
        for start_time, end_time, seg_path in slices:
            result = transcribe_segment(model, seg_path)
            # 修正时间戳（加上偏移量）
            for seg in result["segments"]:
                seg["start"] = round(seg["start"] + start_time, 2)
                seg["end"] = round(seg["end"] + start_time, 2)
            all_segments.extend(result["segments"])

    # 去重重叠区域的重复文本
    all_segments = dedup_segments(all_segments)

    return {"segments": all_segments}

def dedup_segments(segments: list) -> list:
    """
    去除重叠区域的重复文本（简单策略：如果相邻段文本相似度 > 0.9，保留较长的）
    """
    if not segments:
        return segments

    deduped = [segments[0]]
    for seg in segments[1:]:
        prev = deduped[-1]
        # 如果时间有重叠且文本相似度很高，跳过
        if seg["start"] < prev["end"]:
            # 简单的文本去重：如果文本完全相同或包含关系
            if seg["text"] in prev["text"] or prev["text"] in seg["text"]:
                continue
        deduped.append(seg)

    return deduped
```

### 2.5 显存释放

```python
import torch
import gc

def release_gpu_memory():
    """完成 Whisper 转录后释放显存"""
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    gc.collect()
```

---

## Step 3：结构化 — 实现细节

### 3.1 LLM 调用

```python
import json
from openai import OpenAI

# 配置（本地 LLM 或 OpenAI API）
LLM_CONFIG = {
    "api_key": "sk-xxx",                    # 本地可设任意值
    "base_url": "http://localhost:8000/v1", # 本地 LLM 端点
    "model": "gpt-4o-mini",                # 或 "Qwen2.5-14B-Instruct" 等
}

client = OpenAI(**LLM_CONFIG)

SYSTEM_PROMPT = """你是一个视频内容结构化专家。

你的任务：
将视频转录文本转换为结构化 JSON。

输出规则（必须严格遵守）：
1. 只输出 JSON，不输出任何其他内容
2. JSON 必须完全符合以下 Schema
3. 不允许增加字段
4. 不允许缺失字段
5. 不允许空字段

Schema:
{
  "title": "视频标题（字符串）",
  "summary": "一句话总结（必须恰好1句话，不超过50字）",
  "topics": ["主题1", "主题2", "..."],
  "key_points": ["核心观点1", "核心观点2", "..."],
  "insights": ["洞察1", "洞察2", "..."],
  "actions": ["可执行行动建议1", "..."],
  "tags": ["标签1", "标签2", "标签3"]
}

约束：
- summary 必须恰好 1 句话
- tags 必须至少 3 个
- actions 必须至少 1 条"""

def structure_transcript(transcript_text: str, max_retries: int = 3) -> dict:
    """
    将转录文本结构化为 JSON。

    返回: 符合 Schema 的 dict
    失败: 重试 max_retries 次后抛出异常
    """
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=LLM_CONFIG["model"],
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"请结构化以下视频转录文本：\n\n{transcript_text}"}
                ],
                temperature=0.3,   # 低温度以保证稳定性
                response_format={"type": "json_object"},  # 如支持
            )

            content = response.choices[0].message.content.strip()
            # 移除 Markdown 代码块包裹（如果 LLM 加了）
            if content.startswith("```json"):
                content = content[7:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

            result = json.loads(content)

            # Schema 校验
            validate_structure_schema(result)

            return result

        except (json.JSONDecodeError, SchemaValidationError) as e:
            if attempt == max_retries - 1:
                raise RuntimeError(f"Failed to structure transcript after {max_retries} attempts: {e}")
            continue

    raise RuntimeError("Unexpected error in structure_transcript")
```

### 3.2 Schema 校验

```python
class SchemaValidationError(Exception):
    pass

def validate_structure_schema(data: dict):
    """
    校验结构化输出是否符合锁定 Schema。
    """
    REQUIRED_FIELDS = ["title", "summary", "topics", "key_points", "insights", "actions", "tags"]

    # 1. 检查字段完整
    for field in REQUIRED_FIELDS:
        if field not in data:
            raise SchemaValidationError(f"Missing field: {field}")

    # 2. 检查无多余字段
    for field in data:
        if field not in REQUIRED_FIELDS:
            raise SchemaValidationError(f"Unexpected field: {field}")

    # 3. 检查类型
    assert isinstance(data["title"], str), "title must be str"
    assert isinstance(data["summary"], str), "summary must be str"
    assert isinstance(data["topics"], list), "topics must be list"
    assert isinstance(data["key_points"], list), "key_points must be list"
    assert isinstance(data["insights"], list), "insights must be list"
    assert isinstance(data["actions"], list), "actions must be list"
    assert isinstance(data["tags"], list), "tags must be list"

    # 4. 检查约束
    assert len(data["summary"]) > 0, "summary cannot be empty"
    assert len(data["tags"]) >= 3, "tags must have at least 3 items"
    assert len(data["actions"]) >= 1, "actions must have at least 1 item"
    assert all(isinstance(t, str) and len(t) > 0 for t in data["tags"]), "all tags must be non-empty strings"
    assert all(isinstance(a, str) and len(a) > 0 for a in data["actions"]), "all actions must be non-empty strings"
```

### 3.3 JSON → Markdown 渲染

```python
from datetime import datetime

def render_markdown(data: dict, url: str, raw_summary: str) -> str:
    """
    将结构化 JSON 渲染为 Obsidian Markdown。

    严格按照锁定模板，不可修改结构。
    """
    md_id = hashlib.md5(url.encode()).hexdigest()[:12]
    date_str = datetime.now().strftime("%Y-%m-%d")
    tags_str = ", ".join(data["tags"])

    md = f"""---
title: {data['title']}
source: {url}
id: {md_id}
date: {date_str}
tags: [{tags_str}]
---

## 一句话总结

{data['summary']}

## 核心观点

"""
    for point in data["key_points"]:
        md += f"- {point}\n"

    md += "\n## 关键知识点\n\n"
    for insight in data["insights"]:
        md += f"- {insight}\n"

    md += "\n## 可执行行动\n\n"
    for action in data["actions"]:
        md += f"- {action}\n"

    md += f"\n## 原始摘要\n\n{raw_summary}\n"

    return md
```

---

## Step 4：Obsidian 写入 — 实现细节

```python
import hashlib
import os
from pathlib import Path

VAULT_PATH = "vault/videos"  # 可配置

def write_to_obsidian(markdown_content: str, url: str) -> str:
    """
    写入 Obsidian vault。

    返回: 写入的文件路径
    如果 ID 已存在，跳过写入（去重）。
    """
    # 生成 ID
    md_id = hashlib.md5(url.encode()).hexdigest()[:12]
    file_path = Path(VAULT_PATH) / f"{md_id}.md"

    # 确保目录存在
    file_path.parent.mkdir(parents=True, exist_ok=True)

    # 去重检查
    if file_path.exists():
        print(f"[SKIP] {file_path} already exists (duplicate URL: {url})")
        return str(file_path)

    # 写入文件
    file_path.write_text(markdown_content, encoding="utf-8")
    print(f"[WRITE] {file_path}")
    return str(file_path)
```

---

## Step 5：建立索引 — 实现细节

### 5.1 向量索引（FAISS + bge-m3）

```python
from sentence_transformers import SentenceTransformer
import faiss
import numpy as np

# 加载 Embedding 模型
embedding_model = SentenceTransformer(
    "BAAI/bge-m3",
    device="cuda"  # 或 "cpu"
)

class VectorIndex:
    def __init__(self, dimension: int = 1024):  # bge-m3 输出 1024 维
        self.dimension = dimension
        self.index = faiss.IndexFlatIP(dimension)  # 内积（余弦相似度）
        self.documents = []  # 存储原始文本
        self.metadata = []   # 存储 {video_id, start, end, source_url}

    def add_document(self, text: str, meta: dict):
        """添加单个文档到索引"""
        embedding = embedding_model.encode(text, normalize_embeddings=True)
        self.index.add(np.array([embedding], dtype=np.float32))
        self.documents.append(text)
        self.metadata.append(meta)

    def add_documents_batch(self, texts: list, metadata: list):
        """批量添加文档"""
        embeddings = embedding_model.encode(texts, normalize_embeddings=True, show_progress_bar=True)
        self.index.add(np.array(embeddings, dtype=np.float32))
        self.documents.extend(texts)
        self.metadata.extend(metadata)

    def search(self, query: str, top_k: int = 5) -> list:
        """
        向量检索。

        返回: [{"text": ..., "score": ..., "meta": {...}}]
        """
        query_embedding = embedding_model.encode(query, normalize_embeddings=True)
        scores, indices = self.index.search(
            np.array([query_embedding], dtype=np.float32),
            top_k
        )
        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx >= 0 and idx < len(self.documents):
                results.append({
                    "text": self.documents[idx],
                    "score": float(score),
                    "meta": self.metadata[idx]
                })
        return results
```

### 5.2 关键词索引（BM25 + Whoosh）

```python
from whoosh.index import create_in, open_dir
from whoosh.fields import Schema, TEXT, ID, NUMERIC
from whoosh.qparser import QueryParser
from whoosh.scoring import BM25F
import os
import tempfile

class KeywordIndex:
    def __init__(self, index_dir: str = "index/keyword"):
        os.makedirs(index_dir, exist_ok=True)

        self.schema = Schema(
            doc_id=ID(stored=True, unique=True),
            text=TEXT(stored=True),
            video_id=ID(stored=True),
            start=NUMERIC(stored=True),
            end=NUMERIC(stored=True),
            source_url=ID(stored=True)
        )

        if os.path.exists(os.path.join(index_dir, "MAIN.txt")):
            self.ix = open_dir(index_dir)
        else:
            self.ix = create_in(index_dir, self.schema)

        self.writer = self.ix.writer()
        self.doc_count = 0

    def add_document(self, text: str, meta: dict):
        """添加文档到 BM25 索引"""
        doc_id = f"{meta['video_id']}_{meta['start']}_{meta['end']}"
        self.writer.add_document(
            doc_id=doc_id,
            text=text,
            video_id=meta.get("video_id", ""),
            start=meta.get("start", 0),
            end=meta.get("end", 0),
            source_url=meta.get("source_url", "")
        )
        self.doc_count += 1

    def commit(self):
        """提交写入"""
        self.writer.commit()

    def search(self, query: str, top_k: int = 5) -> list:
        """
        BM25 检索。

        返回: [{"text": ..., "score": ..., "meta": {...}}]
        """
        with self.ix.searcher(weighting=BM25F()) as searcher:
            parser = QueryParser("text", self.ix.schema)
            parsed_query = parser.parse(query)
            results = searcher.search(parsed_query, limit=top_k)

            output = []
            for r in results:
                output.append({
                    "text": r["text"],
                    "score": r.score,
                    "meta": {
                        "video_id": r["video_id"],
                        "start": r["start"],
                        "end": r["end"],
                        "source_url": r["source_url"]
                    }
                })
            return output
```

### 5.3 Hybrid Search 合并

```python
class HybridSearch:
    def __init__(self, vector_index: VectorIndex, keyword_index: KeywordIndex):
        self.vector_index = vector_index
        self.keyword_index = keyword_index
        self.vector_weight = 0.6
        self.keyword_weight = 0.4

    def search(self, query: str, top_k: int = 5) -> list:
        """
        Hybrid Search：分别检索 → 合并 → 去重 → 重排序。
        """
        # 1. 向量检索
        vector_results = self.vector_index.search(query, top_k=top_k * 2)

        # 2. 关键词检索
        keyword_results = self.keyword_index.search(query, top_k=top_k * 2)

        # 3. 合并与重排序
        merged = {}

        for r in vector_results:
            key = r["text"][:100]  # 以文本前100字符作为去重key
            merged[key] = {
                "text": r["text"],
                "score": self.vector_weight * r["score"],
                "meta": r["meta"]
            }

        for r in keyword_results:
            key = r["text"][:100]
            if key in merged:
                merged[key]["score"] += self.keyword_weight * r["score"]
            else:
                merged[key] = {
                    "text": r["text"],
                    "score": self.keyword_weight * r["score"],
                    "meta": r["meta"]
                }

        # 4. 按分数降序排列
        sorted_results = sorted(merged.values(), key=lambda x: x["score"], reverse=True)

        return sorted_results[:top_k]
```

---

## Step 6：查询接口 — 实现细节

```python
class QueryEngine:
    def __init__(self, hybrid_search: HybridSearch):
        self.hybrid_search = hybrid_search

    def query(self, question: str, top_k: int = 5) -> dict:
        """
        查询接口。

        返回:
        {
            "question": "...",
            "results": [
                {
                    "text": "文本片段",
                    "score": 0.85,
                    "video_id": "xxx",
                    "start": 10.5,
                    "end": 30.2,
                    "source_url": "https://..."
                }
            ]
        }
        """
        results = self.hybrid_search.search(question, top_k=top_k)

        return {
            "question": question,
            "results": [
                {
                    "text": r["text"],
                    "score": r["score"],
                    "video_id": r["meta"].get("video_id", ""),
                    "start": r["meta"].get("start", 0),
                    "end": r["meta"].get("end", 0),
                    "source_url": r["meta"].get("source_url", "")
                }
                for r in results
            ]
        }
```

---

## Step 7：Agent 能力 — 实现细节

### 7.1 问答

```python
def answer_question(query_engine: QueryEngine, question: str, llm_client: OpenAI) -> str:
    """
    问答：检索上下文 → LLM 生成回答（带时间戳引用）。
    """
    # 1. Hybrid Search 检索
    search_results = query_engine.query(question, top_k=5)

    # 2. 构建上下文
    context_parts = []
    for r in search_results["results"]:
        context_parts.append(
            f"[{r['video_id']} {r['start']:.1f}s-{r['end']:.1f}s]: {r['text']}"
        )
    context = "\n".join(context_parts)

    # 3. LLM 生成回答
    response = llm_client.chat.completions.create(
        model=LLM_CONFIG["model"],
        messages=[
            {"role": "system", "content": "基于以下视频片段回答问题。引用时间戳。"},
            {"role": "user", "content": f"视频内容：\n{context}\n\n问题：{question}"}
        ],
        temperature=0.3
    )

    return response.choices[0].message.content
```

### 7.2 选题生成

```python
def generate_topics(query_engine: QueryEngine, domain: str, llm_client: OpenAI) -> list:
    """
    选题生成：检索相关视频 → 聚合主题 → LLM 生成选题。
    """
    # 1. 检索领域相关视频
    results = query_engine.query(f"{domain} 趋势 方向 选题", top_k=10)

    # 2. 提取所有知识片段
    knowledge = "\n".join([r["text"] for r in results["results"]])

    # 3. LLM 生成选题
    response = llm_client.chat.completions.create(
        model=LLM_CONFIG["model"],
        messages=[
            {"role": "system", "content": f"你是{domain}领域的选题策划专家。基于已有知识生成5个选题，每个选题给出3个不同角度。"},
            {"role": "user", "content": f"已有知识库内容：\n{knowledge}\n\n请生成5个{domain}领域的短视频选题，每个选题给出3个切入角度。"}
        ],
        temperature=0.7  # 适当创意
    )

    return response.choices[0].message.content
```

### 7.3 内容生成

```python
def generate_script(query_engine: QueryEngine, topic: str, llm_client: OpenAI) -> str:
    """
    内容生成：检索相关知识 → LLM 生成短视频脚本。
    """
    # 1. 检索相关知识
    results = query_engine.query(topic, top_k=8)

    knowledge = "\n".join([r["text"] for r in results["results"]])

    # 2. LLM 生成脚本
    response = llm_client.chat.completions.create(
        model=LLM_CONFIG["model"],
        messages=[
            {"role": "system", "content": "你是一个短视频脚本写手。基于知识库内容生成60秒短视频脚本。"},
            {"role": "user", "content": f"参考知识：\n{knowledge}\n\n选题：{topic}\n\n请输出：\n- 标题\n- 脚本正文（口播+画面）\n- 关键信息点"}
        ],
        temperature=0.5
    )

    return response.choices[0].message.content
```

---

## 完整 Pipeline 编排

```python
import json
import yt_dlp

class VideoPipeline:
    """
    完整 7 步 Pipeline。
    Agent 必须严格按顺序执行每一步。
    """

    def __init__(self, config: dict):
        self.config = config
        self.state = {"step": 0, "outputs": {}}

    def execute(self, url: str):
        """执行完整流程"""
        try:
            # Step 1: 音频获取
            self.state["step"] = 1
            audio_path = self._step1_get_audio(url)
            self.state["outputs"]["audio_path"] = audio_path

            # Step 2: ASR 转录
            self.state["step"] = 2
            transcript = self._step2_transcribe(audio_path)
            self.state["outputs"]["transcript"] = transcript

            # Step 3: 结构化
            self.state["step"] = 3
            structured = self._step3_structure(transcript, url)
            self.state["outputs"]["structured"] = structured

            # Step 4: 写入 Obsidian
            self.state["step"] = 4
            md_path = self._step4_write_obsidian(structured, url)
            self.state["outputs"]["md_path"] = md_path

            # Step 5-7 在后续阶段实现
            return self.state

        except Exception as e:
            return {
                "error": str(e),
                "step": self.state["step"],
                "state": self.state
            }
```

---

## 项目目录结构

```
video-knowledge-base/
├── main.py                 # 入口
├── config.py               # 配置
├── pipeline.py             # Pipeline 编排
├── requirements.txt        # 依赖
│
├── modules/
│   ├── audio.py            # Step 1: 音频获取
│   ├── transcribe.py       # Step 2: ASR 转录
│   ├── structure.py        # Step 3: 结构化
│   ├── obsidian_writer.py  # Step 4: Obsidian 写入
│   ├── indexer.py          # Step 5: 索引建立
│   ├── query_engine.py     # Step 6: 查询接口
│   └── agent.py            # Step 7: Agent 能力
│
├── temp/                   # 临时文件（音频、切片）
├── vault/videos/           # Obsidian 笔记输出
├── index/
│   ├── vector/             # FAISS 索引
│   └── keyword/            # Whoosh BM25 索引
│
└── tests/
    ├── test_audio.py
    ├── test_transcribe.py
    ├── test_structure.py
    └── test_index.py
```
