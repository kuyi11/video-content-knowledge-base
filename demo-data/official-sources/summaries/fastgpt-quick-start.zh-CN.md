---
id: official-fastgpt-quick-start
title: FastGPT 快速上手（中文摘要）
source_type: official_summary
publisher: FastGPT
source: https://doc.fastgpt.cn/zh-CN/guide/getting-started/quick-start
license: not_verified
retrieved_at: 2026-10-05
domain: enterprise-knowledge-base
---

# FastGPT 快速上手摘要

## 页面范围

官方快速上手页面以四类示例介绍 FastGPT：对话 Agent、知识库结合对话 Agent、工作流，以及 Agent V2。企业知识库 Demo 主要对应第二类场景。

## 知识库导入流程

1. 创建知识库和文本数据集。
2. 上传本地文件，并选择解析参数。
3. 等待文件解析、分块和索引完成。
4. 预览分块结果，确认标题、正文和元数据没有被错误切分。
5. 将知识库关联到 Agent，再用实际问题验证召回内容和引用。

官方示例涉及 Markdown、TXT、PDF、Word 等常见文档类型。落地企业资料时，应先确定哪些字段是正文、标题、来源和权限信息，再决定是否导入。

## 对 Demo 的工程含义

- 分块大小会影响召回质量：过大容易带入无关内容，过小则可能丢失上下文。
- 解析完成与可检索不是同一个状态，导入流程需要等待索引就绪并做一次查询验证。
- 回答应保留原文引用或来源信息，便于用户核查。
- 官方页面是产品操作说明，不等同于本项目的实现承诺；本地 Demo 仍需单独验证解析器、索引器和权限边界。

本文件是归纳摘要，不是官方页面的逐字复制。使用时请以来源链接中的最新版本为准。
