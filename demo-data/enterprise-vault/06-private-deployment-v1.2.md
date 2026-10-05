---
id: enterprise-private-deployment-v12
source: portfolio://enterprise-kb/private-deployment/v1.2
profile: default
chunk_type: document
domain: enterprise
quality: human_reviewed
review_status: reviewed
source_of_truth: true
risk_level: low
title: 星云私有化部署资源说明 v1.2
---

# 星云私有化部署资源说明 v1.2

资料性质：模拟企业资料，基于公开产品文档结构整理，不构成实际容量承诺或报价。

## 应用服务

部署至少需要 API 服务、索引存储和任务运行环境。API 服务负责鉴权、检索和回答编排；索引目录保存 FAISS、Whoosh、chunk metadata 和健康报告。生产环境还应配置进程管理、健康检查和日志采集。

## 数据组件

业务数据库用于保存工单、成员和配置；向量索引用于语义召回；对象存储用于原始文件和备份。组件可以按企业规模部署在同一私有网络，也可以拆分到不同节点，但必须明确访问控制和备份边界。

## 模型与网络

需要本地 Embedding 模型和可访问的 LLM 服务或 AI Proxy。若企业要求数据不出网，应使用本地模型或企业批准的内网代理。网络策略应允许 API 到模型服务和索引存储的必要通信，同时禁止未经批准的远程 LLM 端点。

## 运维资源

上线前应准备磁盘容量、备份策略、索引重建窗口、监控、密钥轮换和故障转人工流程。具体 CPU、内存、GPU 和并发容量取决于文档规模、Embedding 模型、LLM 模型和响应时延目标，本文不提供未经测试的固定规格。

## API 与私有化选择

API 方式适合快速接入和由供应方维护基础设施；私有化部署适合对数据位置、网络和内部审计有明确要求的企业。选择前应确认合规、运维能力、模型许可、备份恢复和升级责任。
