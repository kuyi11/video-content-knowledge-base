"""LLM-backed semantic entailment judge for answer claims and cited evidence."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Protocol


SEMANTIC_VERDICTS = frozenset({"supported", "contradicted", "insufficient_evidence"})
CONFIDENCE_LEVELS = frozenset({"high", "medium", "low"})
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


@dataclass(frozen=True)
class ClaimJudgment:
    verdict: str
    confidence: str
    rationale: str
    evidence_chunk_ids: tuple[str, ...]

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["evidence_chunk_ids"] = list(self.evidence_chunk_ids)
        return payload


class ClaimJudge(Protocol):
    def __call__(self, claim: str, evidence: Sequence[dict]) -> ClaimJudgment | dict:
        """Classify whether evidence supports, contradicts, or cannot establish a claim."""


def parse_judgment(payload: str | dict, allowed_chunk_ids: set[str]) -> ClaimJudgment:
    """Parse and strictly validate one semantic-judge response."""
    if isinstance(payload, str):
        cleaned = _JSON_FENCE_RE.sub("", payload.strip()).strip()
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Claim judge returned invalid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Claim judge response must be a JSON object")

    verdict = str(payload.get("verdict", "")).strip().lower()
    confidence = str(payload.get("confidence", "")).strip().lower()
    rationale = str(payload.get("rationale", "")).strip()
    raw_ids = payload.get("evidence_chunk_ids", [])
    if verdict not in SEMANTIC_VERDICTS:
        raise ValueError(f"Unsupported claim verdict: {verdict or '<empty>'}")
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError(f"Unsupported claim confidence: {confidence or '<empty>'}")
    if not rationale:
        raise ValueError("Claim judge rationale cannot be empty")
    if not isinstance(raw_ids, list) or any(not isinstance(item, str) for item in raw_ids):
        raise ValueError("evidence_chunk_ids must be a list of strings")
    chunk_ids = tuple(dict.fromkeys(item.strip() for item in raw_ids if item.strip()))
    unknown = set(chunk_ids) - allowed_chunk_ids
    if unknown:
        raise ValueError(f"Claim judge cited unavailable chunks: {sorted(unknown)}")
    if verdict in {"supported", "contradicted"} and not chunk_ids:
        raise ValueError(f"{verdict} verdict must cite at least one evidence chunk")
    return ClaimJudgment(verdict, confidence, rationale, chunk_ids)


class OllamaClaimJudge:
    """Judge one claim against its exact cited chunks using a local Ollama model."""

    SYSTEM_PROMPT = """你是严格的证据蕴含评测器，只判断给定证据能否支持 claim。
只能输出一个 JSON 对象，不要输出 Markdown：
{"verdict":"supported|contradicted|insufficient_evidence","confidence":"high|medium|low","rationale":"简短理由","evidence_chunk_ids":["实际 chunk_id"]}

判定规则：
1. supported：证据直接陈述或必然推出 claim；不能依靠外部医学常识补全。
2. contradicted：证据直接否定 claim 或与 claim 明确冲突。
3. insufficient_evidence：证据相关但不足、含糊、仅部分覆盖，或无法判断。
4. 不评判来源本身在临床上是否正确，只评判 claim 与所给文本的关系。
5. supported 和 contradicted 必须列出实际使用的 evidence_chunk_ids。"""

    def __init__(self, client, model: str):
        self.client = client
        self.model = model

    def __call__(self, claim: str, evidence: Sequence[dict]) -> ClaimJudgment:
        if not claim.strip():
            raise ValueError("Claim cannot be empty")
        if not evidence:
            return ClaimJudgment(
                "insufficient_evidence",
                "high",
                "没有可用于验证该 claim 的引用证据。",
                (),
            )
        compact_evidence = [
            {
                "chunk_id": str(item["chunk_id"]),
                "source_refs": list(item.get("source_refs") or []),
                "text": str(item.get("text", "")),
            }
            for item in evidence
        ]
        allowed_ids = {item["chunk_id"] for item in compact_evidence}
        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {"claim": claim, "evidence": compact_evidence},
                    ensure_ascii=False,
                ),
            },
        ]
        response = self.client.chat(
            model=self.model,
            messages=messages,
            format="json",
            options={"temperature": 0},
        )
        content = response["message"]["content"]
        try:
            return parse_judgment(content, allowed_ids)
        except ValueError as first_error:
            messages.extend(
                [
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": (
                            "上一个 JSON 不符合约束："
                            f"{first_error}。evidence_chunk_ids 只能逐字选择以下值："
                            f"{sorted(allowed_ids)}；Sxxxx 是 source_refs，不是 chunk_id。"
                            "请只返回修正后的 JSON。"
                        ),
                    },
                ]
            )
            retry = self.client.chat(
                model=self.model,
                messages=messages,
                format="json",
                options={"temperature": 0},
            )
            return parse_judgment(retry["message"]["content"], allowed_ids)
