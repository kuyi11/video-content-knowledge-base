---
id: official-dify-indexing-retrieval
title: Dify 索引方式与检索设置（中文摘要）
source_type: official_summary
publisher: Dify
source: https://docs.dify.ai/en/cloud/use-dify/knowledge/create-knowledge/setting-indexing-methods
license: documentation-license-not-verified
retrieved_at: 2026-10-06
domain: retrieval
---

# Dify 索引方式与检索设置摘要

Dify 文档将知识库索引方式分为 High Quality 和 Economical。前者使用 Embedding 将 chunk 转换为向量，并支持向量、全文和混合检索；后者主要使用倒排索引和关键词，资源消耗较低但召回能力较有限。

## 主要内容

- High Quality 可以按语义相似度进行向量检索，也可以按词项进行全文检索，还可以将两者组合成 Hybrid Search。
- Hybrid Search 同时执行向量和全文检索，再按语义与关键词权重或重排模型选择结果。
- Rerank 模型可对初步召回的 chunks 重新排序；启用它需要配置对应模型提供商，并会产生额外模型消耗。
- TopK 控制召回片段数量；Score Threshold 控制最低相似度。文档示例中的默认值为 TopK 3、阈值 0.5，但实际效果应由业务评测确定。
- Economical 模式只提供倒排索引，适合资源受限或精确关键词查询场景。

## 对 Demo 的参考

本项目已有 BM25、向量和 RRF 混合检索，可借鉴 Dify 对“向量、关键词、混合、重排”的解释方式。但项目当前参数和 Dify 默认值不同，演示材料必须以本项目实际配置和离线评测结果为准。
