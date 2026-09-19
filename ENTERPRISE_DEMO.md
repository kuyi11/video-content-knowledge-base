# 10 分钟企业 Demo

## 演示目标

用一条医学问题展示从多媒体知识入库、混合检索、证据引用、风险闸门到审计日志的
完整链路，并用离线评测数据说明质量与延迟取舍。

## 演示前准备

```powershell
uv run --no-sync python tools/index_health.py
uv run --no-sync uvicorn api:app --host 127.0.0.1 --port 8000
```

确认健康检查返回：

```text
status = ok
missing_from_index = []
extra_in_index = []
changed_sources = []
metadata_errors = []
```

浏览器打开 `http://127.0.0.1:8000/docs`，预先准备两个问题：

- 普通知识问题：`脑梗发生后多久内需要尽快就医？`
- 医学风险问题：选择术语核查清单中一条仍为阻断级的术语提问。

## 0:00-1:00 业务问题

企业拥有大量视频、字幕和会议录音。传统搜索只能匹配关键词，普通 RAG 又容易把
二次摘要当成事实，导致来源不清、医学术语错误和无法复现检索结果。

本系统将视频转录为带时间戳的原始证据，同时生成结构化知识；两层内容分别索引，
回答必须引用本次检索得到的真实 chunk 和来源编号。

## 1:00-2:30 数据流水线

展示 README 架构图，并打开一份 vault 文档：

```text
B站 URL
 -> 平台字幕 / ASR
 -> 时间窗事实提取
 -> 多 profile 结构化
 -> Obsidian
 -> BGE-M3 + Whoosh
 -> RRF
 -> Agent
```

说明每个结构化观点保存 `source_refs`，可追溯到 `Sxxxx` 原始证据；索引 manifest 保存
文档路径、内容 hash、元数据和 generation id。

## 2:30-3:30 索引健康检查

展示 `tools/index_health.py` 的输出。重点解释：

- `missing_from_index`：vault 有、索引没有；
- `extra_in_index`：索引有、vault 已删除；
- `changed_sources`：内容或元数据发生变化；
- `metadata_errors`：FAISS 与 Whoosh 版本、维度或 generation 不一致。

当前基线为 33 个文档、577 个 chunk，健康状态为 `ok`。

## 3:30-5:30 结构化查询

在 Swagger 的 `POST /query` 输入：

```json
{
  "question": "脑梗发生后多久内需要尽快就医？",
  "top_k": 5,
  "filters": {"domain": "medical"},
  "rewrite": false
}
```

按顺序展示响应中的：

1. `answer`：面向用户的回答；
2. `citations`：实际使用的 chunk、`source_refs`、路径和时间段；
3. `claim_grounding`：每条 claim 的引用完整性结果；
4. `safety_gate`：医学风险处置；
5. `retrieval`：Top K 排名、分数和元数据；
6. `query_log_path`：可供审计和复现的完整日志。

明确说明 claim grounding 当前验证引用完整性，医学真实性仍需专家审核。

## 5:30-7:00 医学风险闸门

提交预先准备的阻断级术语问题。展示系统在调用 LLM 前停止生成，并返回“原始转录或
医学术语仍待人工核查”。

解释三级行为：

- `allow`：证据条件满足；
- `warn`：允许受限回答，但禁止诊断、处方和剂量；
- `block`：不调用 LLM，要求回看原视频并人工复核。

## 7:00-8:30 离线评测

展示 `RAG_HARDENING_REPORT.md` 的对比表：

| 方法 | Recall@5 | MRR | Source coverage | 总检索延迟 |
| --- | ---: | ---: | ---: | ---: |
| RRF | 0.8571 | 0.7738 | 0.8571 | 44.8 ms |
| RRF + reranker | 1.0000 | 0.9077 | 0.9821 | 998.6 ms |

结论是 reranker 提升质量，但当前总延迟约增加 1.15 秒。因此在线默认使用 RRF，
质量优先或离线场景启用 reranker；这是基于指标作出的产品决策。

## 8:30-9:30 部署与运维

展示 `docker-compose.yml`：代码进入镜像，模型、索引和 vault 通过 volume 挂载，Ollama
运行在宿主机。说明知识内容与应用代码分离，索引可重建，健康检查可用于发布前门禁。

现场执行使用可复现脚本：

```powershell
.\tools\docker_demo.ps1 -Action Config
.\tools\docker_demo.ps1 -Action Demo -KeepRunning
Start-Process 'http://127.0.0.1:8000/docs'
```

脚本会固定 D/F 盘模型和 vault 路径，等待 `/health` 就绪，发送带医学 metadata filter
和 semantic grounding 的查询，并把回答、引用、健康报告和容器日志写入
`eval/reports/docker-demo-*.json`。演示结束后执行：

```powershell
.\tools\docker_demo.ps1 -Action Down
```

## 9:30-10:00 总结

收束到三点：

- 完整链路：视频入口到结构化答案；
- 可度量：固定评测集、Recall@5、MRR、grounded rate 与延迟；
- 可审计：源文件 hash、chunk 引用、医学闸门和查询日志。

最后说明当前边界：时间对齐仍依赖字幕/ASR，claim 语义裁判只判断文本证据关系，
不能证明医学观点本身正确；30 条 evidence 已完成人工来源核对，但医学结论仍不能替代
专业人员审核。Qwen/GLM 双模型在 30 条人工集上的一致率为 `96.67%`，唯一分歧
`thyroid-002` 已保留为人工边界案例。
