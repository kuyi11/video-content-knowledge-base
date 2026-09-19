# VideoContentKnowledgeBase

视频内容知识库 — 将 B站视频 URL 自动转化为可搜索、可提问的结构化知识系统。

> 使用 Codex 开发前请先阅读 [DEVELOPMENT_WORKFLOW.md](DEVELOPMENT_WORKFLOW.md)。日常开发目录是
> `VideoContentKnowledgeBase-public`；真实 vault、原始转录和完整历史保留在本地私有工作树中。

## 架构

```
B站 URL
  → Step 1: 平台人工字幕 → 平台自动字幕 → 可选本地视频内嵌字幕
  → Step 2: 无字幕时 yt-dlp + ffmpeg + faster-whisper ASR 兜底
  → 统一输出带时间戳文本和来源元数据
  → Step 3: 时间窗事实提取 → 全局主题概览 → 多 profile JSON 综合
  → Step 4: → Obsidian vault Markdown
  → Step 5: bge-m3 → FAISS + Whoosh 混合索引
  → Step 6: QueryEngine 统一查询接口
  → Step 7: Agent 问答/选题/脚本生成
```

## 目录结构

```
VideoContentKnowledgeBase/
├── pipeline.py              # 一键执行 Step 1-5
├── config.py                # 全局配置
├── modules/
│   ├── audio.py             # Step 1: 音频下载
│   ├── transcribe.py        # Step 2: ASR 转写
│   ├── subtitle.py          # 字幕发现、选择、解析和内嵌字幕抽取
│   ├── transcript_parser.py # 时间戳解析、来源块和长文本窗口
│   ├── content_map.py       # Step 3a: 可缓存的事实地图提取
│   ├── structure.py         # Step 3b: 全局概览和 profile 综合
│   ├── obsidian_writer.py   # Step 4: 写入 vault
│   ├── vault_loader.py      # 解析 vault 文档
│   ├── chunk_splitter.py    # 文本切块 + 时间对齐
│   ├── indexer.py           # Step 5: FAISS + Whoosh 索引
│   ├── query_engine.py      # Step 6: 查询接口
│   └── agent.py             # Step 7: Agent 问答/选题/脚本
├── vault/videos/            # Obsidian 知识库
├── index/                   # 索引持久化
│   ├── vector/faiss.index   # 向量索引
│   └── keyword/whoosh/      # BM25 关键词索引
└── temp/                    # 临时文件（音频、转录）
```

项目默认仅使用本地 `vault/videos`，不会读取个人 Obsidian 仓库。外部仓库会递归读取
Markdown，兼容 Web Clipper 的 `source_url` 和内嵌时间轴转录，不会复制或修改原文件。
可通过环境变量关联一个或多个外部仓库（Windows 使用分号分隔）：

```powershell
$env:OBSIDIAN_EXTERNAL_VAULTS = "D:\knowledge\obsidian;E:\another-vault"
# 关闭外部仓库
$env:OBSIDIAN_EXTERNAL_VAULTS = ""
```

## Data privacy

This public repository contains source code and synthetic examples only. Real video
transcripts, generated knowledge notes, browser cookies, local indexes, and private
evaluation outputs are intentionally excluded from Git history.

Copy `.env.example` to `.env` or set environment variables in your shell to point
the application at local data. `VAULT_DIR` controls generated notes,
`OBSIDIAN_EXTERNAL_VAULTS` adds read-only knowledge sources, and `INDEX_DIR` stores
rebuildable search indexes. See `examples/sample_note.md` for the public note format.

## 硬件需求

| 组件 | 最低要求 | 当前环境 |
|------|----------|----------|
| GPU | 8 GB VRAM | RTX 5060 Ti 16 GB |
| RAM | 16 GB | 32 GB |
| 磁盘 | 20 GB 可用 | D 盘 |

## 环境搭建

### 1. Python 环境（mamba + uv）

```powershell
# 可复用的 mamba Python 放在 D:\mambaProject，
# 项目实际运行环境固定在本项目的 .venv，不安装到 C 盘
New-Item -Type Directory "D:\mambaProject\VideoContentKnowledgeBase"
mamba create -p "D:\mambaProject\VideoContentKnowledgeBase\mamba_env" python=3.12 ffmpeg -y

# 在项目根目录执行，生成/使用 .venv
uv venv --python "D:\mambaProject\VideoContentKnowledgeBase\mamba_env\python.exe" --system-site-packages
uv sync --python "D:\mambaProject\VideoContentKnowledgeBase\mamba_env\python.exe" --no-python-downloads
```

### 2. 模型下载

| 模型 | 路径 | 命令 |
|------|------|------|
| faster-whisper-medium | `D:\tool\faster-whisper-medium\` | `huggingface_hub.snapshot_download("Systran/faster-whisper-medium", local_dir="D:/tool/faster-whisper-medium")` |
| bge-m3 | `D:\tool\bge-m3\` | `huggingface_hub.snapshot_download("BAAI/bge-m3", local_dir="D:/tool/bge-m3")` |

> 国内使用 `$env:HF_ENDPOINT = "https://hf-mirror.com"` 加速下载

### 3. Ollama + Qwen2.5

```powershell
# 安装
winget install Ollama.Ollama

