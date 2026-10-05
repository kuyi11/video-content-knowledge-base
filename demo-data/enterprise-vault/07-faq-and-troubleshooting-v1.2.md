---
id: enterprise-faq-and-troubleshooting-v12
source: portfolio://enterprise-kb/faq-and-troubleshooting/v1.2
profile: default
chunk_type: document
domain: enterprise
quality: human_reviewed
review_status: reviewed
source_of_truth: true
risk_level: low
title: 星云 FAQ 与故障排查 v1.2
---

# 星云 FAQ 与故障排查 v1.2

资料性质：模拟企业资料，基于公开产品文档结构整理。

## 无法上传文档

先确认文件格式、编码和大小是否符合资料标准，再检查上传者是否具有编辑权限。若文件是扫描图片或正文不可复制，应先执行 OCR 或人工转写。持续失败时记录请求时间、文件类型和错误码，转交管理员或实施人员。

## 检索不到正确内容

先检查资料是否已经成功导入并完成索引，再确认问题中的产品名、错误码和版本写法。可以用更具体的关键词重试，并检查 domain、profile 和质量过滤条件。若资料内容已更新但索引健康报告仍显示 changed_sources，应重新构建索引。

## 回答没有引用

检查召回结果是否为空、资料是否包含有效章节，以及模型是否输出了实际 chunk 引用。没有足够证据时应接受证据不足提示，不能手工补写引用或把模型常识当作产品事实。

## API 鉴权失败

确认 Authorization header 使用 Bearer token，token 是否过期或被轮换，并检查请求是否发送到正确的本地端口。不要把 token 写入截图、日志或问题文本。

## 何时转人工

涉及生产数据修改、权限变更、财务动作、资料冲突、持续索引失败或回答缺少有效证据时，停止自动处理并转交授权管理员或实施人员。
