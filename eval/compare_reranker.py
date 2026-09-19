"""Compare RRF with RRF plus a local cross-encoder reranker."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eval.run_eval import evaluate_cases, load_cases


def baseline_summary(report: dict) -> dict:
    """Return a compact, committable summary of a full comparison report."""
    run = report["run"]
    rrf = report["rrf"]["summary"]
    reranked = report["rrf_reranker"]["summary"]
    recall_key = f"recall_at_{run['top_k']}"
    return {
        "created_at": run["created_at"],
        "corpus": {
            "document_count": run["index_document_count"],
            "chunk_count": run["index_chunk_count"],
        },
        "evaluation": {
            "case_count": rrf["case_count"],
            "answerable_case_count": rrf["answerable_case_count"],
            "top_k": run["top_k"],
            "candidate_k": run["candidate_k"],
        },
        "rrf": {
            recall_key: rrf["recall_at_k"],
            "mrr": rrf["mrr"],
            "source_coverage": rrf["source_coverage"],
            "avg_total_retrieval_latency_ms": rrf["avg_total_retrieval_latency_ms"],
        },
        "rrf_reranker": {
            "model": run["reranker_model"],
            recall_key: reranked["recall_at_k"],
            "mrr": reranked["mrr"],
            "source_coverage": reranked["source_coverage"],
            "avg_candidate_retrieval_latency_ms": reranked["avg_retrieval_latency_ms"],
            "avg_rerank_latency_ms": reranked["avg_rerank_latency_ms"],
            "avg_total_retrieval_latency_ms": reranked["avg_total_retrieval_latency_ms"],
        },
    }


def _comparison_markdown(report: dict) -> str:
    top_k = report["run"]["top_k"]
    lines = [
        "# RRF vs RRF + Reranker",
        "",
        f"| Method | Recall@{top_k} | MRR | Source coverage | Retrieval ms | Rerank ms | Total ms |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name in ("rrf", "rrf_reranker"):
        metrics = report[name]["summary"]
        lines.append(
            f"| {name} | {metrics['recall_at_k']} | {metrics['mrr']} | "
            f"{metrics['source_coverage']} | {metrics['avg_retrieval_latency_ms']} | "
            f"{metrics['avg_rerank_latency_ms']} | "
            f"{metrics['avg_total_retrieval_latency_ms']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, default=PROJECT_ROOT / "eval" / "questions.jsonl")
    parser.add_argument("--expected", type=Path, default=PROJECT_ROOT / "eval" / "expected.jsonl")
    parser.add_argument("--index-dir", type=Path, default=PROJECT_ROOT / "index")
    parser.add_argument("--reranker-model", required=True, help="Local CrossEncoder model path")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-k", type=int, default=20)
    parser.add_argument("--report-dir", type=Path, default=PROJECT_ROOT / "eval" / "reports")
    parser.add_argument(
        "--baseline-output",
        type=Path,
        help="Optional path for a compact baseline JSON suitable for version control",
    )
    args = parser.parse_args()
    if args.top_k < 1:
        parser.error("--top-k must be >= 1")
    if args.candidate_k < args.top_k:
        parser.error("--candidate-k must be >= --top-k")

    import torch

    from config import BGE_MODEL_PATH
    from modules.embedding_runtime import SentenceTransformer
    from modules.indexer import HybridIndex
    from modules.reranker import CrossEncoderReranker

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = SentenceTransformer(BGE_MODEL_PATH, device=device)
    index = HybridIndex(args.index_dir, model=model)
    if index.faiss_index is None:
        raise RuntimeError("No usable index. Run tools/rebuild_index.py first.")
    reranker = CrossEncoderReranker(args.reranker_model, device=device)
    cases = load_cases(args.questions, args.expected)

    baseline = evaluate_cases(cases, index.query, top_k=args.top_k)
    reranked = evaluate_cases(
        cases,
        lambda question, top_k: index.query(question, top_k=args.candidate_k),
        top_k=args.top_k,
        rerank=reranker.rerank,
    )
    report = {
        "run": {
            "created_at": datetime.now().astimezone().isoformat(),
            "top_k": args.top_k,
            "candidate_k": args.candidate_k,
            "reranker_model": args.reranker_model,
            "device": device,
            "index_document_count": len(
                {chunk.document_id for chunk in index.chunks.values()}
            ),
            "index_chunk_count": len(index.chunks),
        },
        "rrf": baseline,
        "rrf_reranker": reranked,
    }
    args.report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = args.report_dir / f"reranker-comparison-{stamp}.json"
    md_path = args.report_dir / f"reranker-comparison-{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_comparison_markdown(report), encoding="utf-8")
    if args.baseline_output:
        args.baseline_output.parent.mkdir(parents=True, exist_ok=True)
        args.baseline_output.write_text(
            json.dumps(baseline_summary(report), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(_comparison_markdown(report))
    print(f"Report: {json_path}")
    if args.baseline_output:
        print(f"Baseline: {args.baseline_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
