---
id: enterprise-permission-and-security-v12
source: portfolio://enterprise-kb/permission-and-security/v1.2
profile: default
chunk_type: document
domain: enterprise
quality: human_reviewed
review_status: reviewed
source_of_truth: true
risk_level: low
title: 星云权限与安全边界 v1.2
---

# 星云权限与安全边界 v1.2

资料性质：模拟企业资料，基于公开产品文档结构整理。

## 角色职责

管理员负责成员、角色、知识库配置、备份和审计设置；编辑成员负责提交和更新资料；阅读成员只能访问已授权的知识内容。角色设计是业务规则说明，当前 Demo API 没有实现按用户身份执行 ACL。

## 内容访问

正式系统应在检索前根据用户身份和知识库权限过滤候选资料，未授权内容不能进入模型上下文。页面分享、导出和外部链接也应受同一权限策略约束。当前公开 Demo 通过独立 vault 和 domain filter 展示数据隔离，不应声称完成生产级权限隔离。

## 审计与日志

审计记录可以包含问题、答案、引用和索引 generation。查询日志和 Claim/Evidence 审计默认关闭；开启前应确认本地目录权限、保留期限和敏感信息管理。对外 API 应隐藏主机路径、Cookie、令牌和私有文件位置。

## 安全边界

知识库只能提供检索和说明，不代替管理员审批，不直接执行生产数据库修改，不自动提升成员权限。任何涉及生产数据、权限变更或外部系统写操作的请求都必须转人工并由授权系统执行。
