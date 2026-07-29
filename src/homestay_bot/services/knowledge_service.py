from dataclasses import dataclass
from typing import Protocol

from homestay_bot.domain.enums import Language


class KnowledgeRecord(Protocol):
    """定义知识服务读取的最小条目字段。"""

    id: int
    category: str
    question_zh: str
    answer_zh: str
    question_en: str
    answer_en: str


class ActiveKnowledgeRepository(Protocol):
    """定义只读取已启用知识的仓储接口。"""

    async def list_active(self) -> list[KnowledgeRecord]:
        """返回当前全部已启用且已审核的知识。"""


@dataclass(frozen=True)
class KnowledgeSnippet:
    """表示交给模型的一条最小化知识。"""

    source_id: int
    category: str
    question: str
    answer: str


class KnowledgeService:
    """把已审核知识转换为指定语言的模型上下文。"""

    def __init__(self, repository: ActiveKnowledgeRepository) -> None:
        """注入只读知识仓储。"""
        self._repository = repository

    async def build_context(self, language: Language) -> list[KnowledgeSnippet]:
        """返回数量受限的有效知识，避免把停用内容交给模型。"""
        entries = await self._repository.list_active()
        snippets: list[KnowledgeSnippet] = []
        for entry in entries[:100]:
            question = (
                entry.question_en if language is Language.EN else entry.question_zh
            )
            answer = entry.answer_en if language is Language.EN else entry.answer_zh
            snippets.append(
                KnowledgeSnippet(
                    source_id=entry.id,
                    category=entry.category,
                    question=question,
                    answer=answer,
                )
            )
        return snippets

