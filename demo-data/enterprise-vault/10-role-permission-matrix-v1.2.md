---
id: enterprise-role-permission-matrix-v12
source: portfolio://enterprise-kb/access-control/role-permission-matrix/v1.2
profile: default
chunk_type: document
domain: enterprise
quality: human_reviewed
review_status: reviewed
source_of_truth: true
risk_level: medium
title: 星云企业工单平台角色权限矩阵 v1.2
---

# 星云企业工单平台角色权限矩阵 v1.2

资料性质：模拟企业资料，是权限设计样例，不代表当前 Demo 已实现 ACL。

| 操作 | 管理员 | 编辑成员 | 阅读成员 |
|---|---:|---:|---:|
| 查看已授权知识库 | 是 | 是 | 是 |
| 新增和更新知识文档 | 是 | 是 | 否 |
| 删除知识文档 | 是 | 否 | 否 |
| 修改成员和角色 | 是 | 否 | 否 |
| 修改索引和模型配置 | 是 | 否 | 否 |
| 导出权限资料 | 按企业策略 | 否 | 否 |

## 权限判定

检索前应先验证用户身份、角色、知识库和文档授权范围。未授权文档不能进入候选结果、模型上下文或引用列表。权限不足时返回“当前角色无权访问该资料”，不能用其他文档猜测答案。

## 当前 Demo 边界

当前 Demo 只通过独立 vault 和 `domain=enterprise` 过滤展示资料隔离，API 尚未实现用户级 ACL。任何权限问题都应作为设计说明或拒答测试处理。
