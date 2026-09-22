"""审核知识的向量化、语义召回与向量补齐。

向量只是 `KnowledgeEntry` 的可重建派生数据：只用于「找哪些审核问答交给模型」，
不作为本店事实的证据。检索时只使用内容哈希与当前正文、当前模型都一致的向量，
正文改了、向量还没重算时，这条知识自动只走关键词路径。
"""

import asyncio
import hashlib
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from homestay_bot.domain.enums import Language
from homestay_bot.services.guest_reply_policy import redact_sensitive_guest_text

logger = logging.getLogger(__name__)

# 参与向量化的正文上限。bge-m3 支持约 8k token，这里按字符保守截断，只影响向量，
# 交给模型的证据仍是完整问答。
EMBEDDING_INPUT_MAX_CHARS = 6_000
# 客人等待回复时的查询向量化上限；超时直接退回关键词检索。
QUERY_EMBEDDING_TIMEOUT_SECONDS = 3.0
# ponytail: 语义召回的候选数与相似度下限是按校准集确定的固定常量；换模型或知识库
# 规模变化明显时，需要用校准集重新确定并冻结，再跑留出集。
SEMANTIC_TOP_K = 8
SEMANTIC_MIN_SIMILARITY = 0.5
# 每小时补齐：单次请求的条数与单轮总数上限，限制单轮外发量与耗时。
SYNC_BATCH_SIZE = 16
SYNC_MAX_PER_ROUND = 200
SYNC_LANGUAGES = (Language.ZH, Language.EN)


class EmbeddingUnavailableError(RuntimeError):
    """表示服务商返回的向量缺失或数量不符，本次不能使用。"""


class EmbeddingEntry(Protocol):
    """向量化所需的知识字段。"""

    id: int
    question_zh: str
    answer_zh: str
    question_en: str
    answer_en: str


class EmbeddingClient(Protocol):
    """把一组文本转成等长的一组向量。"""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """返回与输入顺序一致的向量。"""


@dataclass(frozen=True, slots=True)
class StoredVector:
    """数据库中的一条知识向量。"""

    entry_id: int
    language: str
    content_hash: str
    vector: list[float]


@dataclass(frozen=True, slots=True)
class PendingVector:
    """一条待写入的向量；写入前要确认它仍对应知识的当前正文。"""

    entry_id: int
    language: str
    content_hash: str
    vector: list[float]


class EmbeddingVectorStore(Protocol):
    """读写知识向量的最小接口。"""

    async def list_vectors(self, model: str) -> list[StoredVector]:
        """返回该模型的全部向量。"""

    async def save_current_vectors(self, model: str, vectors: list[PendingVector]) -> int:
        """只写入仍与知识当前正文一致的向量，返回实际写入条数。"""


class ActiveEntrySource(Protocol):
    """读取当前启用的审核知识。"""

    async def list_active(self) -> Sequence[Any]:
        """返回启用中的知识条目。"""


def embedding_text(entry: EmbeddingEntry, language: Language) -> str:
    """拼出某一语言参与向量化的正文：问题加答案。"""
    if language is Language.EN:
        text = f"{entry.question_en}\n{entry.answer_en}"
    else:
        text = f"{entry.question_zh}\n{entry.answer_zh}"
    return text.strip()[:EMBEDDING_INPUT_MAX_CHARS]


def content_hash(text: str, model: str) -> str:
    """由模型名与正文计算内容哈希；换模型或改正文都会得到不同的值。"""
    return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """计算余弦相似度；维度不同或含零向量时返回 0，视为不相关。"""
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else 0.0


class OpenAICompatibleEmbeddingClient:
    """用 OpenAI 兼容的 embeddings 接口（如硅基流动）生成向量。"""

    def __init__(self, client: Any, model: str) -> None:
        """注入已配置地址与 key 的 SDK 客户端和模型名。"""
        self._client = client
        self._model = model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """按输入顺序返回向量；数量不符或出现空向量时整批作废。"""
        response = await self._client.embeddings.create(model=self._model, input=texts)
        items = sorted(response.data, key=lambda item: item.index)
        vectors = [[float(value) for value in item.embedding] for item in items]
        if len(vectors) != len(texts) or any(not vector for vector in vectors):
            raise EmbeddingUnavailableError("向量数量或内容无效")
        return vectors


