# AI 售前作品集制作工作日志

本文件用于记录售前作品集的每个关键步骤。以后每完成一个步骤，都要同步记录：执行日期、关键命令、输入、产物、验证结果和下一步判断。

不要把 API Key、密码、真实客户资料、个人隐私数据或本机敏感路径写入日志。命令中的密钥统一使用占位符。

## 记录规则

每一步至少记录以下内容：

| 字段 | 说明 |
|---|---|
| 日期 | 实际执行日期 |
| 步骤 | 例如：企业 Demo、架构图、POC 报告、售前 PPT |
| 目标 | 这一步要证明什么能力 |
| 命令 | 可复现的关键命令；省略密钥 |
| 输入 | 使用的数据、报告、图片或代码版本 |
| 产物 | 文件路径、截图、视频或链接 |
| 验证 | 测试结果、指标或人工检查结果 |
| 决策 | 通过、返工、延期或进入下一步 |
| 限制 | 当前没有实现或不能对外承诺的内容 |

## 已完成基线

### 企业知识库 RAG POC

| 日期 | 步骤 | 关键命令 | 结果 |
|---|---|---|---|
| 2026-10-08 | 目标评估与评测层优化 | `uv run --no-sync pytest` | `213 passed, 1 warning` |
| 2026-10-08 | 22 条完整评测 | `uv run --no-sync python eval/run_eval.py --questions demo-data/enterprise-questions.jsonl --expected demo-data/enterprise-expected.jsonl --index-dir demo-data/enterprise-index --with-generation --claim-semantic-grounding --report-dir demo-data/enterprise-reports` | 生成完整评测报告，包含 `core_demo` 子集 |
| 2026-10-08 | 企业 Docker Demo | `$env:API_TOKEN='<占位符>'; .\tools\enterprise_demo.ps1 -Action Demo` | 16 文档、45 chunk、5/5 场景通过 |
| 2026-10-08 | 版本冻结 | `git commit -m "feat: package enterprise RAG presales baseline"` | commit `22dc574` |
| 2026-10-08 | 本地基线标签 | `git tag -a v0.1-enterprise-rag-poc -m "Enterprise RAG presales POC baseline"` | tag `v0.1-enterprise-rag-poc` |

对应材料：

- [企业 POC 评测报告](ENTERPRISE_POC_EVALUATION_REPORT.md)
- [企业 RAG 架构说明](ENTERPRISE_ARCHITECTURE.md)
- [企业成本模型](ENTERPRISE_COST_MODEL.md)
- [企业知识库面试问答](ENTERPRISE_INTERVIEW_QA.md)
- [企业 Demo 运行手册](ENTERPRISE_KB_DEMO_RUNBOOK.md)

## 下一步：创建售前方案 PPT

### 1. 开始前先确认的事项

在创建 PPT 文件之前，先回答以下问题，并把答案写入 PPT 首页备注或方案说明：

1. 面向谁：AI 售前面试官、潜在客户、技术负责人，还是销售/业务负责人？
2. 目标岗位：企业 AI 售前、RAG 解决方案、AI 实施，还是 FDE？
3. 主场景：企业知识库问答、故障排查、私有化部署，还是内部知识助手？
4. 主要业务价值：减少人工查资料、缩短故障定位时间、提高回答可追溯性，还是降低部署风险？
5. 需要证明的能力：需求分析、RAG 技术、PoC 评测、部署、权限边界，还是成本判断？
6. 哪些内容是真实运行结果，哪些内容是模拟资料和假设参数？
7. 哪些内容不能承诺：真实客户收益、生产准确率、真实 ACL 隔离、正式合同价格？

默认第一版 PPT 的定位：

> 面向企业知识库/RAG/Agent 售前岗位的模拟企业知识库 POC 方案。

### 2. PPT 创建前的资料盘点命令

```powershell
$repo = "D:\agent project\total\VideoContentKnowledgeBase-public"
Set-Location $repo

New-Item -ItemType Directory -Force -Path "portfolio\enterprise-rag-presales" | Out-Null

Get-ChildItem docs -File | Select-Object Name,Length,LastWriteTime
Get-ChildItem demo-data\enterprise-vault -File | Sort-Object Name | Select-Object Name,Length
Get-Content docs\ENTERPRISE_POC_EVALUATION_REPORT.md -Encoding UTF8
Get-Content docs\ENTERPRISE_ARCHITECTURE.md -Encoding UTF8
Get-Content docs\ENTERPRISE_COST_MODEL.md -Encoding UTF8
Get-Content docs\ENTERPRISE_KB_DEMO_RUNBOOK.md -Encoding UTF8
```

