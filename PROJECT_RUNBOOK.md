# VideoContentKnowledgeBase 项目运行手册

本文档记录项目的当前真实实现、运行方式和操作细节。它不是远期方案文档，也不是优化清单；当 README、PLAN 或历史说明与源码不一致时，以当前源码和本文档为准。

## 1. 项目当前做了什么

`VideoContentKnowledgeBase` 是一个本地视频内容知识库系统。它把视频 URL 转成可检索、可引用、可用于 Agent 生成的结构化知识。

完整链路如下：

```text
视频 URL
  -> 查询平台人工字幕和自动字幕
  -> 可选检查已有本地视频的内嵌文本字幕轨
  -> 无字幕时 yt-dlp 下载音频并由 faster-whisper ASR 兜底
  -> 统一为带时间戳文本和来源元数据
  -> 按时间窗提取事实地图
  -> 生成共享全局主题概览
  -> 按所选 profile 综合为 JSON
  -> 渲染并写入 Obsidian Markdown
  -> bge-m3 生成嵌入
  -> FAISS + Whoosh 建立混合索引
  -> QueryEngine 统一检索
  -> Agent 执行问答、选题、脚本生成
```

主入口是 `pipeline.py`，核心模块在 `modules/` 目录。

## 2. 核心目录

```text
VideoContentKnowledgeBase/
├── pipeline.py              # 端到端流水线入口，执行 Step 1-5
├── config.py                # 路径、模型、设备、LLM、日志配置
├── modules/
│   ├── audio.py             # Step 1: 音频下载、归一化、fallback 调度
│   ├── transcribe.py        # Step 2: faster-whisper 转录，含长音频切片
│   ├── subtitle.py          # 平台/内嵌字幕发现、选择、解析和缓存
│   ├── transcript_parser.py # 时间戳解析、来源块和长文本窗口
│   ├── transcript_normalizer.py # 可追溯的 ASR 疑似术语注释
│   ├── content_map.py       # Step 3a: 事实地图提取、校验和缓存
│   ├── structure.py         # Step 3b: 全局概览、profile 综合和 Markdown 渲染
│   ├── obsidian_writer.py   # Step 4: 写入 Obsidian vault
│   ├── vault_loader.py      # 读取 Markdown 和转录时间戳
│   ├── chunk_splitter.py    # Markdown 切块和时间戳对齐
│   ├── indexer.py           # Step 5: FAISS + Whoosh 混合索引
│   ├── query_engine.py      # Step 6: 查询接口
│   └── agent.py             # Step 7: 问答、选题、脚本生成
├── vault/videos/            # Obsidian Markdown 知识库
├── temp/                    # 音频、转录、结构化中间文件
├── index/                   # 可重建索引和 pipeline 状态
├── tests/                   # pytest 测试
└── tools/cookies/           # 浏览器 Cookie 辅助工具，非主流程
```

### 外部 Obsidian 仓库

项目可通过 `OBSIDIAN_EXTERNAL_VAULTS` 只读关联外部 Obsidian 仓库。加载器会递归扫描其中的 Markdown，并识别
`source_url`、`profile` 以及 Web Clipper 常见的 `**0:00** · 文本` 时间轴；索引重建时
会将项目 `vault/videos` 与外部仓库合并去重。原始外部仓库不会被写入。

通过 `OBSIDIAN_EXTERNAL_VAULTS` 可配置一个或多个路径，Windows 使用 `;` 分隔；设置为空
字符串可禁用外部关联。

## 3. 环境要求

项目遵循工作区的 Python 环境隔离规范：

- 可复用 Python 解释器位于 `D:\mambaProject\VideoContentKnowledgeBase\mamba_env`
- 项目本地运行环境为 `.venv`
- `.venv` 通过 uv 创建，并继承 mamba 环境
- 不向系统 Python、全局环境或 `D:\Miniforge` base 环境安装项目依赖
- uv 维护命令应显式指定 mamba Python，并使用 `--no-python-downloads`

推荐环境创建方式：

