# Docker Demo Runbook

本文档记录 Windows 主机上的 Docker Desktop 安装约束、Demo 操作和可复现输出。
Demo 面向当前 RAG API，不把模型、Obsidian vault 或索引复制进镜像，而是通过只读或持久化 volume 挂载。

## 1. 目标布局

| 内容 | 主机路径 | 容器路径 | 说明 |
| --- | --- | --- | --- |
| Docker Desktop 程序 | `D:\tool\Docker` | - | 安装程序位置 |
| Docker/WSL 数据 | `D:\DockerData` | - | 镜像、容器和 WSL 数据 |
| BGE-M3 模型 | `D:\tool\bge-m3` | `/models/bge-m3` | 只读挂载 |
| Obsidian vault | `${OBSIDIAN_HOST_PATH}` | `/vault/external` | 只读挂载 |
| RAG 索引 | `${INDEX_HOST_PATH}`（默认 `local-data\index`） | `/data/index` | 容器外持久化 |
| Demo 报告 | 项目 `eval\reports` | - | JSON 结果和 Compose 日志 |

Docker Desktop 的少量系统组件可能仍使用系统盘；本项目要求把程序目录和主要 WSL/Docker 数据放在 D 盘，不能把 Docker 数据根目录留在 C 盘。

## 2. 安装前检查

在 PowerShell 中执行：

```powershell
Get-ComputerInfo | Select-Object WindowsProductName, WindowsVersion, OsBuildNumber
Get-CimInstance Win32_Processor | Select-Object VirtualizationFirmwareEnabled
Get-Volume -DriveLetter D | Select-Object DriveLetter, SizeRemaining
wsl --status
docker version
Test-Path D:\tool\bge-m3
Test-Path $env:OBSIDIAN_HOST_PATH
```

Windows 10 19045 需要可用的 WSL2 后端。若 `wsl --version` 显示旧版 inbox WSL，应先通过 Windows 功能或官方 WSL 更新完成升级，再启动 Docker Desktop。

## 3. Docker Desktop 安装记录

安装包：`D:\tool\docker-installer\Docker Desktop_4.91.0_Machine_X64_exe_en-US.exe`

安装时使用以下目标：

- 程序目录：`D:\tool\Docker`
- WSL2/Docker 数据根目录：`D:\DockerData`
- 后端：WSL2

官方安装器参数等价于：

```text
--installation-dir=D:\tool\Docker
--backend=wsl-2
--wsl-default-data-root=D:\DockerData
```

安装完成后需要确认：

```powershell
docker version
docker info
wsl -l -v
```

若安装器或 WSL 要求重启，应先完成重启，再继续第 4 节。不要在未确认 Docker Desktop 已启动前执行 Compose。

## 4. Demo 执行步骤

进入项目目录：

```powershell
Set-Location '<path-to-video-content-knowledge-base>'
```

先验证 Compose 展开后的路径和挂载：

```powershell
.\tools\docker_demo.ps1 -Action Config
```

执行完整 Demo（一个正常回答场景 + 一个预期安全阻断场景）：

```powershell
.\tools\docker_demo.ps1 -Action Demo
```

默认场景分别限定到当前样本中的果糖视频和胃病视频。替换知识库后，必须同时替换
问题与对应 `video_id`。两个场景默认直接检索 `raw_transcript`，避免派生摘要被误当作
可直接回答的证据：

```powershell
.\tools\docker_demo.ps1 -NormalQuestion "索引中有明确证据的问题" `
  -NormalVideoId "BV..." -BlockedQuestion "来源中待核术语能否直接使用？" `
  -BlockedVideoId "BV..."
```

启动前必须设置用于本机 API 的随机令牌：

```powershell
$env:API_TOKEN = "replace-with-a-long-random-secret"
```

脚本会依次完成：

1. 设置 `BGE_MODEL_HOST_PATH`、`OBSIDIAN_HOST_PATH` 和 `INDEX_HOST_PATH` 为本机实际路径；
2. 执行 `docker compose config`；
3. 构建镜像并后台启动 `rag-api`；
4. 轮询 `GET /health`，直到服务加载模型和索引；
5. 发送正常问答与医学阻断两个验证请求到 `POST /query`，校验召回 `video_id` 以及期望/实际
   `safety_gate.action`、`output_gate.action`；
