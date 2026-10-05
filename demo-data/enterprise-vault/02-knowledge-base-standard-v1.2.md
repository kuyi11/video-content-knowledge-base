---
id: enterprise-knowledge-base-standard-v12
source: portfolio://enterprise-kb/knowledge-base-standard/v1.2
profile: default
chunk_type: document
domain: enterprise
quality: human_reviewed
review_status: reviewed
source_of_truth: true
risk_level: low
title: 星云知识库资料标准 v1.2
---

# 星云知识库资料标准 v1.2

资料性质：模拟企业资料，基于公开产品文档结构整理。

## 支持的资料格式

知识库接入流程面向 Markdown、纯文本、PDF 转换文本和结构化问答表。导入前应确认编码、标题层级和表格内容可读。扫描图片和无法复制文字的文件需要先经过 OCR 或人工整理，本 Demo 不包含 OCR 服务。

## 清洗与切分

导入程序保留标题、正文和 frontmatter metadata。正文按二级和三级标题拆分，过短章节会与相邻章节合并，过长章节按句子边界切成不超过约 600 字的 chunk。每个 chunk 继承文档的 domain、审核状态、来源性质和风险等级。

## Metadata 规范

每篇资料应写明稳定的 id、source、profile、domain、quality、review_status、source_of_truth 和 risk_level。v1.2 版本写在标题、正文和 portfolio source 中；当前 Demo 没有正式 version 字段，也不提供严格版本过滤。

## 更新与重新索引

资料修改后应重新构建企业索引。索引会比较文档身份、内容哈希、chunking version、Embedding 模型和 generation id。健康检查发现缺失、额外或变更文档时，应用应执行全量重建，而不是继续使用过期索引。
