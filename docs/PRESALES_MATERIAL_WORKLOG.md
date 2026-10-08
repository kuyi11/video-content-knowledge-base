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

### 2026-10-08：审阅并修订第一版售前 PPT 执行方案

目标：核对评审意见中的指标、输入材料与制作顺序，并把可采纳项落实到本文档。

使用命令：`git status --short --branch`、`git log -4 --oneline --decorate`、`git tag --list "v0.1-enterprise-rag-poc"`、`rg -n "0\.4843|0\.4704|Claim review|Answer point coverage" docs/PRESALES_MATERIAL_WORKLOG.md docs/ENTERPRISE_POC_EVALUATION_REPORT.md`、PowerShell `Test-Path` 核验输入文件、`git diff --check`。

输入：代码/评测基线 `22dc574`（tag `v0.1-enterprise-rag-poc`）、工作日志提交 `1cbd81f`、评测报告日期 `2026-10-08`。

产物：修订本文件中的 PPT 受众、叙事、10 页核心结构、附录内容、验收与证据检查、竞品比较边界、成本敏感性和资料预检命令。

验证：评测报告、架构、成本模型、Demo 运行手册、面试问答和脱敏 Docker 证据文件均存在；工作区原先干净；`git diff --check` 通过。文档编辑后状态为仅本文件修改。

决策：采纳评审建议；不制作或发布 PPT，不变更 RAG 代码、不移动 tag、不推送远程。

限制：成本价格仍需在真正制作 PPT 时按届时供应商公开价格核验并记录来源/日期；替代方案仅可作定性比较，除非另有同集实测。

### 企业知识库 RAG POC

| 日期 | 步骤 | 关键命令 | 结果 |
|---|---|---|---|
| 2026-10-08 | 目标评估与评测层优化 | `uv run --no-sync pytest` | `213 passed, 1 warning` |
| 2026-10-08 | 22 条完整评测 | `uv run --no-sync python eval/run_eval.py --questions demo-data/enterprise-questions.jsonl --expected demo-data/enterprise-expected.jsonl --index-dir demo-data/enterprise-index --with-generation --claim-semantic-grounding --report-dir demo-data/enterprise-reports` | 生成完整评测报告，包含 `core_demo` 子集 |
| 2026-10-08 | 企业 Docker Demo | `$env:API_TOKEN='<占位符>'; .\tools\enterprise_demo.ps1 -Action Demo` | 16 文档、45 chunk、5/5 场景通过 |
| 2026-10-08 | 版本冻结 | `git commit -m "feat: package enterprise RAG presales baseline"` | commit `22dc574` |
| 2026-10-08 | 本地基线标签 | `git tag -a v0.1-enterprise-rag-poc -m "Enterprise RAG presales POC baseline"` | tag `v0.1-enterprise-rag-poc` |

基线说明：POC 代码与评测材料已在 `22dc574` 冻结并打 tag；本工作日志在后续文档提交 `1cbd81f` 中加入。PPT 引用评测时，以 [企业 POC 评测报告](ENTERPRISE_POC_EVALUATION_REPORT.md) 的 2026-10-08 结果为准：22 条全集 `Answer point coverage=0.4843`、`Claim review count=8`；5 条 `core_demo` 子集为 `0.6458`、复核数 4。不要沿用旧稿中的 `0.4704` 或 4 条全集 Claim review。指标复现可能受本地模型输出和环境影响，展示材料需同时写评测日期、模拟资料/离线范围及代码基线 `22dc574`（tag `v0.1-enterprise-rag-poc`）。

对应材料：

- [企业 POC 评测报告](ENTERPRISE_POC_EVALUATION_REPORT.md)
- [企业 RAG 架构说明](ENTERPRISE_ARCHITECTURE.md)
- [企业成本模型](ENTERPRISE_COST_MODEL.md)
- [企业知识库面试问答](ENTERPRISE_INTERVIEW_QA.md)
- [企业 Demo 运行手册](ENTERPRISE_KB_DEMO_RUNBOOK.md)

## 下一步：创建售前方案 PPT

### 1. 第一版受众与叙事主线

第一版主受众固定为 **AI 售前 / FDE 面试官**，用模拟企业知识库客户场景作为叙事外壳。不要同时按真实客户提案和求职作品集两种目的写，避免客户价值、个人贡献和项目边界混在一起。

