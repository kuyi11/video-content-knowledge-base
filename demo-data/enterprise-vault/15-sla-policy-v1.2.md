---
id: enterprise-sla-policy-v12
source: portfolio://enterprise-kb/policies/sla/v1.2
profile: default
chunk_type: document
domain: enterprise
quality: human_reviewed
review_status: reviewed
source_of_truth: true
risk_level: low
title: 星云企业工单平台 SLA 处理规则 v1.2
---

# 星云企业工单平台 SLA 处理规则 v1.2

资料性质：模拟 SLA 规则，用于问答和流程演示，不代表真实客户合同。

## 规则

- P1 紧急工单：工作时间内 30 分钟内响应，2 小时内给出处理计划。
- P2 高优先级工单：4 小时内响应，1 个工作日内给出处理计划。
- P3 普通工单：1 个工作日内响应，3 个工作日内给出处理计划。

计时从工单进入有效队列开始；缺少必填信息或等待客户补充时应记录暂停原因。SLA 提醒只负责提示和记录，不自动修改工单优先级或代表人工确认。
