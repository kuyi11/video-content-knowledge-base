# 企业知识库通用 Demo 实施方案

更新时间：2026-10-05

## 1. Demo 目标

在现有视频内容知识库 RAG 项目的基础上，增加一个面向企业客户的通用知识库场景，用于展示：

- 企业资料接入与结构化整理；
- Embedding、BM25、RRF 混合检索；
- 来源引用和证据追溯；
- 无依据问题拒答；
- 权限、版本和部署边界意识；
- API、私有化部署和售前方案表达能力。

这个 Demo 不需要重新开发一套 RAG 系统，也不应虚构真实客户。建议使用“星云企业工单与知识管理平台”作为虚拟产品名称，所有资料标记为“模拟企业资料”或“基于公开官方文档整理”。

## 2. 场景设定

### 2.1 虚拟产品

产品名称：星云企业工单与知识管理平台。

目标客户：中小企业、客服团队、IT 运维团队和软件实施团队。

目标用户：客服人员、实施顾问、售前工程师、管理员和内部员工。

### 2.2 业务痛点

- 产品手册、FAQ、部署文档和故障记录分散；
- 客服和实施人员需要反复查找资料；
- 不同版本的功能和部署要求容易混淆；
- 企业客户经常询问 API、权限、私有化和部署资源；
- 对没有证据的问题，模型容易编造答案。

### 2.3 系统边界

系统可以：

- 检索产品、FAQ、部署和权限资料；
- 输出带来源的产品说明；
- 总结故障排查步骤；
- 回答 API 和私有化部署问题；
- 对资料中没有依据的问题返回证据不足提示。

系统不应声称：

- 已服务真实客户；
- 已产生真实节省成本或客户收益；
- 能直接修改客户生产数据库；
- 能代替企业管理员执行权限操作；
- 模拟资料代表真实产品能力。

## 3. 推荐公开资料来源

公开资料只用于理解结构和整理来源，不要将官方文档整站复制到仓库或作品集。