# 模型存 D 盘
[Environment]::SetEnvironmentVariable("OLLAMA_MODELS", "D:\tool\ollama\models", "User")

# 拉取模型
ollama pull qwen2.5:7b

# 可选：覆盖 Ollama 服务地址和模型名
$env:OLLAMA_HOST = "http://localhost:11434"
$env:OLLAMA_MODEL = "qwen2.5:7b"
```

### 4. CUDA PyTorch

从 https://download.pytorch.org/whl/cu129/ 下载 `torch-2.9.0+cu129-cp312-cp312-win_amd64.whl`，然后：

```powershell
uv pip install torch-2.9.0+cu129-cp312-cp312-win_amd64.whl
```

## 使用

### 添加视频

```powershell
# 单个视频
uv run --no-sync python pipeline.py "https://www.bilibili.com/video/BV1xxxxx/"

# 指定单个提示词 profile（兼容选项）
uv run --no-sync python pipeline.py --profile detailed "https://www.bilibili.com/video/BV1xxxxx/"

# 同一次导入生成多个 profile，共享转录、事实地图和全局概览
uv run --no-sync python pipeline.py --profiles default,medical,detailed "https://www.bilibili.com/video/BV1xxxxx/"

# 对已有本地视频额外检查内嵌文本字幕轨
uv run --no-sync python pipeline.py --media-path "D:\videos\example.mkv" "https://www.bilibili.com/video/BV1xxxxx/"

# 批量
foreach ($url in Get-Content urls.txt) {
    uv run --no-sync python pipeline.py $url
}
```

自动执行：平台字幕 → 可选内嵌字幕 → ASR 兜底 → 结构化 → 写入 vault → 更新索引。
可用 profile：`default`、`detailed`、`medical`、`course`、`review`。

字幕和语言可通过环境变量配置：

```powershell
$env:SUBTITLE_ENABLED = "true"
$env:SUBTITLE_LANGUAGES = "zh-CN,zh-Hans,zh-Hant,zh,en,en-US,ja,ko"
$env:SUBTITLE_ALLOW_ANY_LANGUAGE = "true"
$env:WHISPER_LANGUAGE = "auto"
```

人工字幕优先于同语言的自动字幕。字幕成功时不会下载音频或加载 Whisper；没有可用字幕时才进入 ASR。OCR 硬字幕当前未实现。

未传参数时读取 `SUMMARY_PROFILES`，默认为 `default`：

```powershell
$env:SUMMARY_PROFILES = "default,medical"
uv run --no-sync python pipeline.py "https://www.bilibili.com/video/BV1xxxxx/"
```

多个 profile 不是互相拼接：系统先按时间窗提取一次事实地图，再生成一次共享全局
主题概览，最后让每个 profile 独立生成自己的深度字段。标题、总摘要、主题和关键点
使用同一概览，避免不同 profile 遗漏开头内容或把独立案例串联。

同一视频的不同 profile 会分别保存，例如 `BV1xxxxx__default.md` 和
`BV1xxxxx__detailed.md`。重复添加同一视频和同一 profile 会自动跳过；强制重跑时
只覆盖该 profile，不影响其他总结类型。

### 长视频结构化

长转录会拆成带 `Sxxxx` 来源编号的时间窗，逐窗提取所有独立案例、主题、建议和
不确定项。事实地图和共享概览缓存在 `temp/structure_cache/`，修改转录、模型、提示词、Schema、
窗口参数或术语表后会自动失效。

`prompts/schemas/content-map-v1.json` 固定的是内部数据结构，不是固定视频内容。
`source_refs` 的可选值、每窗最大单元数等参数由当前窗口在运行时动态写入 Schema；
`unit_type`、标题、摘要、关键点等内容均由该视频实际转录决定。

### 查询知识库

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

# 问答
print(agent.answer_question(engine, "HIV感染者应该如何护理"))

# 选题
print(agent.generate_topics(engine, "医学健康"))

# 脚本
print(agent.generate_script(engine, "DSD患儿家长的心理建设"))
```

### FastAPI 查询服务

接口返回结构化答案、经过校验的 chunk 引用、医学安全闸门、检索结果和查询日志路径。
请求中设置 `"semantic_grounding": true` 后，还会使用本地 Ollama 对每条 claim 与其
实际引用 chunk 做 `supported / contradicted / insufficient_evidence` 三分类。该选项
默认关闭，因为每条 claim 会增加一次模型调用；判定仅表示文本蕴含关系，不代表医学审核通过。
本地启动：

```powershell
uv sync --extra api --extra test --no-python-downloads
uv run --no-sync uvicorn api:app --host 127.0.0.1 --port 8000
```

本地部署建议绑定 `127.0.0.1`。Docker Compose 默认也只发布到本机；如设置
`API_TOKEN`，请求必须携带 `Authorization: Bearer <token>`。查询日志默认关闭，
如确实需要本地审计可设置 `QUERY_LOGGING_ENABLED=true`，但日志可能包含问题和检索片段。