```powershell
New-Item -Type Directory "D:\mambaProject\VideoContentKnowledgeBase"
mamba create -p "D:\mambaProject\VideoContentKnowledgeBase\mamba_env" python=3.12 ffmpeg -y

uv venv --python "D:\mambaProject\VideoContentKnowledgeBase\mamba_env\python.exe" --system-site-packages
uv sync --python "D:\mambaProject\VideoContentKnowledgeBase\mamba_env\python.exe" --no-python-downloads
```

## 4. 模型和外部服务

默认模型路径和服务配置在 `config.py` 中，可用环境变量覆盖。

| 项 | 默认值 | 说明 |
|---|---|---|
| `WHISPER_MODEL_PATH` | `D:/tool/faster-whisper-medium` | faster-whisper 模型目录 |
| `BGE_MODEL_PATH` | `D:/tool/bge-m3` | bge-m3 embedding 模型目录 |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama 服务地址 |
| `OLLAMA_MODEL` | `qwen2.5:7b` | 结构化和 Agent 默认模型 |
| `LLM_TIMEOUT_SECONDS` | `180` | Ollama 单次请求网络超时；语义检查失败时降级为人工复核 |
| `RERANKER_MODEL_PATH` | 空 | 可选本地 Cross-Encoder 路径；为空时使用 RRF |
| `RERANKER_CANDIDATE_K` | `20` | 启用重排时的候选数量 |
| `RERANKER_DEVICE` | 自动 | Cross-Encoder 推理设备 |
| `QUERY_PLANNER_ENABLED` | `false` | 是否对复杂问题启用受约束的 LLM 查询规划 |
| `QUERY_PLANNER_MODEL` | `qwen2.5:7b` | 查询规划使用的本地 Ollama 模型 |
| `QUERY_PLANNER_MAX_SUBQUERIES` | `3` | 查询规划最多拆分的子问题数 |
| `QUERY_MULTI_HOP_ENABLED` | `false` | 是否对第一跳未覆盖的子问题启用受约束迭代检索 |
| `QUERY_MULTI_HOP_MAX_HOPS` | `2` | 最大检索跳数，硬限制为 1-3 |
| `SEMANTIC_GROUNDING_MAX_CLAIMS` | `8` | 单次请求最多使用语义裁判的 Claim 数，超出部分转人工复核 |
| `QUERY_CACHE_ENABLED` | `true` | 是否启用有界内存查询缓存；索引 generation 变化时自动隔离 |
| `CLAIM_EVIDENCE_AUDIT_ENABLED` | `false` | 是否持久化 Claim/Evidence、证据指纹与索引 generation；记录可能包含敏感内容 |
| `QUERY_CACHE_SIZE` | `128` | 查询缓存最大条目数，设为 `0` 可关闭 |
| `API_RATE_LIMIT_PER_MINUTE` | `60` | 单客户端每分钟最大查询请求数，设为 `0` 可关闭 |
| `API_MAX_CONCURRENT_QUERIES` | `4` | API 同时执行的查询数上限 |
| `LLM_INPUT_COST_PER_1K` | `0` | `/metrics` 的输入 token 估算单价，仅用于运营估算 |
| `LLM_OUTPUT_COST_PER_1K` | `0` | `/metrics` 的输出 token 估算单价，仅用于运营估算 |
| `WHISPER_DEVICE` | `auto` | 自动选择 CTranslate2 CUDA，无 CUDA 时回退 CPU |
| `WHISPER_COMPUTE_TYPE` | `auto` | CUDA 使用 `float16`，CPU 使用 `int8` |
| `WHISPER_LANGUAGE` | `auto` | ASR 自动识别语言；可显式设为 `zh`、`en` 等 |
| `SUBTITLE_ENABLED` | `true` | 是否优先尝试字幕 |
| `SUBTITLE_LANGUAGES` | `zh-CN,zh-Hans,zh-Hant,zh,en,en-US,ja,ko` | 字幕语言优先顺序 |
| `SUBTITLE_ALLOW_ANY_LANGUAGE` | `true` | 首选语言不存在时是否接受其他语言 |
| `EMBEDDING_DEVICE` | 自动选择 | 未设置时优先 CUDA，否则 CPU |
| `SUMMARY_PROFILES` | `default` | 逗号分隔的默认综合方式，可多选 |
| `LLM_NUM_CTX` | `16384` | Ollama 上下文窗口 |
| `LLM_NUM_PREDICT` | `4096` | 单次综合最大生成 token |
| `LLM_NUM_PREDICT` | `4096` | 单次最大生成 token |
| `LLM_WINDOW_SECONDS` | `240` | 内容地图目标时间窗长度 |
| `LLM_WINDOW_MAX_CHARS` | `4800` | 单个内容窗口最大字符数 |
| `LLM_WINDOW_OVERLAP_SECONDS` | `20` | 相邻窗口只读上下文重叠 |

