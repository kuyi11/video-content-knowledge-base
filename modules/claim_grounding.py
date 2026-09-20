"""Validate claim citations and optionally judge semantic evidence support."""

from __future__ import annotations

import re
from collections.abc import Iterable

from modules.claim_judge import ClaimJudge, ClaimJudgment, parse_judgment
from modules.evidence_model import allowed_for_answer, build_evidence_units


CITATION_RE = re.compile(r"\[\s*([A-Za-z0-9_-]+)\s*\|\s*([^\]]*)\]")
SOURCE_REF_RE = re.compile(r"\bS\d{4}\b")
HEADING_RE = re.compile(r"^【[^】]+】\s*$")
HEADING_PREFIX_RE = re.compile(r"^【[^】]+】\s*")
LIST_PREFIX_RE = re.compile(r"^(?:[-*]|\d+[.)、])\s*")
ABSTENTION_RE = re.compile(r"^(?:不知道|无法回答)(?:[。！!]|$)")


def is_abstention_answer(answer: str) -> bool:
    """Return true when the answer explicitly declines to make a factual claim."""
    first_line = next((line.strip() for line in answer.splitlines() if line.strip()), "")
    normalized = CITATION_RE.sub("", first_line).strip()
    normalized = LIST_PREFIX_RE.sub("", normalized).strip()
    normalized = HEADING_PREFIX_RE.sub("", normalized).strip()
    return bool(ABSTENTION_RE.match(normalized))


def not_applicable(reason: str, *, index_generation_id: str = "") -> dict:
    """Return a stable report for answers deliberately not generated from RAG."""
    return {
        "status": "not_applicable",
        "reason": reason,
        "index_generation_id": index_generation_id,
        "claim_count": 0,
        "grounded_claim_count": 0,
        "invalid_claim_count": 0,
        "claim_citation_grounding_rate": None,
        "semantic_status": "not_applicable",
        "semantic_claim_count": 0,
        "semantically_supported_claim_count": 0,
        "contradicted_claim_count": 0,
        "insufficient_evidence_claim_count": 0,
        "claim_semantic_grounding_rate": None,
        "valid_cited_chunk_ids": [],
        "valid_cited_source_refs": [],
        "review_claims": [],
        "evidence_units": [],
        "claims": [],
    }


