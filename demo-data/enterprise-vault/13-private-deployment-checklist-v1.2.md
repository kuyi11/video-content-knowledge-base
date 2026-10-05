---
id: enterprise-private-deployment-checklist-v12
source: portfolio://enterprise-kb/deployment/checklist/v1.2
profile: default
chunk_type: document
domain: enterprise
quality: human_reviewed
review_status: reviewed
source_of_truth: true
risk_level: medium
title: 星云企业工单平台私有化部署检查表 v1.2
---

# 星云企业工单平台私有化部署检查表 v1.2

资料性质：模拟部署检查表，用于售前和演示，不是生产容量承诺。

## 资源检查

- [ ] API 应用服务已准备，能够访问内部网络和健康检查地址。
- [ ] 业务数据库已准备账号、备份策略和最小权限网络规则。
- [ ] 向量索引或向量数据库已准备持久化卷。
- [ ] 对象存储已准备桶、读写权限和生命周期策略。
- [ ] Embedding/模型服务或 AI Proxy 已确认地址、鉴权和容量。
- [ ] DNS、TLS、反向代理、防火墙和出站网络策略已确认。
- [ ] 日志、监控、告警、备份恢复和回滚联系人已登记。

## 验收顺序

先做配置校验，再重建一份小规模索引；然后执行健康检查、鉴权测试、企业域过滤、带引用问答和无依据问题测试。没有完成备份恢复和权限评审时，不应宣称完成生产部署。
