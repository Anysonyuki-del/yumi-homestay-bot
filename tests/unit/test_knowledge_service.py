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
