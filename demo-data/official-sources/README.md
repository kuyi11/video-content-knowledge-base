# 官方来源资料

本目录保存与企业知识库 Demo 相关的公开官方资料，按用途分为两层：

- `raw/`：允许按上游许可证保留的官方原文和对应许可证文本。当前包含 FastAPI Deployment/Security、OpenAPI 3.2.1、Compose Specification、Python `venv` 文档，以及 Dify 项目许可证。
- `summaries/`：基于官方页面整理的中文摘要。当前包含 FastGPT、FastAPI、OpenAPI、Compose、Python `venv`、飞书知识库权限和 Dify 知识库资料摘要。
- `sources.jsonl`：每个资料的来源 URL、上游位置、版本、许可、抓取时间和本地 SHA-256。

这些文件**不属于企业 Demo 默认索引**。默认索引仍只读取 `demo-data/enterprise-vault`，以免把官方资料与企业业务材料混在一起。完成版权、版本和内容审核后，再将选定资料复制到单独的审核目录或通过显式配置加入检索范围。

## 复核来源

```powershell
Get-FileHash .\demo-data\official-sources\raw\fastapi-security.md -Algorithm SHA256
Get-FileHash .\demo-data\official-sources\raw\fastapi-license.txt -Algorithm SHA256
Get-FileHash .\demo-data\official-sources\raw\fastapi-deployment.md -Algorithm SHA256
Get-FileHash .\demo-data\official-sources\raw\openapi-3.2.1.md -Algorithm SHA256
Get-FileHash .\demo-data\official-sources\raw\compose-spec.md -Algorithm SHA256
Get-FileHash .\demo-data\official-sources\raw\python-venv.rst -Algorithm SHA256
Get-Content .\demo-data\official-sources\sources.jsonl
```

FastAPI 原文依据 MIT 许可证保留；OpenAPI 和 Compose 原文依据 Apache 2.0 许可证保留；Python 文档依据 Python Software Foundation 许可证保留。Dify 项目使用带附加条件的修改版 Apache 2.0，许可证文本单独保存，不能简化为普通 Apache 2.0。每类原文旁边都有对应许可证文本。FastGPT、飞书和 Dify 文档页面当前只保存归纳摘要，未复制页面原文；使用时应保留来源链接和抓取日期。

## 导入建议

企业 Demo 默认索引仍只读取 `demo-data/enterprise-vault/`。如果要演示“官方资料导入”，优先选择 `summaries/` 下的中文摘要，并将选定文件复制到单独的审核目录后再设置 `VAULT_DIR`。`raw/` 下的原文适合审计、对照和许可复核；纳入检索前应确认许可证文本、来源 URL、抓取日期和本地 SHA-256 均随资料保留。

当前资料覆盖：FastAPI 部署与安全、OpenAPI API 契约、Docker Compose 多容器编排、Python 虚拟环境、飞书知识库权限、Dify 知识库分块/索引/检索测试，以及 FastGPT 知识库快速上手。它们足以支撑部署/API/权限/检索导入的演示，但不等同于完整厂商文档镜像，也不代表本项目已经实现生产级权限、版本或高可用能力。

## 刷新

使用 `tools/refresh_official_sources.ps1` 可以重新下载允许保留的 FastAPI 原文并更新对应哈希。FastGPT 摘要需要在官方页面发生变化时人工复核后更新。
