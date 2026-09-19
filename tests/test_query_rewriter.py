from modules.query_rewriter import QueryRewriter


def test_query_rewriter_appends_auditable_expansions_without_removing_original():
    rewriter = QueryRewriter(
        [{"id": "brain", "aliases": ["脑梗"], "expansions": ["脑卒中", "缺血性脑卒中"]}],
        version="test-v1",
    )

    result = rewriter.rewrite("脑梗 的黄金时间")

    assert result.original == "脑梗 的黄金时间"
    assert result.rewritten == "脑梗 的黄金时间 脑卒中 缺血性脑卒中"
    assert result.applied_rules == ("brain",)
    assert result.expansions == ("脑卒中", "缺血性脑卒中")


def test_query_rewriter_does_not_duplicate_existing_expansion():
    rewriter = QueryRewriter(
        [{"id": "sti", "aliases": ["性病"], "expansions": ["性传播感染", "STI"]}]
    )

    result = rewriter.rewrite("性病也叫性传播感染吗")

    assert result.rewritten == "性病也叫性传播感染吗 STI"