def validate_claims(
    answer: str,
    retrieved_results: Iterable[dict],
    *,
    semantic_judge: ClaimJudge | None = None,
    semantic_max_claims: int | None = None,
    index_generation_id: str = "",
) -> dict:
    """Validate every non-heading answer line as an independently cited claim.

    A claim is valid only when it has at least one citation and every cited
    ``chunk_id`` was returned for the query.  Each cited source reference must
    be listed on that exact chunk; ``N/A`` is valid only for a chunk with no
    source references.
    """
    evidence_units = build_evidence_units(retrieved_results)
    evidence = {
        chunk_id: {
            "source_refs": set(unit.source_refs),
            "text": unit.text,
        }
        for chunk_id, unit in evidence_units.items()
    }
    claims = []
    valid_chunk_ids: set[str] = set()
    valid_source_refs: set[str] = set()

    for line_no, raw_line in enumerate(answer.splitlines(), start=1):
        line = raw_line.strip()
        if not line or HEADING_RE.fullmatch(line):
            continue
        claim_text = CITATION_RE.sub("", line).strip()
        claim_text = LIST_PREFIX_RE.sub("", claim_text).strip()
        claim_text = HEADING_PREFIX_RE.sub("", claim_text).strip()
        # A bare citation in an evidence list supports the preceding prose but
        # does not itself make a factual claim.
        if not claim_text:
            continue

        citations = []
        claim_errors = []
        matches = list(CITATION_RE.finditer(line))
        if not matches:
            claim_errors.append("missing_citation")
        for match in matches:
            chunk_id = match.group(1)
            raw_refs = match.group(2).strip()
            citation_errors = []
            evidence_item = evidence.get(chunk_id)
            refs = sorted(set(SOURCE_REF_RE.findall(raw_refs)))
            if evidence_item is None:
                citation_errors.append("chunk_not_in_retrieval")
            elif raw_refs in {"N/A", ""}:
                if evidence_item["source_refs"]:
                    citation_errors.append("source_refs_missing")
            else:
                if not refs:
                    citation_errors.append("source_refs_malformed")
                elif set(refs) - evidence_item["source_refs"]:
                    citation_errors.append("source_refs_not_in_chunk")
            if not citation_errors:
                valid_chunk_ids.add(chunk_id)
                valid_source_refs.update(refs)
            citations.append(
                {
                    "chunk_id": chunk_id,
                    "source_refs": refs,
                    "valid": not citation_errors,
                    "errors": citation_errors,
                }
            )
            claim_errors.extend(citation_errors)

        claim = {
            "claim_id": f"C{len(claims) + 1:04d}",
            "line": line_no,
            "claim": claim_text,
            "citations": citations,
            "valid": not claim_errors,
            "errors": sorted(set(claim_errors)),
            "semantic_verdict": "not_evaluated",
            "semantic_confidence": None,
            "semantic_rationale": None,
            "semantic_evidence_chunk_ids": [],
            "semantic_error": None,
            "needs_review": False,
            "evidence_unit_ids": [],
            "support_status": "unverified",
            "allowed_for_answer": False,
        }
        if semantic_judge is not None:
            if semantic_max_claims is not None and len(claims) >= semantic_max_claims:
                judgment = ClaimJudgment(
                    "insufficient_evidence",
                    "low",
                    "已达到本次请求的语义裁判预算，需人工复核。",
                    (),
                )
                claim["semantic_error"] = "semantic_claim_budget_exceeded"
            elif claim_errors:
                judgment = ClaimJudgment(
                    "insufficient_evidence",
                    "high",
                    "引用完整性校验失败，无法建立 claim 到证据的有效链路。",
                    (),
                )
            else:
                cited_evidence = [
                    {
                        "chunk_id": citation["chunk_id"],
                        "source_refs": sorted(evidence[citation["chunk_id"]]["source_refs"]),
                        "text": evidence[citation["chunk_id"]]["text"],
                    }
                    for citation in citations
                ]
                try:
                    raw_judgment = semantic_judge(claim_text, cited_evidence)
                    judgment = (
                        raw_judgment
                        if isinstance(raw_judgment, ClaimJudgment)
                        else parse_judgment(
                            raw_judgment,
                            {item["chunk_id"] for item in cited_evidence},
                        )
                    )
                except Exception as exc:
                    judgment = ClaimJudgment(
                        "insufficient_evidence",
                        "low",
                        "语义裁判执行失败，必须转人工复核。",
                        (),
                    )
                    claim["semantic_error"] = str(exc)
            claim.update(
                {
                    "semantic_verdict": judgment.verdict,
                    "semantic_confidence": judgment.confidence,
                    "semantic_rationale": judgment.rationale,
                    "semantic_evidence_chunk_ids": list(judgment.evidence_chunk_ids),
                    "needs_review": judgment.verdict != "supported"
                    or judgment.confidence != "high",
                }
            )
        cited_units = [
            evidence_units[citation["chunk_id"]]
            for citation in citations
            if citation["valid"] and citation["chunk_id"] in evidence_units
        ]
        claim["evidence_unit_ids"] = [unit.evidence_id for unit in cited_units]
        claim["support_status"] = (
            claim["semantic_verdict"]
            if semantic_judge is not None
            else ("citation_validated" if claim["valid"] else "unsupported")
        )
        claim["allowed_for_answer"] = allowed_for_answer(
            cited_units,
            citations_valid=claim["valid"],
            semantic_enabled=semantic_judge is not None,
            semantic_verdict=claim["semantic_verdict"],
        )
        claims.append(claim)

    grounded = sum(claim["valid"] for claim in claims)
    claim_count = len(claims)
    supported = sum(claim["semantic_verdict"] == "supported" for claim in claims)
    contradicted = sum(claim["semantic_verdict"] == "contradicted" for claim in claims)
    insufficient = sum(
        claim["semantic_verdict"] == "insufficient_evidence" for claim in claims
    )
    semantic_errors = sum(bool(claim["semantic_error"]) for claim in claims)
    semantic_enabled = semantic_judge is not None
    return {
        "status": "ok",
        "index_generation_id": index_generation_id,
        "claim_count": claim_count,
        "grounded_claim_count": grounded,
        "invalid_claim_count": claim_count - grounded,
        "claim_citation_grounding_rate": round(grounded / claim_count, 4) if claim_count else None,
        "semantic_status": (
            "partial_failure" if semantic_errors else "ok"
        ) if semantic_enabled else "disabled",
        "semantic_claim_count": claim_count if semantic_enabled else 0,
        "semantically_supported_claim_count": supported,
        "contradicted_claim_count": contradicted,
        "insufficient_evidence_claim_count": insufficient,
        "claim_semantic_grounding_rate": (
            round(supported / claim_count, 4) if semantic_enabled and claim_count else None
        ),
        "valid_cited_chunk_ids": sorted(valid_chunk_ids),
        "valid_cited_source_refs": sorted(valid_source_refs),
        "review_claims": [claim for claim in claims if claim["needs_review"]],
        "evidence_units": [unit.to_dict() for unit in evidence_units.values()],
        "claims": claims,
    }
