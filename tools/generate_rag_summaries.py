"""Generate additive, source-grounded RAG summaries from Obsidian Clippings.

The original clipping notes are never modified. Each generated note uses the
timestamped Transcript as its source of truth and records stable Sxxxx refs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import ollama
import yaml


TIMESTAMP_RE = re.compile(
    r"^\s*\*\*(?P<clock>\d{1,2}:\d{2}(?::\d{2})?)\*\*\s*[·|:-]\s*(?P<text>.+?)\s*$"
)
VIDEO_ID_RE = re.compile(r"/video/(BV[\w]+)", re.I)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) != 3:
        return {}, text
    try:
        data = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        data = {}
    return data if isinstance(data, dict) else {}, parts[2]


def clock_seconds(value: str) -> float:
    parts = [int(x) for x in value.split(":")]
    if len(parts) == 2:
        return float(parts[0] * 60 + parts[1])
    return float(parts[0] * 3600 + parts[1] * 60 + parts[2])


def extract_transcript(body: str) -> list[dict]:
    match = re.search(r"(?im)^##\s+Transcript\s*$", body)
    if not match:
        return []
    transcript = body[match.end() :]
    rows = []
    for line in transcript.splitlines():
        found = TIMESTAMP_RE.match(line)
        if not found:
            continue
        rows.append({"clock": found.group("clock"), "start": clock_seconds(found.group("clock")), "text": found.group("text").strip()})
    for previous, current in zip(rows, rows[1:]):
        previous["end"] = current["start"]
    if rows:
        rows[-1]["end"] = rows[-1]["start"]
    return rows


def transcript_with_refs(rows: list[dict]) -> str:
    return "\n".join(
        f"[S{i:04d} t={row['clock']}] {row['text']}"
        for i, row in enumerate(rows, start=1)
    )


def source_ref_list(rows: list[dict]) -> list[str]:
    return [f"S{i:04d}" for i in range(1, len(rows) + 1)]


def video_id(frontmatter: dict) -> str:
    value = str(frontmatter.get("source_url") or frontmatter.get("source") or "")
    match = VIDEO_ID_RE.search(value)
    return match.group(1) if match else "unknown"


def prompt_for(domain: str, source_title: str, transcript: str) -> str:
    if domain == "medical":
        sections = """## 一句话结论
## 内容概览
## 文章级索引
## 关键医学信息
## 症状与可能相关情况
## 检查、治疗与干预
## 风险信号与就医建议
## 证据与信息边界
## 实用行动清单
## 待核实问题
## 原文定位"""
        domain_rules = "涉及诊断、急症、用药、剂量、手术、妊娠、儿童或性传播感染时，必须写明需专业人员确认；不得把症状写成诊断。"
    else:
        sections = """## 一句话结论
## 内容概览
## 文章级索引
## 核心观点与事实
## 行为与干预建议
## 证据质量与因果边界
## 风险、禁忌与不适用人群
## 实用行动清单
## 待核实问题
## 原文定位"""
        domain_rules = "不得把生活方式建议改写成对所有人的处方；涉及疾病、用药、妊娠或儿童时注明需专业人员确认。"
    return f"""你是严格的中文医学/健康信息整理编辑。请只根据下面的原始时间戳 Transcript 生成结构化摘要，不使用模型常识补事实，不把标题当证据。

这是一个待审核的 structured_summary，不是 source_of_truth。{domain_rules}

规则：
1. 每个可独立验证的事实或观点都必须使用格式 `C001｜内容｜信息类型：事实/医生观点/个人经历/研究结果/指南建议/推测｜来源：S0001`，来源只能使用 Transcript 中实际存在的 Sxxxx。
2. 没有可靠来源的内容写“页面未提供可靠来源”，不要猜测。
3. 明确区分相关与因果、研究结果与视频观点、症状与诊断。
4. 保留不确定性、ASR 疑似错词、缺失数字、单位和待核问题。
5. 不输出 Markdown 代码围栏，不重复一级标题，不写前言。

标题：{source_title}

严格使用以下章节：
{sections}

原始 Transcript（唯一事实来源）：
{transcript}
"""