创建文件前，把以下简报写入 PPT 备注或同目录方案说明：

1. 目标岗位：企业 AI 售前 / RAG 解决方案 / FDE；
2. 模拟场景：企业知识库中的产品咨询、故障排查、版本、部署与权限问题；
3. 要证明的能力：需求澄清、方案判断、端到端 PoC、评测解释、交付边界和成本意识；
4. 证据边界：16 篇模拟文档、45 个 chunk、自建 22 条问题集、离线评测；不代表真实客户或生产环境；
5. 不承诺：真实客户收益、生产准确率、已接入客户 SSO 的 ACL、正式合同报价或竞品性能优劣；
6. 每页写出一条“我的判断/取舍”，说明为什么采用该设计、它解决什么需求、留下什么限制。

默认第一版 PPT 的定位：

> 面向 AI 售前 / FDE 面试的模拟企业知识库 RAG POC 方案说明；用模拟客户问题展示需求分析、技术取舍、评测证据与交付边界。

### 2. PPT 创建前的资料盘点与基线核验

```powershell
$repo = "D:\agent project\total\VideoContentKnowledgeBase-public"
Set-Location $repo

New-Item -ItemType Directory -Force -Path "portfolio\enterprise-rag-presales" | Out-Null

Get-ChildItem docs -File | Select-Object Name,Length,LastWriteTime
Get-ChildItem demo-data\enterprise-vault -File | Sort-Object Name | Select-Object Name,Length
@(
    "docs\ENTERPRISE_POC_EVALUATION_REPORT.md",
    "docs\ENTERPRISE_ARCHITECTURE.md",
    "docs\ENTERPRISE_COST_MODEL.md",
    "docs\ENTERPRISE_KB_DEMO_RUNBOOK.md",
    "docs\ENTERPRISE_INTERVIEW_QA.md",
    "docs\demo-evidence\enterprise-demo-latest.json"
) | ForEach-Object { [pscustomobject]@{ Path = $_; Exists = Test-Path $_ } }
git status --short --branch
git rev-parse "v0.1-enterprise-rag-poc^{commit}"
git rev-parse --short HEAD
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
- 文件存在性及基线 commit/tag；若工作区有与评测相关的未提交修改，先审阅、测试并决定是否纳入冻结版本；不要把临时报告目录当作公开证据。

### 3. PPT 第一版建议结构：10 页核心 + 附录

先做 10 页核心内容，附录承载调研清单和技术细节，避免把主体页面塞满。

| 页码 | 页面 | 要回答的问题 |
|---:|---|---|
| 1 | 项目定位 | 这是为谁解决什么问题？ |
| 2 | 客户痛点与目标 | 为什么需要企业知识库？ |
| 3 | 需求边界与验收 | 用户、资料、权限、成功标准和待确认项是什么？ |
| 4 | 业务流程 | 用户提问后，系统如何处理？ |
| 5 | 技术架构 | 数据、检索、模型、引用和闸门如何连接？ |
| 6 | Demo 场景 | 产品咨询、故障排查、版本和部署问题如何回答？ |
| 7 | 安全与权限 | 无依据问题、越权和人工复核如何处理？ |
| 8 | POC 评测 | 5 条 Docker 场景、5 条 `core_demo` 子集和 22 条全集分别说明什么？ |
| 9 | 部署与成本 | API、云上和私有化如何选择？ |
| 10 | 边界与下一步 | 当前 POC 能交付什么，还需要验证什么？ |

附录建议包括：需求调研问题清单、22 条评测明细和 Claim 复核说明、成本公式/参数来源、角色与权限矩阵、风险与假设、替代方案比较。

验收指标页必须区分“本次观察值”和“客户验收目标”：Recall@5、MRR、source coverage、citation/claim grounding、answer point coverage、拒答、延迟、权限测试各自写明测量口径。当前 POC 指标是模拟资料离线观察值，不自动构成未来客户项目的 SLA 或验收阈值。

替代方案比较可对照传统关键词搜索、通用大模型直接问答、微调、传统知识库和 RAG，比较数据更新、来源追溯、初始投入、权限/部署要求、适用场景与限制。若没有在同一数据集做过对比测试，只能写定性选型逻辑，不要给竞品打性能分或宣称优劣。

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
- 指标是否注明评测日期 `2026-10-08`、模拟资料/离线范围、22 条全集或 `core_demo` 子集、基线 `22dc574` / tag `v0.1-enterprise-rag-poc`？
- 是否把 5 条 Docker 场景、5 条 `core_demo` 子集和 22 条全集分开表述？三者不是同一组请求。
- 所有成本是否注明为假设参数，不写成真实报价？
- 若展示模型/云服务单价，是否记录供应商、价格页面、查询日期、地域和计费单位？是否做低/基准/高用量或关键变量敏感性，而非只给一个总价？
- 是否明确区分当前实现、模拟设定和建议中的未来方案（尤其 ACL/SSO、生产监控与部署）？
- 每页是否有清楚的结论或取舍，而非只有技术组件列表？

#### 售前检查

- 是否说明需求调研要问哪些问题？
- 是否有 API、云部署、私有化和权限的选型判断？
- 是否说明遇到资料不足时如何转人工？
- 是否说明交付边界、验收指标和后续迭代？
- 是否列出需求澄清问题：文档量/格式/更新、用户角色与权限、日均/峰值请求、接口、部署/网络、敏感数据、响应时间、成功标准、人工审批与运维责任？
- 是否提供风险/假设页：模拟资料、离线评测、未接真实 SSO、成本依赖公开价格与客户参数、真实生产效果尚未验证？

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

### 2026-10-09：售前 PPT 第一版

目标：面向 AI 售前 / FDE 面试官，把模拟企业知识库 RAG POC 整理为可编辑的 10 页售前方案，展示需求澄清、技术取舍、评测证据、权限边界和交付计划。

使用命令：使用 Artifact Tool 生成 PPTX 和逐页 PNG；`mark_artifact_operation_started.mjs --operation-kind create --expected-output-count 1 --output-format pptx`；最终化校验检查包完整性、布局几何、字体、页数和标题适配；PowerShell `git diff --check`。

输入资料和版本：`ENTERPRISE_POC_EVALUATION_REPORT.md`、`ENTERPRISE_ARCHITECTURE.md`、`ENTERPRISE_COST_MODEL.md`、`ENTERPRISE_KB_DEMO_RUNBOOK.md`、`ENTERPRISE_INTERVIEW_QA.md`、`docs/demo-evidence/enterprise-demo-latest.json`；评测日期 `2026-10-08`；代码基线 `22dc574`，tag `v0.1-enterprise-rag-poc`。

生成产物：`portfolio/enterprise-rag-presales/enterprise-rag-presales.pptx`；10 页，包含封面、业务问题、需求澄清、方案流程、技术架构、Demo 场景、安全与权限、POC 评测、部署与成本、交付边界。

验证结果：10 页 PNG 逐页人工检查通过；PPTX 包完整性通过，布局 finding 为 0，字体策略通过，页数为 10，slide size 为 13.3333 × 7.5 英寸；最终文件 SHA-256 为 `f626f8d931de29dcfd963ad894c6143b872ed9d9d1402ae429145f9ecdac966f`；`git diff --check` 通过。

发现的问题：当前环境未提供模板选择器和 imagegen，因此正文使用原生可编辑文本、流程图、架构图和条形指标图，没有加入外部图片；最终化工具的 first-party import 检查因其子进程未继承运行时模块路径而关闭，之后已在配置运行时模块路径的独立命令中成功导入最终 PPTX，并确认 10 页。

修改决定：保留模拟资料、离线评测、未接真实 SSO/ACL、成本为假设参数等限制；不把 5 条 Docker 场景、5 条 `core_demo` 子集和 22 条全集混写；不把当前观察值写成客户 SLA 或生产收益。

是否进入下一步：是。下一步为导出 PDF 并检查中文字体与页数，然后制作成本表和 2 分钟 Demo 视频。

## 后续材料顺序

### 2026-10-09：按评测反馈修订售前 PPT

目标：依据完整评测报告修正指标展示、Docker 与离线集的边界、权限能力状态和封面占位内容，并输出反馈记录。

输入资料：用户提供的指标反馈；`docs/ENTERPRISE_POC_EVALUATION_REPORT.md`；`22dc574` / tag `v0.1-enterprise-rag-poc`。

生成产物：`portfolio/enterprise-rag-presales/enterprise-rag-presales-v3.pptx`；`portfolio/enterprise-rag-presales/PRESALES_PPT_FEEDBACK_REPORT.md`。

验证结果：Git 核验确认评测实现和测试已经包含在 tag 中，tag 未移动；PPT 10 页逐页检查通过；包完整性通过，布局 finding `0`、warning `0`，字体策略通过；Artifact Tool 独立导入确认为 10 页；最终 PPTX SHA-256 `8eb3611d345d33d848ad9a2def0b6414b3cef8a8d929de74a42d46851773b62e`；`git diff --check` 通过。

修改决定：采纳当前报告中的准确指标和三组证据隔离；封面模板残留已替换；权限页按“本地 POC 已验证 / 客户环境待验证 / 方案设计、尚未实现”分层。拒绝过时或混用值 `0.4704`、`0.75`、完整集 Claim review `4`、Claim citation `1.0` 和错误命名的 Claim semantic `0.8947`。不重复提交评测代码，不移动或重打 tag。

下一步：导出 PDF 检查中文显示，再完善成本表和制作 2 分钟 Demo 视频。

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

### 2026-10-08：PPT 措辞修正、公开证据同步与 PDF 导出

输入资料：用户提供的措辞与证据链反馈；`D:\.codex-pptx-build\build.mjs`；本地完整评测报告；公开 Docker 脱敏证据；代码基线 `22dc574`。

生成产物：`portfolio/enterprise-rag-presales/enterprise-rag-presales-v5.pptx`、`portfolio/enterprise-rag-presales/enterprise-rag-presales-v5.pdf`、`docs/demo-evidence/enterprise-demo-latest.json`。

验证结果：PPTX 为 10 页，包完整性、布局及字体检查通过；PDF 为 10 页，逐页渲染视觉检查通过，中文显示正常、图片清晰；第 7 页 ACL 未实现说明和第 8 页短语规则脚注存在；全稿未发现“转人工”“权限过滤”“Abstention accuracy”残留。完整测试 `213 passed, 1 warning`。PPTX SHA-256：`b67ef848bd00a44449459fc76fa4e5c3b0f5e7c4d5ea7d4c5a183e356cd39880`。

修改决定：只修正文案和证据链接，不改评测数字；边界表达命中率注明为短语规则结果，不代表通用拒答能力；人工边界统一表述为“提示人工确认”；业务域过滤明确标注 `domain=enterprise`。本地完整评测报告位于 `.gitignore` 排除的 `demo-data/enterprise-reports/`，其原因是报告内容未脱敏；公开同步使用 `docs/demo-evidence/enterprise-demo-latest.json`。Tag `v0.1-enterprise-rag-poc` 仍固定于 `22dc574`，未移动或重打。

### 2026-10-08：成本假设表与 2 分钟 Demo 脚本

输入资料：`docs/ENTERPRISE_COST_MODEL.md`、`docs/ENTERPRISE_KB_DEMO_RUNBOOK.md`、`docs/ENTERPRISE_POC_EVALUATION_REPORT.md` 和 2026-10-08 脱敏证据。

生成产物：更新 `docs/ENTERPRISE_COST_MODEL.md`，新增参数基准、供应商价格输入栏和月度用量/成本公式；新增 `docs/ENTERPRISE_DEMO_VIDEO_SCRIPT.md`，按时间轴区分 Docker 5/5、22 条完整离线评测和 5 条 `core_demo` 子集，并明确短语规则、SSO/ACL 与人工确认边界。

验证结果：参数覆盖问题量、Token、工作日、Embedding/重排调用、应用与数据资源、存储、GPU 和运维；单位价格均留为待核实输入，未表述为报价。讲稿时长合计 2 分钟，`git diff --check` 通过。

### 2026-10-09：v5 错字复核与 PDF 重新导出

检查 v5 第 3 页 PPTX XML，实际文字为“拒答边界”，没有“拒签边界”残留，因此保持已正确的 PPTX 不变。使用 `D:\.codex-pptx-build\export-pdf.ps1` 重新导出 `portfolio/enterprise-rag-presales/enterprise-rag-presales-v5.pdf`；`pdfinfo` 确认 10 页、960 × 540 pt。PDF SHA-256：`98328818b5d79a27552128947f1a85bb83120450ad0c44a39a22c5a680fd7ba5`。公开证据仍为 2026-10-08 / `22dc574`，包含 Docker 5/5、22 条完整指标与 5 条 `core_demo` 指标；成本表和 Demo 脚本已存在。