Ollama 准备：

```powershell
ollama pull qwen2.5:7b
$env:OLLAMA_HOST = "http://localhost:11434"
$env:OLLAMA_MODEL = "qwen2.5:7b"
```

## 5. 端到端导入视频

单个视频：

```powershell
uv run --no-sync python pipeline.py "https://www.bilibili.com/video/BV1xxxxx/"
```

一次生成多个总结类型：

```powershell
uv run --no-sync python pipeline.py --profiles default,medical,detailed "https://www.bilibili.com/video/BV1xxxxx/"
```

如果本地已经有 MP4/MKV 等媒体文件，可额外检查其中的文本字幕轨：

```powershell
uv run --no-sync python pipeline.py --media-path "D:\videos\example.mkv" "https://www.bilibili.com/video/BV1xxxxx/"
```

也可配置默认多选值：

```powershell
$env:SUMMARY_PROFILES = "default,medical"
uv run --no-sync python pipeline.py "https://www.bilibili.com/video/BV1xxxxx/"
```

批量视频：

```powershell
foreach ($url in Get-Content urls.txt) {
    uv run --no-sync python pipeline.py $url
}
```

强制重建索引或重新处理：

```powershell
uv run --no-sync python pipeline.py --force-reindex "https://www.bilibili.com/video/BV1xxxxx/"
```

CLI 会在同一个 `Pipeline` 上下文里处理传入 URL，以便复用 embedding 模型：

```powershell
uv run --no-sync python pipeline.py "url1" "url2" "url3"
```

## 6. Pipeline 执行细节

### Step 0: 状态检查

`Pipeline.run()` 先从 URL 中提取 `BV` 号。如果 URL 里没有 `BV...`，直接返回错误。

非 `--force-reindex` 场景下，`_already_processed()` 会检查：

- `index/pipeline_state.json` 中该视频和 profile 是否标记为 `completed`
- Markdown 文件是否存在
- FAISS 索引是否存在
- Whoosh 索引目录是否存在
- vector 和 keyword manifest 的 `generation_id`、`chunk_count` 是否一致
- `chunks.json` 是否包含对应的 `video_id + profile` 文档身份
- 派生总结命中时，检索结果中是否存在对应的 `retrieval_role=raw_backtrace` 原始证据
- 多问题查询的 `retrieval.meta.coverage` 是否覆盖各个子问题
- 多跳开启时，`retrieval.meta.hop_trace` 是否仅对 `missing`、`partial` 或 `unsupported` 的证据需求继续检索
- 启用语义检查时，`evidence_coverage` 是否对每个子问题给出 `covered`、`partial` 或 `unsupported`
- 启用语义检查时，`evidence_conflicts` 是否记录冲突证据及其 Chunk ID
- `claim_grounding.claims` 是否通过 `evidence_unit_ids` 关联证据，且派生总结未被单独标记为 `allowed_for_answer`
- `output_gate` 是否删除未获准 Claim，并确保响应引用与日志 `used_chunk_ids` 仅指向最终保留的 Claim
- 开启 Claim/Evidence 审计时，记录的 `index_generation_id` 和 `evidence_fingerprint` 是否可用于检测历史证据失效
- 审计生命周期扫描是否先以 preview 模式运行，确认 `invalidated`、`corrupt` 和 `expired` 数量后再显式 `--apply` 清理