def clean_model_body(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:markdown)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def append_source_locations(body: str, rows: list[dict]) -> str:
    if "## 原文定位" in body:
        return body
    refs = []
    for value in re.findall(r"\bS(\d{4})\b", body):
        index = int(value)
        if 1 <= index <= len(rows) and index not in refs:
            refs.append(index)
    if not refs:
        return body + "\n\n## 原文定位\n\n原文未提供可由生成结果确认的定位。"
    lines = ["## 原文定位", ""]
    for index in refs:
        row = rows[index - 1]
        preview = row["text"][:100].strip()
        lines.append(f"- S{index:04d}｜t={row['clock']}｜{preview}")
    return body + "\n\n" + "\n".join(lines)


def normalize_claim_ids(body: str) -> str:
    """Make claim IDs unique and sequential without changing claim text."""
    counter = 0

    def replace(match: re.Match) -> str:
        nonlocal counter
        counter += 1
        return f"C{counter:03d}｜"

    return re.sub(r"(?m)^C\d{3}\s*[｜|]", replace, body)


def cited_source_refs(body: str, row_count: int) -> list[str]:
    refs = []
    for value in re.findall(r"\bS(\d{4})\b", body):
        index = int(value)
        ref = f"S{index:04d}"
        if 1 <= index <= row_count and ref not in refs:
            refs.append(ref)
    return refs


def render_note(frontmatter: dict, body: str) -> str:
    return "---\n" + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip() + "\n---\n\n" + body + "\n"


def generate_one(client: ollama.Client, source: Path, output: Path, model: str) -> dict:
    raw = source.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(raw)
    rows = extract_transcript(body)
    if not rows:
        raise ValueError(f"No timestamped Transcript found: {source}")
    domain = str(fm.get("domain") or ("medical" if "Medical" in source.parts else "wellness"))
    refs = source_ref_list(rows)
    source_title = str(fm.get("source_title") or source.stem)
    response = client.chat(
        model=model,
        messages=[{"role": "user", "content": prompt_for(domain, source_title, transcript_with_refs(rows))}],
        options={"temperature": 0.1, "num_ctx": 32768, "num_predict": 7000},
    )
    generated = normalize_claim_ids(clean_model_body(response["message"]["content"]))
    generated = append_source_locations(generated, rows)
    required = ["## 一句话结论", "## 内容概览", "## 文章级索引", "## 待核实问题", "## 原文定位"]
    missing = [section for section in required if section not in generated]
    if missing:
        raise ValueError(f"Generated note missing sections {missing}: {source}")
    output.parent.mkdir(parents=True, exist_ok=True)
    cited_refs = cited_source_refs(generated, len(rows))
    out_fm = {
        "source": fm.get("source") or fm.get("source_url", ""),
        "source_url": fm.get("source_url") or fm.get("source", ""),
        "source_title": source_title,
        "video_id": video_id(fm),
        "domain": domain,
        "profile": f"{domain}_rag_summary",
        "document_kind": "rag_summary",
        "chunk_type": "structured_summary",
        "quality": "draft",
        "source_of_truth": False,
        "text_source": "ollama_transcript_grounded",
        "review_status": "pending",
        "risk_level": "high" if domain == "medical" else "medium",
        "answer_policy": "retrieval_only_unreviewed",
        "source_refs": cited_refs,
        "raw_source_path": str(source),
        "raw_transcript_segments": len(rows),
        "generator_model": model,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    output.write_text(render_note(out_fm, generated), encoding="utf-8")
    return {"source": str(source), "output": str(output), "segments": len(rows), "source_refs": len(cited_refs)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("local-data/vault/Clippings"))
    parser.add_argument("--output", type=Path, default=Path("local-data/vault/RAG_Summaries"))
    parser.add_argument("--model", default="qwen2.5:7b")
    parser.add_argument("--host", default="http://localhost:11434")
    args = parser.parse_args()
    client = ollama.Client(host=args.host)
    sources = [p for p in sorted(args.input.rglob("*.md")) if "RAG_Summaries" not in p.parts]
    results = []
    for source in sources:
        relative = source.relative_to(args.input)
        domain = "medical" if "Medical" in source.parts else "wellness"
        output = args.output / domain / (source.stem + "__rag_summary.md")
        print(f"[GENERATE] {source}", flush=True)
        results.append(generate_one(client, source, output, args.model))
        print(f"[WROTE] {output}", flush=True)
    print(json.dumps({"count": len(results), "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
