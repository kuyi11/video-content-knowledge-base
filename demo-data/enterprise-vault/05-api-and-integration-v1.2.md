---
id: enterprise-api-and-integration-v12
source: portfolio://enterprise-kb/api-and-integration/v1.2
profile: default
chunk_type: document
domain: enterprise
quality: human_reviewed
review_status: reviewed
source_of_truth: true
risk_level: low
title: 星云 API 与集成说明 v1.2
---

# 星云 API 与集成说明 v1.2

资料性质：模拟企业资料，基于公开产品文档结构整理。

## API 鉴权

调用方应通过 HTTPS 请求发送短期或可轮换的 API token。token 不应写入前端代码、日志或知识库正文。API 服务端应校验令牌、限制请求频率，并在错误响应中避免返回本机路径和内部配置。

## 查询调用

调用方提交问题、top_k 和可选 metadata filter，服务返回答案、引用、检索结果摘要、证据覆盖状态和回答置信度。企业场景应固定使用独立企业索引，并在请求中限制 domain=enterprise。

## 资料更新

资料更新接口的正式实现需要校验调用者权限、写入待审核队列并触发索引重建。当前 Demo 只支持通过本地文件和维护命令重建索引，没有开放任意路径写入或在线资料管理 API。

## 错误处理

鉴权失败返回 401；请求频率超过限制返回 429；查询容量耗尽返回 503；过滤字段不支持返回 400。模型或索引不可用时，服务应返回受控错误，不应生成无来源答案。