执行后记录：

- 使用的资料版本；
- PPT 引用的指标；
- PPT 中需要绘制的图；
- 仍需补充的截图或演示结果。

### 3. PPT 第一版建议结构

控制在 8～10 页，适合面试演示和售前沟通：

| 页码 | 页面 | 要回答的问题 |
|---:|---|---|
| 1 | 项目定位 | 这是为谁解决什么问题？ |
| 2 | 客户痛点与目标 | 为什么需要企业知识库？ |
| 3 | 需求边界 | 用户、资料、权限和验收标准是什么？ |
| 4 | 业务流程 | 用户提问后，系统如何处理？ |
| 5 | 技术架构 | 数据、检索、模型、引用和闸门如何连接？ |
| 6 | Demo 场景 | 产品咨询、故障排查、版本和部署问题如何回答？ |
| 7 | 安全与权限 | 无依据问题、越权和人工复核如何处理？ |
| 8 | POC 评测 | 5 条 Docker 场景和 22 条完整评测分别结果如何？ |
| 9 | 部署与成本 | API、云上和私有化如何选择？ |
| 10 | 边界与下一步 | 当前 POC 能交付什么，还需要验证什么？ |

### 4. PPT 制作时必须检查的问题

#### 叙事检查

- 是否从业务问题开始，而不是从 BGE-M3、FAISS 等技术名词开始？
- 每一项技术是否对应一个客户问题？
- 是否说明了客户、用户、资料、流程和成功标准？
- 是否把 Demo 结果和完整评测结果分开？

#### 证据检查

- `16 个文档、45 个 chunk` 是否标注为模拟企业资料？
- `5/5 场景通过` 是否对应 Docker 证据文件？
- `Recall@5=1.0` 是否标注为 22 条离线评测中的可回答问题？
- `Answer point coverage=0.4843` 和 `8 条 Claim review` 是否作为已知限制保留？
- 所有成本是否注明为假设参数，不写成真实报价？

#### 售前检查

- 是否说明需求调研要问哪些问题？
- 是否有 API、云部署、私有化和权限的选型判断？
- 是否说明遇到资料不足时如何转人工？
- 是否说明交付边界、验收指标和后续迭代？

### 5. PPT 生成后的验证命令

PPT 文件生成后，记录实际使用的渲染和检查命令。示例模板如下：

```powershell
Set-Location $repo

# 检查文件是否生成
Get-Item "portfolio\enterprise-rag-presales\enterprise-rag-presales.pptx"

# 检查目录内的交付文件
Get-ChildItem "portfolio\enterprise-rag-presales" -Recurse |
    Select-Object FullName,Length,LastWriteTime
```

还要人工检查：

- 所有文字是否完整显示；
- 架构图是否清晰；
- 指标和脚注是否对应报告；
- 页面是否出现“真实客户”“生产收益”等未经证明的表述；
- 导出 PDF 后页数、字体、中文显示和图片清晰度是否正常。

### 6. PPT 步骤完成记录模板

复制下面的模板追加到本文件末尾：

```markdown
### YYYY-MM-DD：售前 PPT 第一版

目标：

使用命令：

输入资料和版本：

生成产物：

验证结果：

发现的问题：

修改决定：

是否进入下一步：
```

## 后续材料顺序

PPT 完成并验证后，按以下顺序继续，每一步都追加日志：

1. POC 评测报告 PDF：记录导出命令、页数和视觉检查结果；
2. Token/API/部署成本表：记录参数来源、公式和假设；
3. 2 分钟 Demo 视频：记录录屏工具、脚本版本和视频文件；
4. AI 售前简历：记录使用的项目指标和目标岗位；
5. 目标岗位投递：记录公司、岗位、简历版本和反馈。

## 每次提交前的统一检查

```powershell
Set-Location "D:\agent project\total\VideoContentKnowledgeBase-public"
uv run --no-sync pytest
git diff --check
git status --short
git log -1 --oneline --decorate
```

提交时记录：

- commit hash；
- tag（如果创建）；
- 测试结果；
- 新增产物；
- 是否推送远程仓库。
