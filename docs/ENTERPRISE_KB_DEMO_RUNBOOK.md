# 企业知识库 Demo 运行手册

本手册对应 `docs/ENTERPRISE_KB_DEMO_PLAN.md` 第一阶段产物。数据全部位于
`demo-data/enterprise-vault/`，是可公开展示的模拟企业资料，不代表真实客户或真实产品能力。

## 本地索引

在仓库根目录执行：

```powershell
$repo = (Get-Location).Path
$env:VAULT_DIR = "$repo\demo-data\enterprise-vault"
$env:INDEX_DIR = "$repo\demo-data\enterprise-index"
$env:TEMP_DIR = "$repo\demo-data\enterprise-temp"
$env:OBSIDIAN_EXTERNAL_VAULTS = ""
$env:TOOL_DIR = "D:\tool"
$env:BGE_MODEL_PATH = "$env:TOOL_DIR\bge-m3"
Set-Location $repo

uv run --no-sync python tools/rebuild_index.py
uv run --no-sync python tools/index_health.py
```

`TEMP_DIR`、`INDEX_DIR` 和企业资料目录与现有视频场景分开，避免覆盖原有索引和临时文件。

## 官方来源资料

与 FastGPT、FastAPI 相关的公开资料位于 `demo-data/official-sources/`，不在企业 Demo 默认索引中：

- `raw/`：FastAPI Deployment/Security、OpenAPI 3.2.1、Compose Specification、Python `venv` 原文及许可证文本；另有 Dify 项目许可证文本。
- `summaries/`：FastGPT、飞书知识库权限、Dify Knowledge、FastAPI Deployment/Security、OpenAPI、Compose 和 Python `venv` 的中文摘要，保留来源链接，不复制厂商文档页面原文。
- `sources.jsonl`：来源、版本、抓取日期、许可和 SHA-256 清单。

需要把官方资料纳入检索时，应先审核许可、版本和内容，再将选定文件复制到独立审核目录，并显式设置 `VAULT_DIR` 或 `OBSIDIAN_EXTERNAL_VAULTS`。不要直接把整个 `official-sources` 目录加入默认企业索引。

刷新可保留的官方原文并更新本地哈希清单：

```powershell
.\tools\refresh_official_sources.ps1
```

刷新后应检查 `sources.jsonl` 的来源 URL、抓取日期、许可证和本地路径。GitHub API 限流时脚本不会伪造 commit；清单会保留 raw URL 和本地 SHA-256，`source_revision` 需要在能够复核上游提交时再补齐。

如需把官方资料导入一次性演示索引，建议只复制经过审核的 `summaries/` 文件到独立目录，例如 `demo-data/official-review/`，然后暂时设置：

```powershell
$env:VAULT_DIR = "$repo\demo-data\official-review"
uv run --no-sync python tools/rebuild_index.py
```

不要把 `raw/`、许可证文本和企业模拟资料混在同一个默认索引中；导入摘要时仍应在回答中保留来源 URL。Dify 项目许可证包含额外的商业、多租户和前端署名条件，不能按普通 Apache 2.0 处理。

## API 查询

```powershell
$env:API_TOKEN = "replace-with-a-long-random-token"
uv run --no-sync uvicorn api:app --host 127.0.0.1 --port 8001
```

另开 PowerShell 窗口查询：

```powershell
$body = @{
    question = "客服无法接收新工单时，应该如何排查？"
    top_k = 5
    filters = @{ domain = "enterprise" }
    rewrite = $false
    semantic_grounding = $true
} | ConvertTo-Json -Depth 10

Invoke-RestMethod `
    http://127.0.0.1:8001/query `
    -Method Post `
    -Headers @{ Authorization = "Bearer $env:API_TOKEN" } `
    -ContentType "application/json" `
    -Body $body
```

检查 `retrieval.results` 的 `domain`、`document_id`、`section` 和 `chunk_id`，以及 `citations`、
`output_gate` 和 `answer_confidence`。响应还应包含 `answer_provider`、`answer_model`、
`answer_call_completed` 和 `llm_usage`，用于证明本次回答经过配置的 Ollama 模型完成；公开输出不应包含宿主机绝对路径。

## Docker 演示

