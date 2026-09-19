import json
import os
import time
import uuid
from pathlib import Path

import ollama

from config import INDEX_DIR, LLM_CONFIG, TERMINOLOGY_REVIEW_PATH
from modules.claim_judge import ClaimJudge, OllamaClaimJudge
from modules.claim_grounding import is_abstention_answer, not_applicable, validate_claims
from modules.medical_safety import evaluate_medical_safety, load_review_rules
from modules.query_engine import QueryEngine

_ollama_client = ollama.Client(host=LLM_CONFIG["host"])

MAX_CONTEXT_CHARS = 2800

SYSTEM_QA = """你是一个基于视频知识库的问答助手。
规则：
1. 只能使用提供的知识片段回答。
2. 每个独立结论必须在同一行末尾引用来源，禁止把引用放到下一行或集中放在文末。必须逐字复制上下文 `citation:` 后面的完整引用，禁止自行组合 chunk_id 和 S 编号。
3. 如果片段包含“具体知识点、问题分析、优缺点分析、证据与结论、风险与限制”等结构化内容，优先使用这些内容。
4. 无法从片段直接得出的结论，必须标注【推测】。
5. 完全无法回答时只输出：不知道。不要附加依据。
6. 回答尽量简洁，避免重复。
7. 严禁把 `chunk_id`、`source_refs`、`片段1`、`证据块1` 等占位文字当作引用。

输出结构：
【结论】
1. 独立结论文字 [实际 chunk id | 实际 source refs]
2. 独立结论文字 [实际 chunk id | 实际 source refs]

不要另设“依据”章节，不要重复同一结论。"""

SYSTEM_TOPIC = """你是{domain}领域的短视频选题策划专家。
请基于知识库片段生成 5 个短视频选题。

规则：
1. 每个选题必须来源于知识片段。
2. 每个选题给 3 个具体切入角度。
3. 角度要具体，不要泛泛而谈。
4. 优先利用知识片段中的问题分析、优缺点、风险和证据。
5. 每个角度必须标注来源：[video_id start-end]。

输出格式：
选题1：xxx
- 角度1：xxx [video_id start-end]
- 角度2：xxx [video_id start-end]
- 角度3：xxx [video_id start-end]"""

SYSTEM_SCRIPT = """你是短视频脚本写手。
请基于知识库片段生成 60 秒短视频脚本。

规则：
1. 只能使用提供的知识片段。
2. 不允许编造。
3. 如果知识片段中包含优缺点、风险、证据不足或未解决问题，需要在脚本中体现。

结构要求：
[开头 0-3s] 钩子，吸引注意力
[正文 3-50s] 核心知识，带节奏变化
[结尾 50-60s] 总结或引导关注

输出格式：
标题：xxx

[开头] ...
[正文] ...
[结尾] ..."""


def _build_context(results: list) -> str:
    context = ""
    included = 0
    for i, r in enumerate(results):
        ts = f"{r['start']:.0f}s-{r['end']:.0f}s" if r.get("has_timestamp", True) else "N/A"
        source_refs = ",".join(r.get("source_refs", [])) or "N/A"
        citation = f"[{r.get('chunk_id', '')} | {source_refs}]"
        block = (
            f"[证据块]\n"
            f"来源: {r['video_id']} {ts}\n"
            f"chunk_id: {r.get('chunk_id', '')}\n"
            f"source_refs: {source_refs}\n"
            f"citation: {citation}\n"
            f"chunk_type: {r.get('chunk_type', 'document')}\n"
            f"review_status: {r.get('review_status', '') or 'unknown'}\n"
            f"内容: {r['text']}\n\n"
        )
        if len(context) + len(block) > MAX_CONTEXT_CHARS:
            if included == 0:
                context += block[:MAX_CONTEXT_CHARS]
            else:
                context += f"...（共 {len(results)} 个片段，已截断前 {included} 个）\n"
            break
        context += block
        included += 1
    return context


def _handle_empty(result: dict) -> str | None:
    if result["status"] == "empty":
        return "知识库中暂无相关内容，无法回答。请先添加更多视频到知识库。"
    if result["status"] == "error":
        return f"检索系统异常：{result['meta'].get('error', '未知错误')}"
    return None


def _ask(messages: list, temp: float) -> str:
    answer, _ = ask_with_usage(messages, temp)
    return answer


def ask_with_usage(messages: list, temp: float) -> tuple[str, dict]:
    """Ask Ollama and preserve optional runtime counters for offline evaluation."""
    resp = _ollama_client.chat(
        model=LLM_CONFIG["model"],
        messages=messages,
        options={"temperature": temp},
    )
    usage = {
        key: resp[key]
        for key in ("prompt_eval_count", "eval_count", "prompt_eval_duration", "eval_duration")
        if key in resp
    }
    return resp["message"]["content"], usage


