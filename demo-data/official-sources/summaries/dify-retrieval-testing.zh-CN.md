---
id: official-dify-retrieval-testing
title: Dify 知识库检索测试（中文摘要）
source_type: official_summary
publisher: Dify
source: https://docs.dify.ai/en/cloud/use-dify/knowledge/test-retrieval
license: documentation-license-not-verified
retrieved_at: 2026-10-06
domain: retrieval-evaluation
---

# Dify 知识库检索测试摘要

Dify 提供 Retrieval Testing 页面，用于模拟用户问题、检查知识库召回结果并试验检索设置。测试页面中临时调整的设置只作用于当前测试会话。

## 主要内容

- 可以用真实问题模拟检索，观察不同设置下返回的 chunks。
- Records 会记录知识库相关的检索事件，包括测试页面请求和关联应用在测试或生产中的检索请求。
- 测试检索与普通检索共享同一个 API endpoint，因此测试结果应结合运行环境和权限配置解释。

## 对 Demo 的参考

本项目的 5 条快速演示问题和 20 条离线标注集可以承担类似的验证职责，但需要额外记录 Recall@5、MRR、引用命中率和拒答率，不能只凭单次人工体验判断效果。
