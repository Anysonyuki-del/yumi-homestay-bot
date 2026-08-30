from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol

from homestay_bot.services.model_budget import MODEL_BUDGET, serialized_chars


class FaqCandidateRecord(Protocol):
    """定义模型候选上下文所需的最小记录字段。"""

    id: int
    canonical_question: str


class FaqCandidateRepository(Protocol):
    """定义候选上下文服务所需的只读仓储接口。"""

    async def list_context(
        self, *, now: datetime
    ) -> Sequence[FaqCandidateRecord]:
        """返回当前可参与语义匹配的候选。"""


class FaqCandidateContextService:
    """把候选记录收敛为不含身份和示例的模型上下文。"""

    def __init__(
        self,
        repository: FaqCandidateRepository,
        *,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        """注入候选仓储和可测试的 UTC 时钟。"""
        self._repository = repository
        self._now_provider = now_provider or (lambda: datetime.now(UTC))

    async def build_context(self) -> list[dict[str, int | str]]:
        """返回模型可见的最小候选上下文。"""
        candidates = await self._repository.list_context(
            now=self._now_provider()
        )
        context: list[dict[str, int | str]] = []
        for candidate in candidates[: MODEL_BUDGET.faq_candidates]:
            item: dict[str, int | str] = {
                "id": candidate.id,
                "canonical_question": candidate.canonical_question[:300],
            }
            if serialized_chars([*context, item]) > MODEL_BUDGET.faq_candidates_chars:
                break
            context.append(item)
        return context