只有这些条件都满足，才会跳过。

### Step 1: 文本来源选择

入口：`modules.subtitle.get_platform_transcript(url, video_id)`

执行顺序：

1. 复用已经校验过的统一转录；新增 profile 不会切换文本来源。
2. 查询平台人工字幕和自动字幕，先按配置语言排序，同语言优先人工字幕。
3. 传入 `--media-path` 时，检查该本地媒体已有的文本字幕轨。
4. 字幕不可用或解析失败时，进入音频下载和 ASR。

SRT 和 WebVTT 会转换成统一格式，原始字幕保存在 `temp/subtitles/`。统一转录固定写到 `temp/{BV号}_norm.txt`，元数据写到同名 `.meta.json`。OCR 硬字幕不在当前实现范围内。

### Step 2a: 音频获取（字幕不可用时）

入口：`modules.audio.get_audio(url)`

当前实际可用路径：

```text
yt-dlp 下载 bestaudio
  -> ffmpeg 转 WAV
  -> ffmpeg loudnorm 归一化
  -> 输出 temp/{video_id}_norm.wav
```

Playwright 抓流和 VB-Cable 录制是预留 fallback 接口，目前会返回未实现状态，不能作为稳定能力依赖。

### Step 2b: ASR 转录（字幕不可用时）

入口：`modules.transcribe.transcribe(audio_path, force=False)`

执行逻辑：

- 同名 `.txt` 只有在 `.meta.json` 中的音频 SHA-256、语言、Whisper 模型和计算配置全部匹配时才复用
- 默认 `WHISPER_LANGUAGE=auto`；长音频只在第一片检测语言，后续片段锁定该语言
- `--force-reindex` 会传入 `force=True`，强制重新转录
- 加载 faster-whisper 模型
- 探测音频时长
- 不超过 `MAX_DIRECT_DURATION` 时直接转录
- 超过阈值时按 `SEGMENT_MINUTES` 切片，并使用 `SEGMENT_OVERLAP` 重叠
- 每个切片转录后按切片起点修正全局时间戳
- 重叠区域使用文本相似度去重
- 转录文本和缓存元数据均通过同目录临时文件原子写入

输出格式：

```text
# Language: zh (probability: 0.9999)

[0.0s -> 3.2s] 第一段文本
[3.2s -> 8.5s] 第二段文本
```

### Step 3: 结构化

Pipeline 入口依次调用：

```text
modules.content_map.extract_content_map(transcript_text)
  -> modules.structure.extract_content_overview(content_map)
  -> modules.structure.structure_content_map(content_map, profile=...)
```

执行逻辑：

- 解析 `[start -> end]` 时间戳并合并为 `S0001`、`S0002` 等来源块
- 按时长和字符上限构造窗口；重叠来源仅作 `CONTEXT_ONLY`，不重复承担覆盖责任
- 对每个窗口提取全部独立案例、主题、观点、明确建议和不确定项
- 使用项目级缓存复用窗口结果和共享概览；转录、模型、提示词、Schema、术语表或窗口配置变化时自动失效
- 将各窗口压缩为内容摘要，生成一份所有 profile 共享的全局主题概览
- 程序把窗口整理为不重不漏的主题分区，并重新计算每组合法来源引用
- 各 profile 分别生成专属深度字段；标题、总摘要、主题和关键点由共享概览统一
- Markdown 额外写入程序生成的“内容脉络”，逐窗保留时间和 `Sxxxx` 来源

`prompts/schemas/content-map-v1.json` 是内部字段契约，并不把视频内容参数写死。
运行时会把当前窗口允许使用的 `source_refs` 和单元数量上限动态绑定到 Schema；
标题、单元类型、重要性、关键点和不确定项都来自当前转录。

默认 profile（v1）最终结果为：

```json
{
  "title": "视频标题",
  "summary": "一句话总结",
  "topics": ["主题"],
  "key_points": ["核心观点"],
  "insights": ["洞察"],
  "actions": ["行动建议"],
  "tags": ["标签1", "标签2", "标签3"]
}
```