| 资料方向 | 官方来源 | 在 Demo 中的用途 |
|---|---|---|
| 产品和知识库流程 | [FastGPT 快速上手](https://doc.fastgpt.cn/zh-CN/guide/getting-started/quick-start) | 参考知识库创建、文件上传、解析和工作流流程 |
| 检索机制 | [FastGPT 知识库搜索方案和参数](https://doc.fastgpt.cn/zh-CN/guide/dataset/dataset_engine) | 整理 Embedding、知识库、集合和检索参数 |
| 数据导入 | [FastGPT 模板导入](https://doc.fastgpt.cn/zh-CN/guide/dataset/template) | 参考 q、a、index 和 metadata 数据结构 |
| API 对接 | [FastGPT API 文档](https://doc.fastgpt.cn/zh-CN/openapi/intro) | 参考 API Key、应用调用和接口说明 |
| 知识库 API | [FastGPT 知识库接口](https://doc.fastgpt.cn/zh-CN/openapi/dataset) | 参考创建、更新和管理知识库的场景 |
| 私有化部署 | [FastGPT Docker Compose 部署](https://doc.fastgpt.cn/zh-CN/self-host/deploy/docker) | 参考数据库、向量库、AI Proxy 和资源配置 |
| 企业权限 | [飞书知识库权限](https://www.feishu.cn/hc/zh-CN/articles/821998241087-%E5%BF%AB%E9%80%9F%E4%BA%86%E8%A7%A3%E7%9F%A5%E5%BA%93%E6%9D%83%E9%99%90) | 参考管理员、编辑成员、阅读成员和页面权限 |
| 竞品参考 | [Dify 知识库介绍](https://docs.dify.ai/guides/knowledge-base/retrieval) | 作为低代码 RAG 和自部署能力的对比资料 |

## 4. 数据目录

先创建独立数据目录，不要直接覆盖现有视频知识库数据：

```text
demo-data/
└── enterprise-vault/
    ├── 01-product-overview-v1.2.md
    ├── 02-knowledge-base-standard-v1.2.md
    ├── 03-retrieval-and-citation-v1.2.md
    ├── 04-permission-and-security-v1.2.md
    ├── 05-api-and-integration-v1.2.md
    ├── 06-private-deployment-v1.2.md
    └── 07-faq-and-troubleshooting-v1.2.md
```

### 4.1 文档内容建议

`01-product-overview-v1.2.md`

- 产品定位；
- 目标客户和用户；
- 工单、SLA、知识库和报表功能；
- 不支持的功能和产品边界。

`02-knowledge-base-standard-v1.2.md`

- 支持的文档格式；
- 文档清洗、切分和 metadata 规范；
- 文档版本管理；
- 数据更新和重新索引流程。

`03-retrieval-and-citation-v1.2.md`

- 向量检索和关键词检索的区别；
- 混合检索和重排流程；
- Citation 的生成规则；
- 没有足够证据时的处理方式。

`04-permission-and-security-v1.2.md`

- 管理员、编辑成员和阅读成员；
- 知识库和页面权限；
- 对外分享限制；
- 未授权内容不能被回答；
- 审计和日志边界。

`05-api-and-integration-v1.2.md`

- API Key 使用方式；
- 应用调用流程；
- 知识库更新接口；
- 第三方系统接入边界；
- 接口失败和权限错误处理。

`06-private-deployment-v1.2.md`

- 应用服务；
- 向量数据库；
- 业务数据库；
- 对象存储；
- 模型服务或 AI Proxy；
- 网络、权限、日志和备份；
- API 部署与私有化部署的选择条件。

`07-faq-and-troubleshooting-v1.2.md`

- 无法上传文档；
- 检索不到正确内容；
- 回答没有引用；
- API 鉴权失败；
- 私有化部署无法连接模型服务；
- 问题需要转人工的条件。

## 5. Markdown 元数据模板

现有项目支持 `domain`、`quality`、`review_status`、`source_of_truth`、`risk_level` 等字段。每篇文档的正文中也要写明版本和资料性质。

```markdown
---
id: enterprise-product-overview-v12
source: portfolio://enterprise-kb/product-overview/v1.2
profile: default
chunk_type: document
domain: enterprise
quality: human_reviewed
review_status: reviewed
source_of_truth: true
risk_level: low
title: 星云企业工单平台产品介绍 v1.2
---

# 星云企业工单平台产品介绍 v1.2

资料性质：模拟企业资料，基于公开产品文档结构整理。
适用角色：客服、售前、实施和企业管理员。

## 产品定位

...
```

当前索引过滤字段中没有正式的 `version` 字段。因此第一版应在标题、正文和 `source` 中明确版本，但不要在简历里声称已经实现严格的版本 metadata 过滤。后续若需要正式版本过滤，再扩展 `Document`、`Chunk`、索引序列化和 API filter。

## 6. 五类核心演示问题

### 6.1 产品咨询

问题：`星云企业工单平台有哪些核心功能？`

验收：命中产品介绍，答案非空，至少包含一条有效引用。

### 6.2 故障处理

问题：`客服无法接收新工单时，应该如何排查？`

验收：命中 FAQ 或故障处理文档，输出按顺序排列的排查步骤。

### 6.3 版本对比

问题：`v1.2 相比旧版本增加了哪些功能？`

验收：命中 v1.2 资料，不把假设的旧版本内容当成事实。

### 6.4 私有化部署

问题：`企业要求私有化部署，需要准备哪些资源？`

验收：命中部署文档，回答应用、数据库、向量库、对象存储、模型服务和网络要求。

### 6.5 无依据问题

问题：`这个系统能不能自动修改客户数据库中的订单金额？`

验收：返回证据不足或超出系统边界，不编造支持能力，并提示人工确认。

## 7. 建立企业场景评测集

建议先做 20 条人工标注问题：

| 类型 | 数量 | 评测目标 |
|---|---:|---|
| 产品功能 | 6 | 基础召回和引用 |
| 故障排查 | 4 | FAQ 检索和步骤完整性 |
| 版本对比 | 3 | 版本区分 |
| 部署与成本 | 3 | 方案资料召回 |
| 无依据问题 | 2 | 证据不足处理 |
| 跨文档问题 | 2 | 多文档证据覆盖 |

记录以下指标：

- Recall@5；
- MRR；
- Citation 命中率；
- 来源正确率；
- 无依据问题拒答率；
- 平均响应时间；
- 进入人工复核的问题数量。

所有指标必须标注“自建数据集、模拟企业资料、离线评测”，不能把它们写成生产效果。

## 8. 本地索引和查询

在仓库根目录执行：

```powershell
$repo = "D:\agent project\total\VideoContentKnowledgeBase-public"

$env:VAULT_DIR = "$repo\demo-data\enterprise-vault"
$env:INDEX_DIR = "$repo\demo-data\enterprise-index"

Set-Location $repo

uv run --no-sync python tools/rebuild_index.py
uv run --no-sync python tools/index_health.py
```

启动 API：

```powershell
uv run --no-sync uvicorn api:app --host 127.0.0.1 --port 8000
```

查询示例：

```powershell
$body = @{
    question = "客服无法接收新工单时，应该如何排查？"
    top_k = 5
    filters = @{ domain = "enterprise" }
    rewrite = $false
    semantic_grounding = $true
} | ConvertTo-Json -Depth 10

Invoke-RestMethod `
    http://127.0.0.1:8000/query `
    -Method Post `
    -ContentType "application/json" `
    -Body $body
```

重点检查：

- `answer` 是否非空；
- `citations` 是否存在；
- `retrieval.results` 是否命中正确文档；
- `output_gate.action` 是否符合预期；
- `answer_confidence` 是否合理；
- 引用是否来自真实企业文档，而不是本机路径或无关内容。

## 9. Docker 演示规划

现有 `tools/docker_demo.ps1` 固定了视频 ID、`wellness` 正常场景和 `medical` 阻断场景，不能直接当作企业 Demo 使用。

企业场景稳定后，新增独立脚本或给脚本增加场景参数，例如：

```text
tools/enterprise_demo.ps1
```

企业 Docker Demo 至少应验证：

1. 企业索引健康状态为 `ok`；
2. 企业问题只召回 `domain=enterprise` 文档；
3. 正常问题返回非空答案；
4. 正常问题至少包含一条 Citation；
5. 无依据问题不会生成无来源的确定性答案；
6. API 返回不暴露宿主机路径和敏感配置；
7. 运行结果保存为脱敏 JSON 证据文件。

## 10. 作品集最终产出

```text
企业知识库 Demo/
├── README.md
├── 01-业务痛点与目标用户.md
├── 02-需求调研确认单.md
├── 03-业务流程图.png
├── 04-技术架构图.png
├── 05-企业知识库资料/
├── 06-测试问题集.json
├── 07-POC评测报告.md
├── 08-Token与部署成本测算.xlsx
├── 09-Demo演示截图/
└── 10-Demo演示脚本.md
```

演示叙事顺序：

```text
客户痛点
  -> 数据来源
  -> 文档处理和索引
  -> 混合检索与重排
  -> 引用回答
  -> 无依据拒答
  -> 权限和部署边界
  -> 评测结果
  -> 交付和后续迭代
```

## 11. 简历和面试表达

完成后可以这样描述：

> 基于公开产品文档结构设计模拟企业知识库场景，整理产品手册、FAQ、权限、API 和私有化部署资料，接入现有 RAG 流程，通过混合检索、引用校验和无依据问题处理验证企业知识问答 PoC，并输出需求调研、架构、评测和部署边界材料。

不要写成：

- 已服务某真实客户；
- 已实现某企业生产知识库；
- 已节省具体金额；
- 已完成严格权限隔离；
- 已完成正式版本 metadata 过滤。

## 12. 实施顺序

1. 创建 `demo-data/enterprise-vault/`；
2. 编写 7 篇模拟企业资料；
3. 建立独立企业索引；
4. 用 5 个核心问题验证召回和引用；
5. 扩展到 20 条评测问题；
6. 记录 Recall、MRR、Citation 和拒答结果；
7. 绘制业务流程图和技术架构图；
8. 编写 POC 评测报告；
9. 制作企业 Docker 演示脚本；
10. 最后制作售前 PPT 和 2 分钟 Demo 视频。

第一阶段只需要完成前 5 步，不要一开始就做 PPT、竞品对比或 Agent 扩展。
