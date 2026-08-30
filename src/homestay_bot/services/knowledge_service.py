import re
import unicodedata
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
    keywords: list[str]


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

    @staticmethod
    def _tokens(content: str) -> set[str]:
        """提取英文词元和中文二元组，供确定性相关度评分使用。"""
        normalized = unicodedata.normalize("NFKC", content).casefold()
        tokens = set(re.findall(r"[a-z0-9]{2,}", normalized))
        for segment in re.findall(r"[\u4e00-\u9fff]+", normalized):
            if len(segment) == 1:
                tokens.add(segment)
            else:
                tokens.update(segment[index : index + 2] for index in range(len(segment) - 1))
        return tokens

    @classmethod
    def _score(cls, query_tokens: set[str], entry: KnowledgeRecord, language: Language) -> int:
        """按问题、关键词、分类和答案的证据强度计算相关度。"""
        question = entry.question_en if language is Language.EN else entry.question_zh
        answer = entry.answer_en if language is Language.EN else entry.answer_zh
        alternate_question = (
            entry.question_zh if language is Language.EN else entry.question_en
        )
        alternate_answer = entry.answer_zh if language is Language.EN else entry.answer_en
        keyword_text = " ".join(str(item) for item in entry.keywords)
        return (
            len(query_tokens & cls._tokens(question)) * 6
            + len(query_tokens & cls._tokens(alternate_question)) * 4
            + len(query_tokens & cls._tokens(keyword_text)) * 5
            + len(query_tokens & cls._tokens(entry.category)) * 4
            + len(query_tokens & cls._tokens(answer))
            + len(query_tokens & cls._tokens(alternate_answer))
        )

    async def retrieve(
        self,
        language: Language,
        query: str,
        *,
        limit: int = 8,
        char_budget: int = 12_000,
    ) -> list[KnowledgeSnippet]:
        """按当前问题返回相关且受字符预算约束的审核知识。"""
        entries = await self._repository.list_active()
        query_tokens = self._tokens(query)
        ranked = sorted(
            (
                (self._score(query_tokens, entry, language), entry)
                for entry in entries
            ),
            key=lambda item: (item[0], item[1].id),
            reverse=True,
        )
        snippets: list[KnowledgeSnippet] = []
        used_chars = 0
        for score, entry in ranked:
            if score <= 0 or len(snippets) >= max(0, limit):
                break
            question = (
                entry.question_en if language is Language.EN else entry.question_zh
            )
            answer = entry.answer_en if language is Language.EN else entry.answer_zh
            question = question[:300]
            answer = answer[:1_200]
            item_chars = len(entry.category) + len(question) + len(answer)
            if used_chars + item_chars > max(0, char_budget):
                continue
            snippets.append(
                KnowledgeSnippet(
                    source_id=entry.id,
                    category=entry.category,
                    question=question,
                    answer=answer,
                )
            )
            used_chars += item_chars
        return snippets