校验规则：

- 不允许缺字段
- 不允许多字段
- `title`、`summary` 必须是字符串
- `topics`、`key_points`、`insights`、`actions`、`tags` 必须是列表
- `summary` 不得为空
- `tags` 至少 3 个
- 原文没有明确建议时 `actions` 可以为空
- 不允许外部 URL、无关 BV 号或点赞/关注等社交 CTA
- v2 的知识点、问题、优缺点、证据和风险必须引用内容地图中存在的 `Sxxxx`
- 原文不支持的 v2 栏目允许为空，不得为满足数量而补充外部知识
- v2 深度数组和单字段有运行时上限，防止本地模型输出过长而截断 JSON
- 共享概览必须覆盖每个有效窗口；重复或漏分配由程序归一化并再次校验

事实提取、概览和 profile 综合各自最多重试 3 次。多 profile 运行时，某个 profile
失败不会覆盖或撤销已经成功的其他 profile，最终状态会标记为 `partial`。

可用 profile：

| profile | 用途 |
|---|---|
| `default` | 通用结构化 |
| `detailed` | 深度知识整理 |
| `medical` | 医学健康内容 |
| `course` | 课程/教程内容 |
| `review` | 评测/方案比较 |

调用示例：

```powershell
uv run --no-sync python pipeline.py --profile detailed "https://www.bilibili.com/video/BV1xxxxx/"
uv run --no-sync python pipeline.py --profiles default,medical,detailed "https://www.bilibili.com/video/BV1xxxxx/"
```

### Step 4: 写入 Obsidian

入口：`modules.obsidian_writer.write_to_obsidian(markdown_content, url, output_dir, profile=...)`

文件 ID 规则：

```text
document_id = "{video_id}__{profile}"
vault/videos/{document_id}.md
```

同一个视频的不同 profile 使用不同文件。同一 `video_id + profile` 重跑时只覆盖自身，
不会覆盖该视频的其他总结类型。旧 URL 哈希文件仍可读取，但加载时会按复合身份去重，
并优先使用新命名文件。

Markdown frontmatter 使用 YAML 写入，包含：

- `title`
- `source`
- `id`
- `video_id`
- `profile`
- `schema_version`
- `prompt_version`
- `date`
- `tags`

### Step 5: 建立或更新索引

入口：`modules.indexer.HybridIndex`

索引包含两套结构：

| 类型 | 路径 | 用途 |
|---|---|---|
| FAISS | `index/vector/faiss.index` | 语义向量召回 |
| id map | `index/vector/id_map.json` | FAISS 数字 ID 到 chunk ID |
| chunks | `index/vector/chunks.json` | chunk 元数据 |
| manifest | `index/vector/manifest.json` | vector 索引版本信息 |
| Whoosh | `index/keyword/whoosh/` | BM25 / 关键词召回 |
| manifest | `index/keyword/whoosh/manifest.json` | keyword 索引版本信息 |

首次构建或强制重建：

```text
VaultLoader.load_all()
  -> ChunkSplitter.split()
  -> bge-m3 encode chunks
  -> 构建 FAISS
  -> 构建 Whoosh
  -> 校验临时索引
  -> 原子切换 index/vector 和 index/keyword/whoosh
```

增量添加：

```text
VaultLoader.load_one(video_id, profile)
  -> ChunkSplitter.split()
  -> 删除同 document_id 的旧 chunk
  -> 追加新 embedding 到 FAISS
  -> 更新 Whoosh
  -> 写入临时索引
  -> 校验并原子切换
```

索引更新由 `.index.lock` 跨进程串行化。原子切换通过 `.tmp_vector`、`.tmp_keyword`、
`.bak_vector_*`、`.bak_keyword_*` 和 `.swap_journal.json` 实现；若中途异常，下次初始化会
尝试恢复或回滚。索引 manifest 同时校验格式版本、Embedding 模型身份和向量维度，损坏或
不兼容时由 Pipeline 自动全量重建。

