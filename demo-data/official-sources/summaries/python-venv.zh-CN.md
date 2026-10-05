---
id: official-python-venv
title: Python venv 模块（中文摘要）
source_type: official_summary
publisher: Python Software Foundation
source: https://docs.python.org/3/library/venv.html
raw_source: raw/python-venv.rst
license: Python-2.0
retrieved_at: 2026-10-06
domain: python-environment
---

# Python venv 摘要

Python `venv` 模块用于创建轻量级虚拟环境。虚拟环境建立在一个已有的基础 Python 之上，默认与基础环境中的第三方包隔离，只使用环境内明确安装的依赖。

## 主要内容

- 使用 `python -m venv <目录>` 创建环境；Windows PowerShell 通常使用 `<目录>\Scripts\Activate.ps1` 激活。
- 激活只是修改当前 shell 的 PATH，并不是运行 Python 所必需的；也可以直接调用虚拟环境中的解释器。
- 虚拟环境通常应当可删除、可重建，不应当假设它可以安全地移动或复制；项目应保存依赖声明而不是提交整个环境目录。
- `--system-site-packages` 等选项会改变隔离边界，需要在构建和运行文档中明确记录。

## 对 Demo 的参考

本项目使用 `uv run --no-sync` 复用既有依赖环境；若需要用标准库方式建立项目环境，可按 Python 官方说明创建 `.venv`，再安装锁定依赖。环境路径、模型路径和索引路径应通过配置传入，不应写死在 API 响应中。

本摘要对应的 Python 官方 venv 文档原文保存在 `../raw/python-venv.rst`，许可证文本保存在 `../raw/python-license.txt`。
