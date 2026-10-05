---
id: enterprise-change-request-cr2026003
source: portfolio://enterprise-kb/change-requests/CR-2026-003
profile: default
chunk_type: document
domain: enterprise
quality: human_reviewed
review_status: reviewed
source_of_truth: true
risk_level: medium
title: 模拟变更单 CR-2026-003：新增工单标签筛选
---

# 模拟变更单 CR-2026-003：新增工单标签筛选

资料性质：模拟变更单，用于展示需求、影响和验收记录。

## 变更内容

v1.2 增加按标签筛选工单的能力，筛选条件由前端传给查询 API。变更不允许绕过成员权限，也不改变知识库原文。

## 影响范围

涉及工单列表查询、API 请求参数校验和前端筛选展示；不涉及订单金额、权限角色、数据库结构迁移或模型服务替换。

## 验收条件

拥有工单访问权限的成员可以按已存在标签筛选；无效标签返回空结果并记录 request id；未授权成员不能通过标签筛选看到未授权工单。