## 7. 查询知识库

示例：

```python
from pathlib import Path

from config import BGE_MODEL_PATH
from modules.embedding_runtime import SentenceTransformer
from modules.indexer import HybridIndex
from modules.query_engine import QueryEngine

model = SentenceTransformer(BGE_MODEL_PATH, device="cuda")
index = HybridIndex(Path("index"), model=model)
engine = QueryEngine(index)

result = engine.query("HIV感染者应该如何护理", top_k=5)
print(result)
```

查询规则：

- 空问题返回 `status = "empty"`
- `top_k < 1` 抛出 `ValueError`
- `top_k` 最大为 10
- 单条文本默认截断到 300 字

Hybrid 查询流程：

```text
问题
  -> bge-m3 encode
  -> FAISS top12
  -> Whoosh + jieba top12
  -> RRF(k=60) 融合
  -> 去重
  -> 返回 top_k
```

## 8. Agent 使用

示例：

```python
from pathlib import Path

from config import BGE_MODEL_PATH
from modules.embedding_runtime import SentenceTransformer
from modules.indexer import HybridIndex
from modules.query_engine import QueryEngine
from modules import agent

model = SentenceTransformer(BGE_MODEL_PATH, device="cuda")
index = HybridIndex(Path("index"), model=model)
engine = QueryEngine(index)

print(agent.answer_question(engine, "HIV感染者应该如何护理"))
print(agent.generate_topics(engine, "医学健康"))
print(agent.generate_script(engine, "DSD患儿家长的心理建设"))
```

Agent 不是直接生成内容，而是：

```text
QueryEngine 检索相关片段
  -> 拼接最多 2000 字上下文
  -> Ollama / Qwen2.5 生成回答
```

三个主要能力：

| 函数 | 用途 |
|---|---|
| `answer_question(engine, question)` | 基于知识库回答问题，并要求引用来源 |
| `generate_topics(engine, domain)` | 生成短视频选题和角度 |
| `generate_script(engine, topic)` | 生成 60 秒短视频脚本 |

## 9. 常用维护命令

运行测试：

```powershell
uv run --no-sync pytest
```

只运行某一组测试：

```powershell
uv run --no-sync pytest tests/test_indexer.py
uv run --no-sync pytest tests/test_transcribe.py
```

检查当前 Git 状态：

```powershell
git status --short --branch
```

查看 README 或源码中文内容：

```powershell
Get-Content -Raw -Encoding UTF8 README.md
Get-Content -Raw -Encoding UTF8 modules\audio.py
```

单独执行结构化：

```powershell
uv run --no-sync python run_structure.py "https://www.bilibili.com/video/BV1xxxxx/"
```

检查转录文件编码和中文字符：

```powershell
uv run --no-sync python check_text.py temp\BV1xxxxx_norm.txt
```

## 10. 主要落盘文件

| 文件或目录 | 来源 | 是否可重建 | 说明 |
|---|---|---|---|
| `temp/*.wav` | audio.py | 是 | 下载和归一化音频 |
| `temp/{BV号}_norm.txt` | subtitle.py / transcribe.py | 是 | 统一字幕或 ASR 转录结果 |
| `temp/{BV号}_norm.meta.json` | subtitle.py / transcribe.py | 是 | 文本来源、语言和缓存指纹 |
| `temp/subtitles/` | subtitle.py | 是 | 原始平台或内嵌字幕 |
| `temp/*_structured.json` | run_structure.py | 是 | 单步结构化调试产物 |
| `temp/*_structured.md` | run_structure.py | 是 | 单步结构化 Markdown |
| `temp/*__content_overview.json` | run_structure.py | 是 | 可检查的共享主题概览 |
| `temp/structure_cache/` | content_map.py / structure.py | 是 | 逐窗事实地图和共享概览缓存 |
| `vault/videos/*.md` | obsidian_writer.py | 否/半可重建 | 结构化知识库笔记 |
| `index/vector/*` | indexer.py | 是 | FAISS 和 chunk 元数据 |
| `index/keyword/whoosh/` | indexer.py | 是 | Whoosh 关键词索引 |
| `index/pipeline_state.json` | pipeline.py | 是/应谨慎 | 视频处理状态 |

