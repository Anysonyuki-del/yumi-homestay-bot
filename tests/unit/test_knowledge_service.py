from dataclasses import dataclass

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

    context = await service.build_context(Language.EN)

    assert context[0].source_id == 1
    assert context[0].question == "What time is check-in?"
    assert context[0].answer == "Check-in is after 3 PM."

