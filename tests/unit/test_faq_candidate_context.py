from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from homestay_bot.services.faq_candidate_context import (
    FaqCandidateContextService,
)


class CandidateRepositoryStub:
    """记录查询时间并返回带额外敏感字段的候选记录。"""

    def __init__(self) -> None:
        """初始化候选和查询记录。"""
        self.queries: list[datetime] = []
        self.items = [
            SimpleNamespace(
                id=index,
                canonical_question=f"标准问题{index}",
                examples=[f"原始问法{index}"],
                external_userid=f"wm-{index}",
            )
            for index in range(1, 56)
        ]

    async def list_context(self, *, now: datetime):
        """保存 UTC 查询时间并返回候选。"""
        self.queries.append(now)
        return self.items


@pytest.mark.asyncio
async def test_context_keeps_only_twenty_ids_and_canonical_questions() -> None:
    """候选上下文不得包含示例、客人身份或超过二十条记录。"""
    repository = CandidateRepositoryStub()
    fixed_now = datetime(2026, 7, 30, 8, tzinfo=UTC)
    service = FaqCandidateContextService(
        repository,
        now_provider=lambda: fixed_now,
    )

    context = await service.build_context()

    assert repository.queries == [fixed_now]
    assert len(context) == 20
    assert context[0] == {"id": 1, "canonical_question": "标准问题1"}
    assert context[-1] == {"id": 20, "canonical_question": "标准问题20"}
    assert all(set(item) == {"id", "canonical_question"} for item in context)