class SemanticRanker:
    """按查询与知识向量的相似度给启用知识排序，供关键词检索融合。"""

    def __init__(
        self,
        embedder: EmbeddingClient,
        store: EmbeddingVectorStore,
        model: str,
        *,
        top_k: int = SEMANTIC_TOP_K,
        min_similarity: float = SEMANTIC_MIN_SIMILARITY,
        timeout_seconds: float = QUERY_EMBEDDING_TIMEOUT_SECONDS,
    ) -> None:
        """保存向量化客户端、向量存储与召回常量。"""
        self._embedder = embedder
        self._store = store
        self._model = model
        self._top_k = top_k
        self._min_similarity = min_similarity
        self._timeout_seconds = timeout_seconds

    async def rank(
        self,
        language: Language,
        query: str,
        entries: Sequence[EmbeddingEntry],
    ) -> list[int]:
        """返回相似度达到下限的知识编号，按相似度从高到低，至多 top_k 条。

        没有任何可用向量时不向服务商发送查询。查询先脱敏再发出；超时、服务商
        异常都向上抛出，由调用方退回关键词检索。
        """
        stored = {
            vector.entry_id: vector
            for vector in await self._store.list_vectors(self._model)
            if vector.language == language.value
        }
        usable: list[tuple[int, list[float]]] = []
        for entry in entries:
            vector = stored.get(entry.id)
            expected = content_hash(embedding_text(entry, language), self._model)
            if vector is not None and vector.content_hash == expected:
                usable.append((entry.id, vector.vector))
        if not usable:
            return []
        safe_query = redact_sensitive_guest_text(query).strip()[:EMBEDDING_INPUT_MAX_CHARS]
        if not safe_query:
            return []
        query_vectors = await asyncio.wait_for(
            self._embedder.embed([safe_query]),
            timeout=self._timeout_seconds,
        )
        scored = sorted(
            (
                (cosine_similarity(query_vectors[0], vector), entry_id)
                for entry_id, vector in usable
            ),
            reverse=True,
        )
        return [
            entry_id
            for similarity, entry_id in scored
            if similarity >= self._min_similarity
        ][: self._top_k]


@dataclass(frozen=True, slots=True)
class EmbeddingSyncReport:
    """一轮补齐的计数，只含数量，不含正文。"""

    pending: int
    embedded: int
    saved: int


class KnowledgeEmbeddingSync:
    """为启用知识补齐缺失或过期的向量。"""

    def __init__(
        self,
        entries: ActiveEntrySource,
        store: EmbeddingVectorStore,
        embedder: EmbeddingClient,
        model: str,
    ) -> None:
        """注入知识来源、向量存储、向量化客户端与模型名。"""
        self._entries = entries
        self._store = store
        self._embedder = embedder
        self._model = model

    async def sync_once(self, *, limit: int = SYNC_MAX_PER_ROUND) -> EmbeddingSyncReport:
        """补齐一轮：找出缺失或内容哈希不符的向量，分批向量化后写入。

        读取与写入各用自己的短会话，向服务商请求时不持有数据库事务。写入时由
        存储层重新核对知识当前正文，向量化期间知识被改过的结果直接丢弃，下一轮
        再按新正文重算。停用知识不补；已有向量保留，重新启用且内容一致时直接复用。
        """
        existing = {
            (vector.entry_id, vector.language): vector.content_hash
            for vector in await self._store.list_vectors(self._model)
        }
        pending: list[tuple[int, str, str, str]] = []
        for entry in await self._entries.list_active():
            for language in SYNC_LANGUAGES:
                text = embedding_text(entry, language)
                if not text:
                    continue
                expected = content_hash(text, self._model)
                if existing.get((entry.id, language.value)) != expected:
                    pending.append((entry.id, language.value, text, expected))
        pending = pending[: max(0, limit)]
        embedded = 0
        saved = 0
        for start in range(0, len(pending), SYNC_BATCH_SIZE):
            batch = pending[start : start + SYNC_BATCH_SIZE]
            vectors = await self._embedder.embed([text for _, _, text, _ in batch])
            embedded += len(vectors)
            saved += await self._store.save_current_vectors(
                self._model,
                [
                    PendingVector(entry_id, language, expected, vector)
                    for (entry_id, language, _, expected), vector in zip(
                        batch, vectors, strict=True
                    )
                ],
            )
        return EmbeddingSyncReport(pending=len(pending), embedded=embedded, saved=saved)
