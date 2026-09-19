# Claim 标签人工复核清单

`eval/claim_labels.jsonl` 当前 30 条的证据关系均已由人工复核并标记为
`reviewed`。标签分布为：`supported` 11 条、`contradicted` 11 条、
`insufficient_evidence` 8 条。这里的 `reviewed` 只表示 claim 与给定 evidence 的
文本关系已复核，不表示原始字幕真实性或医学事实已经由专家认证。

人工复核时逐条确认：claim 是否只表达一个命题、evidence 是否完整、标签是否符合下列定义：

- `supported`：证据直接支持命题的全部关键内容。
- `contradicted`：证据明确否定命题，或给出足以否定全称命题的反例。
- `insufficient_evidence`：证据既不能完整支持，也没有明确反驳。

## 后续核查

- 已运行来源溯源审查：

  ```powershell
  uv run --no-sync python tools/audit_claim_sources.py `
    --output eval/reports/claim-source-audit-YYYYMMDD.json `
    --baseline-output eval/baselines/claim-source-audit-YYYYMMDD.json
  ```

  2026-09-18 的结果是 30/30 条均已定位到 BVID 和原始字幕时间窗口，
  0 条可以仅靠字符串匹配自动判定为逐字一致，0 条无法定位。这个结果应解读为
  “来源可供人工比对”，而不是“30 条来源已经验证”。原始 chunk（如
  `BV...__raw_2757`）使用 Obsidian 文档的时间标记；带 `Sxxxx` 的结构化证据
  使用结构化摘要中的时间范围，审查器不会混用两套时间轴。

- [ ] 调取原始字幕，核对所有带 BVID 或 `S` 编号的 evidence 是否逐字对应原文。
- [ ] `thyroid-004`：确认甲状腺功能检测频率的个体化原则。
- [ ] `diet-002`：确认体重延时反映的例外情况。
- [ ] `cardio-001`：确认持续胸痛的定义与语境。
- [ ] `cardio-004`：确认高血压管理中“结合”的个体化含义。
- [ ] `winter-006`：区分身体乳“缓解干燥”与“治愈皮炎”。
- [ ] `fitness-008`：确认 80% 糖原供能比例的适用条件。
- [ ] `fitness-010`：确认耐力运动补糖剂量的个体化建议。

2026-09-19 已完成 GLM 复核队列的 9 条原始字幕核对，标签变更为 0 条。复核发现
`winter-006` 的 `source_refs` 原为 `S0005`，但身体乳原文位于 `S0003`；已修正
`eval/claim_labels.jsonl`，并记录于 `eval/baselines/claim-grounding-glm4-9b-human-review-20260919.json`。

如果后续发现 claim、evidence 或 label 需要修改，应同步修改 `notes`，并保留修改依据。
完成后运行：

```powershell
uv run --no-sync python eval/claim_grounding_eval.py --reviewed-only
```

不要因为裁判模型的输出与标签一致就自动批准；本轮的 `reviewed` 状态来自人工阅读
claim 与 evidence，而不是模型自评。

来源审查基线位于 `eval/baselines/claim-source-audit-20260918.json`，完整窗口和
相似度明细位于被 `.gitignore` 忽略的 `eval/reports/claim-source-audit-20260918.json`。
审查时优先确认三个字段：`source_path` 是否为原始字幕、`segments` 是否包含完整
上下文、`text_similarity` 是否只是定位辅助而非医学正确性分数。发现时间窗口不吻合时，
应先修正 `source_refs` 或 chunk 时间，再重新判断 claim 标签。

剩余 21 条来源真实性核对队列：
`eval/reports/claim-source-audit-remaining-21-20260919.json`。该文件中的
`source_path`、`source_refs`、`segments` 和 `best_source_text` 用于逐条回到原始字幕比对；
当前 21 条均已定位，但尚未完成来源真实性确认。

2026-09-19 已完成剩余 21 条来源真实性核对，标签变更为 0 条；其中 supported 11 条、
contradicted 10 条。已修正 4 条来源窗口：`cardio-001 S0044 -> S0047`、
`winter-004 S0006 -> S0005`、`fitness-009 S0013 -> S0011`、
`fitness-011 S0005 -> S0004`。详细人工记录位于
`eval/reports/claim-source-audit-remaining-21-human-review-20260919.json`，提交基线位于
`eval/baselines/claim-source-audit-remaining-21-human-review-20260919.json`。
