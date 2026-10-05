---
id: official-dify-chunking
title: Dify 文档分块与清洗（中文摘要）
source_type: official_summary
publisher: Dify
source: https://docs.dify.ai/en/cloud/use-dify/knowledge/create-knowledge/chunking-and-cleaning-text
license: documentation-license-not-verified
retrieved_at: 2026-10-06
domain: document-processing
---

# Dify 文档分块与清洗摘要

Dify 将导入知识库的文档切分为 chunks，以便检索时快速定位相关片段。分块策略会直接影响召回精度、上下文完整性和后续回答质量。

## 主要内容

- 分隔符决定文本在哪里切分，最大 chunk 长度限制单个片段大小；分隔符会被移除，因此应选择不会损失正文含义的字符。
- General 模式使用单层 chunk，可配置分隔符、最大长度和相邻 chunk 重叠。
- Parent-child 模式使用小的 child chunk 做匹配，再返回较大的 parent chunk，以平衡精确召回和上下文完整性。
- Parent chunk 可以按段落或整篇文档组织；整篇模式适合短小、强关联文档，但官方说明超过 10,000 tokens 的内容会被截断。
- 预处理可以压缩连续空格、换行和制表符，也可以移除 URL 和邮箱；清洗规则应结合引用需求谨慎启用。
- 预览分块结果是导入前的重要检查步骤，多个文档可以逐个查看分块效果。

## 对 Demo 的参考

企业资料导入时应先保留标题、版本、来源和权限字段，再选择分隔符与最大长度。当前项目的索引器有自己的切分规则，不能直接声称与 Dify 的 General 或 Parent-child 模式完全一致。
