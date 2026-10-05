---
id: official-fastapi-security
title: FastAPI Security 教程（中文摘要）
source_type: official_summary
publisher: FastAPI
source: https://fastapi.tiangolo.com/tutorial/security/
raw_source: raw/fastapi-security.md
license: MIT
retrieved_at: 2026-10-05
domain: api-security
---

# FastAPI Security 摘要

FastAPI 官方 Security 教程区分认证（确认调用者身份）和授权（确认调用者能做什么），并说明如何使用符合 OpenAPI 的安全方案。

## 主要内容

- `fastapi.security` 提供可复用的安全依赖。
- HTTP Basic、Bearer Token、API Key 等方案可以声明为接口依赖，并反映到 OpenAPI 文档。
- OAuth2 可用于密码流、Bearer Token 和权限范围（scope）；OpenID Connect 建立在 OAuth2 之上。
- 安全依赖可以与路径操作和依赖注入组合，统一处理凭证读取、校验和错误响应。

## 对本项目的参考

当前 Demo 的 `API_TOKEN` 主要用于保护服务入口，并不等同于完整的用户身份、知识库权限或 OAuth2 scope 模型。若将 Demo 扩展为多租户企业服务，应进一步设计用户、角色、知识库和文档级授权，并把授权结果传递到检索过滤条件。

本摘要对应的 FastAPI 官方 Markdown 原文保存在 `../raw/fastapi-security.md`，许可证文本保存在 `../raw/fastapi-license.txt`。
