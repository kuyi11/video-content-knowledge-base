# AI 售前 PPT 修改反馈报告

日期：2026-10-08
依据：2026-10-08 企业 RAG POC 评测报告、用户提供的修改反馈、Git 基线核验结果

## 1. 修改结论

本次反馈整体采纳，但修正了评测指标混用、Docker 演示与评测集边界、权限能力状态和 Git 基线判断。

## 2. 已采纳的修改

### 第 8 页：评测口径

- 增加评测前提：模拟企业资料、离线评测、评测日期 `2026-10-08`、代码基线 `22dc574`。
- 列出 22 条完整问题集的全部指标：
  - Recall@5 `1.0000`
  - MRR `0.7935`
  - Source coverage `0.8519`
  - Claim citation grounding `0.8947`
  - Claim semantic grounding `0.7895`
  - Abstention accuracy `1.0000`
  - Answer point coverage `0.4843`
  - Claim review `8` 条
- 增加 5 条 `core_demo` 子集对照值：Recall@5 `1.0000`、MRR `0.8750`、Source coverage `0.8750`、Claim citation grounding `0.6667`、Claim semantic grounding `0.6667`、Abstention accuracy `1.0000`、Answer point coverage `0.6458`、Claim review `4` 条。
- 单独注明 Docker 固定演示场景 `5/5` 通过，且不与 `core_demo` 或 22 条全集混算。
- 保留 `Answer point coverage` 是文本匹配诊断指标的说明，不把它写成语义正确率。

### 第 7 页：安全与权限状态

改为三类状态：

1. **本地 POC 已验证**：API Token 鉴权与限流、`domain=enterprise` 过滤、Citation/Claim/Output Gate、索引健康检查和 Docker 演示。
2. **客户环境待验证**：客户网关接入、业务域映射、Compose 与模型资源部署、客户网络/并发/响应表现。
3. **方案设计，尚未实现**：真实 SSO、组织同步、ACL/行级权限、越权审计、正式版本过滤、增量索引、回滚、监控和告警。

页脚明确：`domain=enterprise` 属于业务域过滤，不等于用户 ACL 隔离；当前未接真实 SSO 或企业 ACL。

### 封面

将“候选片段 1/2/3/4”等模板残留替换为“产品手册、版本说明、故障记录、来源可追溯、相关性排序、证据可核验、不足时提示人工确认”。

### 成本页和边界页

- 成本页保留“假设参数与公式，不是报价”的说明。
- 交付边界页保留“模拟资料、无真实客户收益数据、未接入真实 SSO/ACL、离线结果不构成生产准确率或 SLA”。

## 3. 未采纳或不重复执行的建议

- 不使用 `Answer point coverage = 0.4704`，当前完整报告值为 `0.4843`。
- 不使用 `Abstention accuracy = 0.75`，当前完整报告值为 `1.0000`。
- 不把完整集 Claim review 写成 4 条；完整集为 8 条，4 条仅属于 `core_demo` 子集。
- 不把 Claim citation grounding 写成 `1.0`，完整集为 `0.8947`。
- 不把 Claim semantic grounding 写成 `0.8947`，完整集为 `0.7895`。
- 不重复提交评测代码，不移动或重打已有 Tag。

## 4. Git 基线核验

- `v0.1-enterprise-rag-poc` 指向 commit `22dc574`。
- Tag 统计已包含 `eval/run_eval.py`、`tests/test_rag_eval.py`、评测报告、架构说明、成本模型、运行手册和面试问答。
- `git diff v0.1-enterprise-rag-poc -- eval/run_eval.py tests/test_rag_eval.py` 无输出，说明评测实现与测试没有脱离该 Tag。
- 当前工作区已有售前工作日志修改和 PPT 产物；本次不修改评测代码、不移动 Tag。

## 5. 验证结果

- PPT 总页数：10。
- 逐页 PNG 视觉检查：通过，重点检查封面残留、评测表、权限三类状态、成本说明、文字溢出和重叠。
- PPTX 包完整性：通过。
- 布局几何检查：finding `0`，warning `0`。
- 字体检查：`Microsoft YaHei` 策略通过。
- 最终 PPTX 已用 Artifact Tool 独立导入确认共 10 页。
- 最终修订版：`enterprise-rag-presales-v5.pptx`；SHA-256 `b67ef848bd00a44449459fc76fa4e5c3b0f5e7c4d5ea7d4c5a183e356cd39880`。
- PDF 已重新导出，共 10 页；逐页渲染检查通过，中文显示正常、图片清晰。
- 第 8 页边界表达命中率脚注与第 7 页 ACL 未实现说明均存在；PPT 全文未发现“转人工”“权限过滤”残留。
- 项目完整测试：`213 passed, 1 warning`。

## 6. 下一步

1. 依据客户问题量、Token 分布、文档规模、峰值并发、数据敏感性和运维责任完善成本表。
2. 制作 2 分钟 Demo 视频，继续区分 Docker 固定场景与完整离线评测。

## 7. 2026-10-08 措辞修正

- Abstention accuracy 改为边界表达命中率 4/4（短语规则），并注明非通用拒答能力验证。
- “转人工”统一改为“提示人工确认”；第 4 页“权限过滤”改为“业务域过滤（domain=enterprise）”。
- 公开脱敏证据文件已更新至 2026-10-08 评测基线，包含 22 条完整评测、5 条 `core_demo` 子集和 Docker 5/5 场景结果。
- 重新生成 PPTX，SHA-256：`b67ef848bd00a44449459fc76fa4e5c3b0f5e7c4d5ea7d4c5a183e356cd39880`。
- 重新导出 PDF，共 10 页；逐页渲染检查通过，中文显示正常、图片清晰；第 8 页脚注和第 7 页 ACL 未实现说明均存在。