`.gitignore` 已排除虚拟环境、临时文件、索引和凭据输出。

## 11. 已知限制

- 没有可用字幕时，音频获取仍主要依赖 yt-dlp；Playwright 和 VB-Cable 只是占位接口。
- OCR 硬字幕识别尚未实现。
- 平台字幕可能需要登录态；不可访问、内容为空或解析失败时会自动回退 ASR。
- LLM 结构化和 Agent 依赖本地 Ollama 服务，服务未启动时会失败。
- Bilibili 私有、会员、登录态或风控视频可能需要额外 cookie 支持。
- `tools/cookies/` 涉及浏览器凭据，只能在可信环境中临时使用，不应提交导出的 cookie 文件。
- README 和源码注释使用 UTF-8；PowerShell 未指定编码时可能显示乱码。
- 长视频外部分片会增加处理时间，并依赖重叠去重策略。

## 12. 排错手册

### 中文显示乱码

文件通常没有损坏，优先用 UTF-8 读取：

```powershell
Get-Content -Raw -Encoding UTF8 README.md
```

编辑器建议启用 EditorConfig 支持，项目根目录已声明 `.editorconfig`。

### yt-dlp 下载失败

检查：

- URL 是否可公开访问
- 当前网络是否能访问目标平台
- yt-dlp 版本是否过旧
- 是否需要 cookie
- `temp/` 所在磁盘是否有足够空间

当前 fallback 分支未实现，yt-dlp 失败通常会导致 Step 1 失败。

### ffmpeg 或 ffprobe 找不到

`config.py` 会按以下顺序查找：

1. `FFMPEG_BIN` / `FFPROBE_BIN` 环境变量
2. 项目 `.venv/Library/bin`
3. `.venv/pyvenv.cfg` 中 mamba home 的 `Library/bin`
4. `CONDA_PREFIX/Library/bin`
5. 系统 PATH

优先确认 mamba 环境中已安装 ffmpeg。

### 转录很慢

检查：

- `WHISPER_DEVICE` 是否为 `cuda`
- `WHISPER_COMPUTE_TYPE` 是否适合当前设备
- 音频是否超过 `MAX_DIRECT_DURATION` 触发切片
- GPU 显存是否被其他模型占用

### 结构化失败

检查：

- Ollama 是否启动
- `OLLAMA_HOST` 是否正确
- `OLLAMA_MODEL` 是否已经 pull
- LLM 输出是否符合当前事实提取、概览或 profile 阶段的 JSON Schema

### 查询时报 Index not built

说明索引没有成功加载。可尝试：

```powershell
uv run --no-sync python pipeline.py --force-reindex "https://www.bilibili.com/video/BV1xxxxx/"
```

如果只是索引损坏但 vault 笔记仍在，可以通过全量重建恢复索引。

### 同一个视频被跳过

这是正常去重逻辑。若确实要重新处理，使用：

```powershell
uv run --no-sync python pipeline.py --force-reindex "https://www.bilibili.com/video/BV1xxxxx/"
```

### Agent 回答没有时间戳

可能原因：

- 对应转录 `.txt` 文件缺失
- `VaultLoader` 没有找到匹配的 BV 转录文件
- chunk 无法对齐到 transcript segment

这种情况下结果会标记为无时间戳，Agent 上下文中显示 `N/A`。

## 13. 文档职责划分

建议保持以下职责边界：

| 文档 | 作用 |
|---|---|
| `README.md` | 快速介绍、环境搭建、基本使用 |
| `PROJECT_RUNBOOK.md` | 当前事实、操作细节、排错手册 |
| `PLAN.md` | 架构方案和设计说明 |
| `EXECUTION_PROTOCOL.md` | Agent 执行约束 |
| `OPTIMIZATION.md` | 已发现问题和优化清单 |

后续修改代码时，若改变了实际运行方式，应优先同步本文档。