def answer_question_with_usage(
    engine: QueryEngine,
    question: str,
    *,
    top_k: int = 5,
    filters: dict | None = None,
    rewrite: bool | None = None,
    include_retrieval: bool = False,
    semantic_grounding: bool = False,
    semantic_judge: ClaimJudge | None = None,
) -> tuple[str, dict]:
    """Answer a question with Ollama usage metadata when the server exposes it."""
    query_kwargs = {"top_k": top_k}
    if filters is not None:
        query_kwargs["filters"] = filters
    if rewrite is not None:
        query_kwargs["rewrite"] = rewrite
    result = engine.query(question, **query_kwargs)
    if empty := _handle_empty(result):
        grounding = not_applicable("retrieval_empty")
        log_path = _write_query_log(
            question, result, empty, {}, safety=None, grounding=grounding
        )
        usage = {"retrieval_log_path": str(log_path), "claim_grounding": grounding}
        if include_retrieval:
            usage["retrieval"] = result
        return empty, usage
    safety = evaluate_medical_safety(
        question,
        result["results"],
        load_review_rules(TERMINOLOGY_REVIEW_PATH),
    )
    if safety.action == "block":
        terms = "、".join(safety.matched_terms)
        detail = f"（命中：{terms}）" if terms else ""
        answer = (
            "原始转录或医学术语仍待人工核查"
            f"{detail}，当前证据不足以生成确定性医疗结论。"
            "请回看原视频并由专业人员复核。"
        )
        grounding = not_applicable("medical_safety_block")
        log_path = _write_query_log(
            question, result, answer, {}, safety=safety.to_dict(), grounding=grounding
        )
        usage = {
            "retrieval_log_path": str(log_path),
            "safety_gate": safety.to_dict(),
            "claim_grounding": grounding,
        }
        if include_retrieval:
            usage["retrieval"] = result
        return answer, usage
    context = _build_context(result["results"])
    system_prompt = SYSTEM_QA
    if safety.action == "warn":
        system_prompt += (
            "\n7. 当前材料尚未完成人工医学审核；必须明确说明这一限制，"
            "不得给出诊断、处方、剂量或替代就医的确定性建议。"
        )
    answer, usage = ask_with_usage(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"知识片段：\n{context}\n\n问题：{question}"},
        ],
        0.3,
    )
    if is_abstention_answer(answer):
        grounding = not_applicable("model_abstention")
    else:
        semantic_grounding = semantic_grounding or semantic_judge is not None
        if semantic_grounding and semantic_judge is None:
            semantic_judge = OllamaClaimJudge(_ollama_client, LLM_CONFIG["model"])
        grounding = validate_claims(
            answer,
            result["results"],
            semantic_judge=semantic_judge if semantic_grounding else None,
        )
    log_path = _write_query_log(
        question, result, answer, usage, safety=safety.to_dict(), grounding=grounding
    )
    usage = dict(usage)
    usage["retrieval_log_path"] = str(log_path)
    usage["safety_gate"] = safety.to_dict()
    usage["claim_grounding"] = grounding
    if include_retrieval:
        usage["retrieval"] = result
    return answer, usage


def _write_query_log(
    question: str,
    result: dict,
    answer: str,
    usage: dict,
    *,
    safety: dict | None,
    grounding: dict,
) -> Path:
    log_dir = INDEX_DIR / "query_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"{time.time_ns()}-{uuid.uuid4().hex[:8]}"
    payload = {
        "run_id": run_id,
        "created_at_ns": time.time_ns(),
        "query": question,
        "query_version": result.get("meta", {}).get("query_version", "raw-v1"),
        "retrieval_query": result.get("meta", {}).get("retrieval_query", question),
        "applied_rewrite_rules": result.get("meta", {}).get("applied_rewrite_rules", []),
        "metadata_filters": result.get("meta", {}).get("filters", {}),
        "retrieval": result,
        "used_chunk_ids": grounding["valid_cited_chunk_ids"],
        "used_source_refs": grounding["valid_cited_source_refs"],
        "claim_grounding": grounding,
        "answer": answer,
        "answer_model": LLM_CONFIG["model"],
        "prompt_version": (
            "qa-grounded-v4-semantic"
            if grounding.get("semantic_status") in {"ok", "partial_failure"}
            else "qa-grounded-v3"
        ),
        "safety_gate": safety,
        "usage": usage,
    }
    path = log_dir / f"{run_id}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))
    return path


def answer_question(engine: QueryEngine, question: str) -> str:
    answer, _ = answer_question_with_usage(engine, question)
    return answer


def generate_topics(engine: QueryEngine, domain: str) -> str:
    query = f"{domain} 核心问题 常见误区 关键决策 案例 优缺点 风险 证据 争议"
    result = engine.query(query, top_k=10)
    if empty := _handle_empty(result):
        return empty
    context = _build_context(result["results"])
    return _ask(
        [
            {"role": "system", "content": SYSTEM_TOPIC.format(domain=domain)},
            {"role": "user", "content": f"知识片段：\n{context}\n\n请生成 5 个{domain}领域短视频选题。"},
        ],
        0.7,
    )


def generate_script(engine: QueryEngine, topic: str) -> str:
    result = engine.query(topic, top_k=8)
    if empty := _handle_empty(result):
        return empty
    context = _build_context(result["results"])
    return _ask(
        [
            {"role": "system", "content": SYSTEM_SCRIPT},
            {"role": "user", "content": f"知识片段：\n{context}\n\n选题：{topic}\n\n请输出 60 秒短视频脚本。"},
        ],
        0.5,
    )
