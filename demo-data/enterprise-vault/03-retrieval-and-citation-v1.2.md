---
id: enterprise-retrieval-and-citation-v12
source: portfolio://enterprise-kb/retrieval-and-citation/v1.2
profile: default
chunk_type: document
domain: enterprise
quality: human_reviewed
review_status: reviewed
source_of_truth: true
risk_level: low
title: 星云检索与引用规范 v1.2
---

# 星云检索与引用规范 v1.2

资料性质：模拟企业资料，基于公开产品文档结构整理。

## 混合检索流程

系统先使用 Embedding 进行语义召回，再使用 Whoosh 和 jieba 执行关键词召回，最后通过 Reciprocal Rank Fusion 合并结果。候选结果按 chunk 去重，必要时可以使用本地 Cross-Encoder 重排。语义检索适合表达改写，关键词检索适合产品名、错误码和配置项。

## 引用规则

回答中的每个独立事实都应引用实际检索到的 chunk。企业资料没有视频时间戳时，引用使用文档身份和章节；不能凭空构造 Sxxxx 时间来源。引用只表示答案使用了该资料，不代表资料已经适用于所有企业环境。

## 证据不足处理

如果没有命中资料，或者命中的内容不能直接支持问题，系统应返回证据不足提示。派生摘要不能单独成为高风险答案证据；回答生成后还会检查引用是否存在、来源是否属于对应 chunk，并删除未通过校验的结论。

## 版本边界

v1.2 只是资料标题和来源中的版本标记。当前索引不会将 v1.2 作为独立 metadata 字段过滤，因此版本对比问题必须以资料中明确写出的变更说明为依据，不能把推测当成旧版本事实。
