from dataclasses import dataclass, field

import pytest

from homestay_bot.domain.enums import Language
from homestay_bot.services.knowledge_service import KnowledgeService


@dataclass
class KnowledgeRow:
    """提供知识服务测试所需字段。"""

    id: int
    category: str
    question_zh: str
    answer_zh: str
    question_en: str
    answer_en: str
    keywords: list[str] = field(default_factory=list)


class KnowledgeRepositoryStub:
    """返回已由仓储过滤为启用状态的知识。"""

    async def list_active(self):
        """提供一条中英文知识。"""
        return [
            KnowledgeRow(
                id=1,
                category="入住",
                question_zh="几点入住？",
                answer_zh="下午三点后入住。",
                question_en="What time is check-in?",
                answer_en="Check-in is after 3 PM.",
            )
        ]


@pytest.mark.asyncio
async def test_build_context_selects_requested_language() -> None:
    """知识上下文必须使用客人当前会话语言。"""
    service = KnowledgeService(KnowledgeRepositoryStub())

    context = await service.retrieve(Language.EN, "What time is check-in?")

    assert context[0].source_id == 1
    assert context[0].question == "What time is check-in?"
    assert context[0].answer == "Check-in is after 3 PM."


class ManyKnowledgeRepositoryStub:
    """提供超过旧上限的知识，验证召回不依赖仓储顺序。"""

    async def list_active(self):
        """先返回大量无关条目，再返回较新的停车知识。"""
        rows = [
            KnowledgeRow(
                id=index,
                category="入住",
                question_zh=f"入住说明{index}",
                answer_zh="下午三点后入住。",
                question_en=f"Check-in note {index}",
                answer_en="Check-in is after 3 PM.",
            )
            for index in range(1, 106)
        ]
        rows.append(
            KnowledgeRow(
                id=106,
                category="停车",
                question_zh="民宿附近有停车位吗？",
                answer_zh="附近有经过审核的停车安排。",
                question_en="Is parking available nearby?",
                answer_en="Reviewed parking guidance is available.",
                keywords=["停车", "车位", "parking"],
            )
        )
        return rows


@pytest.mark.asyncio
async def test_retrieve_finds_relevant_entry_after_first_hundred() -> None:
    """相关知识即使排在第一百条之后，也必须被确定性检索命中。"""
    service = KnowledgeService(ManyKnowledgeRepositoryStub())

    context = await service.retrieve(Language.ZH, "请问有停车位吗？")

    assert [item.source_id for item in context] == [106]
    assert sum(len(item.question) + len(item.answer) for item in context) <= 12_000


@pytest.mark.asyncio
async def test_retrieve_returns_empty_when_no_entry_is_relevant() -> None:
    """没有词元交集时不得把无关知识兜底注入模型。"""
    service = KnowledgeService(ManyKnowledgeRepositoryStub())

    context = await service.retrieve(Language.ZH, "如何给自行车轮胎充气？")

    assert context == []


class RowsRepositoryStub:
    """返回构造时给定的知识行。"""

    def __init__(self, rows: list[KnowledgeRow]) -> None:
        """保存知识行。"""
        self.rows = rows

    async def list_active(self):
        """返回全部知识行。"""
        return self.rows


def _row(entry_id: int, question: str, answer: str, **fields) -> KnowledgeRow:
    """构造中英文字段齐全的知识行。"""
    return KnowledgeRow(
        id=entry_id,
        category=fields.get("category", "须知"),
        question_zh=question,
        answer_zh=answer,
        question_en=fields.get("question_en", "Note?"),
        answer_en=fields.get("answer_en", "See note."),
        keywords=fields.get("keywords", []),
    )


@pytest.mark.asyncio
async def test_retrieve_keeps_the_whole_answer_beyond_the_old_cutoff() -> None:
    """答案尾部的收费与例外不能被截掉：整条问答是最小证据单元。"""
    answer = "入住须知。" * 260 + "延迟退房每小时加收 50 元，节假日不接受延迟退房。"
    assert len(answer) > 1_300
    service = KnowledgeService(RowsRepositoryStub([_row(1, "延迟退房有什么规定？", answer)]))

    context = await service.retrieve(Language.ZH, "可以延迟退房吗")

    assert context[0].answer == answer


@pytest.mark.asyncio
async def test_retrieve_skips_units_that_do_not_fit_and_counts_them() -> None:
    """放不进预算的问答整条跳过并计数，不截断；来源数与总字符数守住上限。"""
    long_answer = "停车说明。" * 1_300
    rows = [_row(1, "停车有什么规定？", long_answer, category="停车")]
    rows += [
        _row(index, f"停车问题{index}？", "门口有临时车位。", category="停车")
        for index in range(2, 12)
    ]
    service = KnowledgeService(RowsRepositoryStub(rows))

    retrieval = await service.retrieve_detailed(
        Language.ZH,
        "停车有什么规定",
        char_budget=6_000,
    )

    assert retrieval.budget_skipped == 1
    assert all(item.answer != long_answer[:1_200] for item in retrieval.snippets)
    assert 1 not in [item.source_id for item in retrieval.snippets]
    assert len(retrieval.snippets) == 8
    assert (
        sum(len(i.category) + len(i.question) + len(i.answer) for i in retrieval.snippets)
        <= 6_000
    )


@pytest.mark.asyncio
async def test_retrieve_expands_known_topic_synonyms() -> None:
    """「泊车」「wash my clothes」能召回停车、洗衣知识，原问题不被改写。"""
    rows = [
        _row(1, "民宿可以停车吗？", "门口有 2 个临时车位。", category="停车"),
        _row(
            2,
            "有洗衣机吗？",
            "公共区域有洗衣机。",
            category="洗衣",
            question_en="Do you have a washing machine?",
            answer_en="There is a washing machine in the common area.",
        ),
        _row(3, "可以用厨房吗？", "厨房每天开放。", category="厨房",
             question_en="Can I use the kitchen?"),
    ]
    service = KnowledgeService(RowsRepositoryStub(rows))

    parking = await service.retrieve(Language.ZH, "能泊车不")
    laundry = await service.retrieve(Language.EN, "Where can I wash my clothes?")

    assert parking[0].source_id == 1
    assert laundry[0].source_id == 2


@pytest.mark.asyncio
async def test_question_filler_words_do_not_make_unrelated_entries_relevant() -> None:
    """「你们」「Do you have」这类虚词不算相关证据，无答案问题不硬配条目。"""
    rows = [
        _row(1, "你们提供早餐吗？", "不提供早餐。", category="早餐",
             question_en="Do you have breakfast?"),
        _row(2, "你们可以开发票吗？", "可以开电子发票。", category="发票",
             question_en="Do you have invoices?"),
    ]
    service = KnowledgeService(RowsRepositoryStub(rows))

    assert await service.retrieve(Language.ZH, "你们有游泳池吗") == []
    assert await service.retrieve(Language.EN, "Do you have a gym?") == []
