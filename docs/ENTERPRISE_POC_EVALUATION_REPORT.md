# 企业知识库 RAG POC 评测报告

评测日期：2026-10-08
资料性质：模拟企业资料，离线评测
模型：BGE-M3 + Ollama `qwen2.5:7b`
索引：16 个文档、45 个 chunk
评测入口：`eval/run_eval.py`

## 1. 评测目标

验证企业知识库场景中的四件事：

1. 能否召回正确资料；
2. 能否生成带 Citation 的答案；
3. 能否对无依据问题进行边界处理；
4. 能否通过 Claim/Evidence 和 Output Gate 约束回答。

## 2. 两层证据

### 核心 Demo 集

核心 Demo 是 `docs/demo-evidence/enterprise-demo-latest.json` 中的 5 个 Docker 场景，全部通过：

| 项目 | 结果 |
|---|---:|
| 场景数量 | 5 |
| 通过数量 | 5 |
| 索引健康状态 | `ok` |
| 文档数 | 16 |
| Chunk 数 | 45 |
| 答案置信度要求 | `high` |
| Citation | 每个场景大于 0 |
| Output Gate | `allow` |

这组证据适合用于视频演示和售前主线。它证明固定的产品咨询、故障排查、版本说明、私有化部署和产品边界问题可以完成端到端演示。

### 完整评测集

完整评测集包含 22 条问题：18 条可回答问题和 4 条无依据/越权问题。最新原始报告位于 `demo-data/enterprise-reports/rag-eval-20261008-052956.json`，该目录属于本地运行产物。

| 指标 | 结果 | 解释 |
|---|---:|---|
| Recall@5 | 1.0000 | 18 条可回答问题都召回了至少一个目标资料 |
| MRR | 0.7935 | 目标资料排序仍有优化空间 |
| Source coverage | 0.8519 | 多来源问题没有全部覆盖所有目标资料 |
| Claim citation grounding | 0.8947 | 部分 Claim 的引用完整性需要复核 |
| Claim semantic grounding | 0.7895 | 语义证据判断存在待复核样本 |
| Abstention accuracy | 1.0000 | 本次 4 条无依据问题均触发了边界表达 |
| Answer point coverage | 0.4843 | 生成答案没有覆盖所有人工标注要点 |
| Claim review count | 8 | 进入语义或证据人工复核队列 |

核心 Demo 对应的 5 条完整评测子集指标如下：

| 指标 | `core_demo` |
|---|---:|
| Case count | 5 |
| Recall@5 | 1.0000 |
| MRR | 0.8750 |
| Source coverage | 0.8750 |
| Answer point coverage | 0.6458 |
| Abstention accuracy | 1.0000 |
| Claim citation grounding | 0.6667 |
| Claim semantic grounding | 0.6667 |
| Claim review count | 4 |

核心子集和 Docker 五场景不是完全相同的请求文本，因此不把两组指标混写。Docker 证据用于展示固定演示流程，`core_demo` 用于观察完整评测集中的代表性质量。

## 3. 结果判断

当前最强的部分是检索命中和拒答边界：Recall@5 为 1，且本次无依据问题的拒答准确率为 1。Citation 机制也能把答案与实际 chunk 关联起来。

当前主要短板是答案覆盖和证据裁判稳定性。`Answer point coverage` 使用人工标注答案点的归一化文本匹配，属于诊断指标，不是语义正确率；它暴露出回答可能遗漏关键步骤、字段或部署组件。8 条 Claim 进入复核，其中故障排查、SLA 和跨文档问题较集中。

因此本项目适合定位为：

> 基于模拟企业资料的企业知识库 RAG POC、可复现 Demo 和售前参考实现。

不应定位为生产级企业 RAG、真实客户交付结果或已经完成真实 ACL 隔离的平台。

## 4. 优化路线

### P0：提升回答覆盖

- 对故障排查、API 字段、权限和部署问题使用结构化回答模板；
- 在生成提示中要求逐项覆盖问题中的对象、步骤和限制；
- 将 `answer_points` 调整为可验证的短事实，不用过长自然语言；
- 增加“缺少某个答案点时返回待确认”的输出策略。

### P1：稳定证据判断

- 对 8 条 Claim review queue 逐条人工复核；
- 区分“引用缺失”“语义不足”“资料冲突”和“评测标注过严”；
- 对跨文档问题要求答案逐条绑定来源，避免一个引用覆盖多个未经证明的步骤；
- 保留复核记录，不直接删除失败样本。

### P2：补齐企业交付边界

- 增加正式 `version`、`document_type` 和 `effective_status` metadata；
- 设计用户身份、角色和 ACL 过滤接口；
- 增加增量索引、回滚、监控、告警和审计方案；
- 使用成本模型验证 API、云部署和私有化选型。

## 5. 复现命令

```powershell
$env:VAULT_DIR = "$PWD\demo-data\enterprise-vault"
$env:INDEX_DIR = "$PWD\demo-data\enterprise-index"
$env:TEMP_DIR = "$PWD\demo-data\enterprise-temp"
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

本报告中的指标来自本地模型和模拟资料，重新运行可能因模型输出和运行环境产生小幅变化。
