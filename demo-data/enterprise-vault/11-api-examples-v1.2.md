---
id: enterprise-api-examples-v12
source: portfolio://enterprise-kb/api/examples/v1.2
profile: default
chunk_type: document
domain: enterprise
quality: human_reviewed
review_status: reviewed
source_of_truth: true
risk_level: medium
title: 星云企业工单平台 API 请求响应示例 v1.2
---

# 星云企业工单平台 API 请求响应示例 v1.2

资料性质：模拟企业资料。示例中的 token、工单编号和 URL 均为虚构值。

## 查询知识库

```http
POST /query HTTP/1.1
Authorization: Bearer demo-token
Content-Type: application/json

{"question":"如何排查新工单接收失败？","top_k":5,"filters":{"domain":"enterprise"},"semantic_grounding":true}
```

成功响应至少包含 `answer`、`retrieval.results`、`citations`、`output_gate` 和 `answer_confidence`。引用应能对应返回结果中的 `document_id`、`section` 和 `chunk_id`。

## 常见错误

- `401`：缺少或无效的 Bearer token；客户端应重新鉴权，不要重试敏感写操作。
- `422`：请求字段缺失或类型错误；客户端应根据 OpenAPI schema 修正请求。
- `503`：模型或索引服务不可用；记录 request id 后转交管理员检查服务健康状态。

## 写操作边界

当前 Demo 只提供查询接口，不提供修改客户订单、修改成员权限或执行生产数据库写入的 API。