先确认本机模型目录 `D:\tool\bge-m3` 存在。Docker 通过
`BGE_MODEL_HOST_PATH` 将它只读挂载到容器的 `/models/bge-m3`，容器内的
`BGE_MODEL_PATH` 固定为 `/models/bge-m3`。如模型放在其他位置，先覆盖主机路径变量：

```powershell
$env:BGE_MODEL_HOST_PATH = "D:\tool\bge-m3"
$env:API_TOKEN = "replace-with-a-long-random-token"
.\tools\enterprise_demo.ps1 -Action Config
.\tools\enterprise_demo.ps1 -Action Demo
```

`Demo` 会先在企业 Docker 服务镜像中执行 `tools/rebuild_index.py`，再启动 API，因此首次运行不要求主机预先生成企业索引。脚本使用 `docker-compose.yml` 加 `docker-compose.enterprise.yml`，将企业资料、索引和临时目录挂载到独立路径。它会执行五个快速演示问题，验证索引健康状态、企业域过滤、答案非空、引用、`high` 置信度和至少 50% 的 Claim 保留率；无依据问题另外检查产品边界表达。结果写入：

```text
demo-data/enterprise-reports/enterprise-demo-latest.json
```

公开仓库中的脱敏基线证据见
[`docs/demo-evidence/enterprise-demo-latest.json`](demo-evidence/enterprise-demo-latest.json)。
运行时报告可能包含更详细的请求字段，因此继续保存在被忽略的本地报告目录。

运行中的服务可用以下命令停止：

```powershell
.\tools\enterprise_demo.ps1 -Action Down
```

只重建企业索引而不启动 API：

```powershell
.\tools\enterprise_demo.ps1 -Action Build
```

## 当前边界

- 快速 Docker 演示仍是 5 条问题；完整离线评测集已扩展为 22 条，位于 `demo-data/enterprise-questions.jsonl` 和 `demo-data/enterprise-expected.jsonl`。
- 企业资料已扩展为 16 篇，覆盖产品 v1.1/v1.2、版本对照、权限矩阵、API 示例、工单、SLA、变更单、部署检查表和故障复盘。
- 当前 `version` 仍通过标题、正文和 `source` 表达，没有正式版本过滤字段。
- 权限矩阵用于说明产品边界和拒答测试；运行时尚未实现按用户 ACL 过滤检索结果。
- 企业 Demo 依赖本地 Embedding 模型和 Ollama 服务，未配置时只能完成静态语法和配置检查。

## 完整评测与版本基线

完整 22 条评测使用以下命令。问题文件中的 `cohort=core_demo` 会额外生成核心子集指标，避免把固定 Docker 场景与完整离线评测混为一组。

```powershell
$env:VAULT_DIR = "$repo\demo-data\enterprise-vault"
$env:INDEX_DIR = "$repo\demo-data\enterprise-index"
$env:TEMP_DIR = "$repo\demo-data\enterprise-temp"
$env:OBSIDIAN_EXTERNAL_VAULTS = ""
$env:BGE_MODEL_PATH = "D:\tool\bge-m3"

uv run --no-sync python eval/run_eval.py `
  --questions demo-data/enterprise-questions.jsonl `
  --expected demo-data/enterprise-expected.jsonl `
  --index-dir demo-data/enterprise-index `
  --with-generation `
  --claim-semantic-grounding `
  --report-dir demo-data/enterprise-reports
```

当前评测基线与解读见 [`docs/ENTERPRISE_POC_EVALUATION_REPORT.md`](ENTERPRISE_POC_EVALUATION_REPORT.md)。架构说明、成本测算和面试问答分别见 [`docs/ENTERPRISE_ARCHITECTURE.md`](ENTERPRISE_ARCHITECTURE.md)、[`docs/ENTERPRISE_COST_MODEL.md`](ENTERPRISE_COST_MODEL.md) 和 [`docs/ENTERPRISE_INTERVIEW_QA.md`](ENTERPRISE_INTERVIEW_QA.md)。

评测修改需要与企业问题和预期标签一起提交，提交前至少运行：

```powershell
uv run --no-sync pytest tests/test_rag_eval.py tests/test_enterprise_demo_evidence.py
git diff --check
```
