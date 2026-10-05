---
id: enterprise-incident-postmortem-20261002
source: portfolio://enterprise-kb/incidents/2026-10-02/INC-17
profile: default
chunk_type: document
domain: enterprise
quality: human_reviewed
review_status: reviewed
source_of_truth: true
risk_level: medium
title: 模拟故障复盘 INC-17：索引服务不可用
---

# 模拟故障复盘 INC-17：索引服务不可用

资料性质：模拟故障复盘，不代表真实生产事故。

## 影响

2026-10-02 14:10 至 14:28，知识库查询返回 `503`，已建立的工单数据未丢失；客服仍可通过既有工单页面查看历史记录。

## 原因与恢复

演示环境的向量索引卷未正确挂载，API 健康检查发现索引目录为空。重新挂载持久化卷并执行索引健康检查后，查询恢复。恢复步骤是确认挂载路径、检查索引 generation、重建索引、执行带引用问答和无依据测试。

## 改进项

部署前增加卷挂载检查和索引 generation 检查；监控 `503` 比例；保留可回滚索引；故障期间向用户返回服务不可用提示，不把空检索结果编造成确定答案。
