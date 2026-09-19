# 环境搭建方案 — mamba + uv 隔离管理

> 本文档与 [README.md](README.md) 使用同一套环境约定：mamba 解释器放在
> `D:\mambaProject`，项目依赖由 uv 安装到仓库内的 `.venv`。

---

## 可用工具

| 工具 | 路径 | 版本 |
|------|------|------|
| mamba | `D:\Miniforge\condabin\mamba.bat` | 2.1.1 |
| conda | `D:\Miniforge\Scripts\conda.exe` | 25.3.0 |
| uv | `uv`（已安装命令） | 0.8.14+ |
| Python | `D:\mambaProject\VideoContentKnowledgeBase\mamba_env\python.exe` | 3.12 |

---

## 环境管理方案

```
项目根目录/
├── .venv/              ← uv 创建，继承 mamba Python（隔离，在项目内）
├── .python-version     ← uv 识别 Python 版本
├── pyproject.toml      ← uv 管理依赖
└── uv.lock             ← uv 锁定版本
```

---

## 安装步骤

### 1. 用 mamba 创建可复用解释器（不碰 base）

```powershell
New-Item -ItemType Directory -Force "D:\mambaProject\VideoContentKnowledgeBase"
mamba create -p "D:\mambaProject\VideoContentKnowledgeBase\mamba_env" python=3.12 ffmpeg -y
```

- mamba 环境只提供 Python 和 ffmpeg 等可复用基础能力。
- 不向 `D:\Miniforge` base 或系统 Python 安装项目包。

### 2. 用 uv 创建项目 `.venv`

```powershell
uv venv --python "D:\mambaProject\VideoContentKnowledgeBase\mamba_env\python.exe" --system-site-packages
```

`.venv\pyvenv.cfg` 的 `home` 应指向上述 mamba 环境。

### 3. 按锁文件同步依赖

```powershell
uv sync `
  --python "D:\mambaProject\VideoContentKnowledgeBase\mamba_env\python.exe" `
  --no-python-downloads
```

所有依赖以 `pyproject.toml` 和 `uv.lock` 为准，不逐项执行 `uv pip install`。

### 4. 日常使用

```powershell
uv run --no-sync python pipeline.py "https://www.bilibili.com/video/BV1xxxxx/"

# 维护依赖时始终指定 mamba Python
uv add <package> `
  --python "D:\mambaProject\VideoContentKnowledgeBase\mamba_env\python.exe" `
  --no-python-downloads
```

---

## 依赖分层（按 MVP 阶段）

| 阶段 | 依赖 | 大小估算 |
|------|------|----------|
| Phase 1 | yt-dlp, ffmpeg, faster-whisper, ollama | ~3-4 GB（含模型） |
| Phase 2 | + sentence-transformers, faiss-cpu, whoosh, ffmpeg-python | ~+2-3 GB |
| Phase 3 | playwright（可选，浏览器缓存放在项目内） | ~+300 MB |
| 全部 | 以上全部 | ~6-8 GB |

---

## 与 pip venv 方案对比

| 对比项 | pip venv | mamba + uv |
|--------|----------|------------|
| ffmpeg 安装 | 需单独去官网下载 | mamba install 一键 |
| torch 安装 | pip 可能缺 CUDA | mamba 自动匹配 CUDA |
| 环境隔离 | ✅ | ✅ |
| 安装速度 | 慢 | uv 快 10-20x |
| 全局污染 | ❌ 不污染 | ❌ 不污染 |

---

## 卸载（完整清理）

```bash
# 删除前先确认环境和缓存不再需要；uv.lock 应保留在仓库中
Remove-Item -Recurse -Force .venv
```