6. 正常问答还必须返回非空答案和至少一个 citation；
7. 保存回答、引用、grounding、检索结果和健康报告，并写入不含本地路径或证据正文的
   `docs/demo-evidence/docker-demo-latest.json`；
8. 保存最近 200 行容器日志；
9. 默认停止 Compose 服务。

保留服务以便浏览器演示：

```powershell
.\tools\docker_demo.ps1 -Action Demo -KeepRunning
Start-Process 'http://127.0.0.1:8000/docs'
```

手动操作：

```powershell
.\tools\docker_demo.ps1 -Action Up
.\tools\docker_demo.ps1 -Action Logs
.\tools\docker_demo.ps1 -Action Down
```

## 5. 结果检查清单

一次成功 Demo 至少应满足：

- `GET /health` 返回 `service=ready`；
- 健康报告中的 `missing_from_index`、`extra_in_index`、`changed_sources`、`metadata_errors` 为空；
- `/query` 返回 `answer`、`citations`、`claim_grounding`、`safety_gate` 和 `retrieval`；
- 正常场景 `answer_nonempty=true`、`citation_count>0`，两个场景的 `evidence_matched=true`；
- `citations` 包含 `chunk_id`、`source_refs`、视频 ID 和时间段，不返回主机文件路径；
- `eval/reports/docker-demo-*.json` 与对应 `*-compose.log` 均已生成；
- 关闭服务后，索引和 vault 文件没有被容器修改。

## 6. 执行日志

| 日期 | 操作 | 结果 | 报告 |
| --- | --- | --- | --- |
| 2026-09-19 | 重启后只读检查 | Windows 10 19045；D 盘剩余约 176.9 GB；固件虚拟化已开启；Docker 尚未安装；模型、vault、索引均存在 | 待安装 |
| 2026-09-19 | Docker Desktop 安装 | 完成；程序在 `D:\tool\Docker`，数据目录为 `D:\DockerData` | Docker Engine `29.8.0` |
| 2026-09-19 | Hyper-V/WSL 修复 | 完成；设置 `hypervisorlaunchtype=auto`，安装官方 WSL `2.7.13.0`，重启后 `HypervisorPresent=True` | Docker Engine 已启动 |
| 2026-09-19 | Compose Demo 首次构建 | 失败；Docker Hub `registry-1.docker.io:443` 连接超时，本机无 `python:3.12-slim` 缓存 | `eval/reports/docker-demo-*.json` 和 `*-compose.log` |
| 2026-09-19 | 配置 Docker Desktop 代理 | 完成；代理 `http://127.0.0.1:7897`，`hello-world` 和 `python:3.12-slim` 拉取成功 | Docker Hub 连通 |
| 2026-09-19 | 优化构建上下文 | 完成；排除 `.u`、`.uv-cache`、`.venv-funasr-test` 等开发缓存，context 从约 1.4 GB 降至约 190 KB | `.dockerignore` |
| 2026-09-19 | 修复跨 volume 模型/来源路径 | 完成；模型 ID 使用目录名和配置哈希，健康检查忽略仅由挂载产生的绝对路径差异 | `modules/indexer.py` |
| 2026-09-19 | Compose 基础设施运行 | API 和安全闸门可运行，但默认脑梗问题没有域内证据并召回胃病内容，因此不能作为有效问答演示 | 本地历史报告，不作为公开成功证据 |
| 2026-09-20 | 双场景 Compose Demo | 通过；索引健康，正常问题仅召回指定果糖视频、生成非空答案和 1 条 citation；医学术语案例仅召回指定胃病视频并由安全闸门阻断 | [`docs/demo-evidence/docker-demo-latest.json`](demo-evidence/docker-demo-latest.json) |

## 7. 故障处理

- `/health` 超时：查看 `docker compose logs --tail 200 rag-api`，通常是模型路径、索引路径或 WSL 资源问题。
- `invalid mount config`：重新执行 `-Action Config`，确认 D/F 盘路径存在且 Docker Desktop 已允许访问。
- `persisted index is unavailable`：先在宿主机运行 `uv run --no-sync python tools/index_health.py`，确认 `${INDEX_HOST_PATH}`（默认 `local-data\index`）完整。
- Ollama 不可用：Demo 仍可检查容器和 API，但需要在宿主机启动 Ollama，并确认 `OLLAMA_HOST=http://host.docker.internal:11434` 可达。