查询示例：

```powershell
Invoke-RestMethod http://localhost:8000/query -Method Post -ContentType "application/json" `
  -Body '{"question":"HIV感染者应该如何护理","top_k":5,"semantic_grounding":true}'
```

可用端点：

- `GET /health`：检查索引、vault 清单和 FAISS/Whoosh 一致性。
- `POST /query`：参数为 `question`、可选 `top_k`、`filters`、`rewrite`。

Claim 语义裁判的正式口径现包含 30 条人工已审三分类样本，标签分布为
`supported` 11 条、`contradicted` 11 条、`insufficient_evidence` 8 条；这表示文本证据
关系已复核，不表示原始字幕真实性或医学事实已获专家认证。
在当前 33 文档、577 chunk 上重复运行 3 次 30 题生成评测，共覆盖 101 条 claim，
加权引用完整率和同模型语义支持率均为 `0.9901`，单轮范围为 `0.9697-1.0000`，4 条
进入复核。生成和第一裁判使用 `qwen2.5:7b`；独立的 `glm4:9b` 在 30 条人工集上
Accuracy 为 `0.9667`，与 Qwen 一致率为 `0.9667`。两者都只判断文本证据关系，不代表
医学真实性结论。

Docker Compose 只挂载模型、vault 和索引，不把这些本地数据写入镜像：

```powershell
$env:BGE_MODEL_HOST_PATH = "D:\tool\bge-m3"
$env:OBSIDIAN_HOST_PATH = "D:\knowledge\obsidian"
docker compose up --build
```

容器默认连接宿主机 `http://host.docker.internal:11434` 的 Ollama 服务。

### RRF 与 reranker 对比评测

使用同一份索引和人工标注集，对比 RRF Top 5 与“RRF 候选集 + CrossEncoder
重排”的 Recall@5、MRR、source coverage 和延迟。reranker 必须指定本地模型路径，
评测命令不会隐式下载模型：

```powershell
uv run --no-sync python eval/compare_reranker.py `
  --reranker-model "D:\tool\bge-reranker-v2-m3" `
  --top-k 5 `
  --candidate-k 20 `
  --baseline-output eval/baselines/reranker-comparison-expanded-20260918.json
```

结果写入 `eval/reports/reranker-comparison-*.json` 和对应 Markdown 表格。
当前 33 文档、577 chunk 基线中，RRF 的 Recall@5/MRR 为 `0.8571/0.7738`；
加入 reranker 和证据去重后为 `1.0000/0.9077`，来源覆盖率为 `0.9821`，平均总检索
延迟约从 `45 ms` 增至 `999 ms`。Recall@5 为 1 不表示全部来源齐全：跨视频题仍缺一个
目标来源。
优化方法、负向实验、延迟代价和采用决策统一记录在
[`eval/EXPERIMENT_LOG.md`](eval/EXPERIMENT_LOG.md)，结构化数据位于
`eval/experiment_registry.json`。
只运行单一 reranker 配置时，也可以使用：

```powershell
uv run --no-sync python eval/run_eval.py `
  --reranker-model "D:\tool\bge-reranker-v2-m3" `
  --reranker-candidate-k 20
```

完整的演示顺序、讲稿和请求示例见 `ENTERPRISE_DEMO.md`；最终评测快照见
`eval/baselines/final-evaluation-snapshot-20260919.json`。

### Obsidian

打开 `vault/` 目录即可浏览所有视频的结构化笔记。

## 技术栈

| 层 | 组件 |
|----|------|
| 字幕 | yt-dlp + 内置 SRT/WebVTT 解析 |
| 音频兜底 | yt-dlp + ffmpeg |
| ASR 兜底 | faster-whisper-medium |
| 结构化 | Ollama + Qwen2.5-7B |
| 嵌入 | BGE-M3 (1024d) |
| 向量索引 | FAISS IndexIDMap2(IndexFlatIP) |
| 关键词索引 | Whoosh + jieba |
| 融合 | RRF (k=60) |
| Agent | Ollama + Qwen2.5-7B |

## 注意事项

- 显存：Whisper (~4 GB) → Ollama (~5 GB) → bge-m3 (~2 GB)，三者串行不重叠，16 GB VRAM 够用
- 环境：用 mamba_env Python 运行，`uv run --no-sync` 避免覆盖 CUDA torch
- 模型：全部存 `D:\tool\`，不占 C 盘

## 编码与查看

项目文档和源码注释统一使用 UTF-8 编码。若在 Windows PowerShell 5.1 或未启用 UTF-8 的终端中看到中文乱码，通常是终端按本地 ANSI 编码读取了 UTF-8 文件；文件内容本身不一定损坏。

建议使用以下方式查看：

```powershell
Get-Content -Raw -Encoding UTF8 README.md
Get-Content -Raw -Encoding UTF8 modules\audio.py
```

仓库根目录的 `.editorconfig` 已声明 `charset = utf-8`，支持 EditorConfig 的编辑器会按 UTF-8 打开和保存文本文件。
