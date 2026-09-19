# Codex 日常开发工作流

本仓库是 GitHub 公开开发版本。使用 Codex 开发时，应将当前目录作为唯一的日常开发目录：

`D:\agent project\total\VideoContentKnowledgeBase-public`

## 两个本地目录的职责

`VideoContentKnowledgeBase-public`（`public-main`）用于公开代码、测试、文档和 GitHub 推送。

`VideoContentKnowledgeBase`（`main`）用于完整历史、真实 vault、原始转录、本地索引和私有实验。原始目录不是废弃项目，也不应删除；但不要在其中进行公开版本开发或向 GitHub 推送。

## 开始工作

在 Codex 中打开 `VideoContentKnowledgeBase-public`，执行：

```powershell
cd "D:\agent project\total\VideoContentKnowledgeBase-public"
git status --short --branch
git pull --ff-only origin main
```

本地分支名是 `public-main`，远程公开分支名是 `main`。

## 本地真实数据

公开工作树不包含真实视频转录、个人 Obsidian 内容、浏览器 Cookie、本地索引或私有评估基线。真实数据通过环境变量使用：

```powershell
$env:VAULT_DIR = "D:\agent project\total\VideoContentKnowledgeBase\vault\videos"
$env:INDEX_DIR = "D:\agent project\total\VideoContentKnowledgeBase\index"
$env:OBSIDIAN_EXTERNAL_VAULTS = "F:\obsidian"
```

不要把真实数据复制到公开工作树。`examples/sample_note.md` 是可提交的脱敏示例。

## 开发、测试与提交

```powershell
& "D:\agent project\total\VideoContentKnowledgeBase\.venv\Scripts\python.exe" -m pytest -q
git status --short
git diff --check
git ls-files vault tools/cookies eval/baselines
```

最后一条命令应无输出。提交前还应检查本机路径、Cookie 和 Token：

```powershell
git grep -n -I -i -E "F:\\obsidian|D:\\agent project|vd_source=|douyin_cookies|Network\.getAllCookies|encrypted_value|sk-[A-Za-z0-9]{10,}" -- ':!uv.lock'
```

确认测试通过且没有敏感内容后：

```powershell
git add .
git commit -m "feat: describe the change"
git push origin public-main:main
```

## 注意事项

- 不要在 `VideoContentKnowledgeBase` 原始目录直接开发公开功能。
- 不要从原始目录向 `origin/main` 推送。
- 不要提交真实字幕、医疗笔记、个人知识库、索引、日志、Cookie 或模型文件。
- 需要真实数据测试时，使用环境变量指向本地数据，不要复制数据文件。
- API 默认只绑定本机；启用 `API_TOKEN` 后再允许客户端调用。
- 不要把 `OLLAMA_HOST` 指向不受信任的远程服务，问题和检索片段可能被发送出去。
- 查询日志默认关闭；开启 `QUERY_LOGGING_ENABLED` 前确认本地日志目录的访问权限。
