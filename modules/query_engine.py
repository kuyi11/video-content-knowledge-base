from config import QUERY_REWRITE_ENABLED, QUERY_REWRITE_PATH
from modules.indexer import HybridIndex
from modules.query_rewriter import QueryRewriter


class QueryEngine:
    MAX_TEXT_LEN = 600
    MAX_TOP_K = 10

    def __init__(self, index: HybridIndex, rewriter: QueryRewriter | None = None):
        self.index = index
        self.rewriter = rewriter or QueryRewriter.from_path(QUERY_REWRITE_PATH)

    def query(
        self,
        question: str,
        top_k: int = 5,
        *,
        filters: dict | None = None,
        rewrite: bool | None = None,
    ) -> dict:
        question = question.strip()
        if not question:
            return {
                "question": question,
                "status": "empty",
                "results": [],
                "meta": {"top_k": top_k, "result_count": 0, "error": "Empty question"},
            }
        if top_k < 1:
            raise ValueError(f"top_k must be >= 1, got {top_k}")
        top_k = min(top_k, self.MAX_TOP_K)

        try:
            rewrite = QUERY_REWRITE_ENABLED if rewrite is None else rewrite
            rewrite_result = self.rewriter.rewrite(question) if rewrite else None
            retrieval_query = rewrite_result.rewritten if rewrite_result else question
            results = self.index.query(retrieval_query, top_k=top_k, filters=filters)
        except Exception as e:
            return {
                "question": question,
                "status": "error",
                "results": [],
                "meta": {
                    "top_k": top_k, "result_count": 0, "error": str(e),
                    "filters": filters or {},
                },
            }

        if not results:
            return {
                "question": question,
                "status": "empty",
                "results": [],
                "meta": {
                    "top_k": top_k,
                    "result_count": 0,
                    "retrieval_query": retrieval_query,
                    "query_version": self.rewriter.version if rewrite else "raw-v1",
                    "applied_rewrite_rules": list(rewrite_result.applied_rules) if rewrite_result else [],
                    "filters": filters or {},
                },
            }

        formatted = []
        for r in results:
            text = r.content[:self.MAX_TEXT_LEN]
            if len(r.content) > self.MAX_TEXT_LEN:
                text += "..."

            formatted.append(
                {
                    "rank": r.rank,
                    "text": text,
                    "score": float(round(r.score, 4)),
                    "video_id": r.video_id,
                    "profile": r.profile,
                    "source_url": r.source_url,
                    "start": r.start,
                    "end": r.end,
                    "has_timestamp": r.has_timestamp,
                    "chunk_id": r.chunk_id,
                    "document_id": getattr(self.index.chunks.get(r.chunk_id), "document_id", ""),
                    "source_path": r.source_path,
                    "chunk_type": r.chunk_type,
                    "domain": r.domain,
                    "quality": r.quality,
                    "review_status": r.review_status,
                    "source_of_truth": r.source_of_truth,
                    "risk_level": r.risk_level,
                    "answer_policy": r.answer_policy,
                    "source_refs": list(r.source_refs or []),
                }
            )

        return {
            "question": question,
            "status": "ok",
            "results": formatted,
            "meta": {
                "top_k": top_k,
                "result_count": len(formatted),
                "retrieval_query": retrieval_query,
                "query_version": self.rewriter.version if rewrite else "raw-v1",
                "applied_rewrite_rules": list(rewrite_result.applied_rules) if rewrite_result else [],
                "query_expansions": list(rewrite_result.expansions) if rewrite_result else [],
                "filters": filters or {},
            },
        }
