"""语义检索：排序、融合、向量补齐与晚到保护。"""

import asyncio
import logging
from dataclasses import dataclass, field

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.application import SessionKnowledgeRepository, SessionKnowledgeVectorStore
from homestay_bot.domain.enums import Language
from homestay_bot.domain.models import Base, KnowledgeEmbedding, KnowledgeEntry
from homestay_bot.services.knowledge_embeddings import (
    KnowledgeEmbeddingSync,
    PendingVector,
    SemanticRanker,
    StoredVector,
    content_hash,
    cosine_similarity,
    embedding_text,
)
from homestay_bot.services.knowledge_service import KnowledgeService

MODEL = "BAAI/bge-m3"


@dataclass
class Entry:
    """语义检索所需的最小知识字段。"""

    id: int
    question_zh: str
    answer_zh: str
    question_en: str = "Q?"
    answer_en: str = "A."
    category: str = "测试"
    keywords: list[str] = field(default_factory=list)


class FakeEmbedder:
    """按文本里的关键字给出固定方向的向量，记录每次收到的文本。"""

    def __init__(self, *, delay: float = 0.0, error: Exception | None = None) -> None:
        """可模拟延迟或异常。"""
        self.calls: list[list[str]] = []
        self.delay = delay
        self.error = error

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """「车」相关文本指向同一方向，其余文本指向正交方向。"""
        self.calls.append(list(texts))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return [[1.0, 0.0] if "车" in text else [0.0, 1.0] for text in texts]


class MemoryStore:
    """内存向量存储，写入不做正文核对（核对由数据库仓储负责，另有集成测试）。"""

    def __init__(self, vectors: list[StoredVector] | None = None) -> None:
        """保存初始向量。"""
        self.vectors = list(vectors or [])

    async def list_vectors(self, model: str) -> list[StoredVector]:
        """返回全部向量。"""
        return list(self.vectors)

    async def save_current_vectors(self, model: str, vectors: list[PendingVector]) -> int:
        """直接写入。"""
        self.vectors.extend(
            StoredVector(item.entry_id, item.language, item.content_hash, item.vector)
            for item in vectors
        )
        return len(vectors)


def stored_for(entry: Entry, vector: list[float], *, stale: bool = False) -> StoredVector:
    """为知识的中文正文造一条向量；stale 时哈希对不上当前正文。"""
    text = embedding_text(entry, Language.ZH)
    digest = content_hash(text + ("旧" if stale else ""), MODEL)
    return StoredVector(entry.id, "zh", digest, vector)


def test_cosine_similarity_handles_mismatched_and_zero_vectors() -> None:
    """维度不同或零向量视为不相关。"""
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine_similarity([1.0], [1.0, 0.0]) == 0.0
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


@pytest.mark.asyncio
async def test_ranker_uses_only_vectors_matching_the_current_text() -> None:
    """正文改过而向量没重算时，这条知识不参与语义召回。"""
    fresh = Entry(1, "门口能停车吗？", "门口有车位。")
    stale = Entry(2, "车位怎么收费？", "每天 40 元。")
    store = MemoryStore([stored_for(fresh, [1.0, 0.0]), stored_for(stale, [1.0, 0.0], stale=True)])
    ranker = SemanticRanker(FakeEmbedder(), store, MODEL)

    assert await ranker.rank(Language.ZH, "开车过去放哪", [fresh, stale]) == [1]


@pytest.mark.asyncio
async def test_ranker_sends_nothing_without_usable_vectors() -> None:
    """没有可用向量时不向服务商发送客人问题。"""
    embedder = FakeEmbedder()
    ranker = SemanticRanker(embedder, MemoryStore(), MODEL)

    assert await ranker.rank(Language.ZH, "能停车吗", [Entry(1, "停车？", "可以。")]) == []
    assert embedder.calls == []


@pytest.mark.asyncio
async def test_ranker_redacts_the_guest_question_before_sending() -> None:
    """客人问题先脱敏再向量化：手机号不出现在发给服务商的文本里。"""
    entry = Entry(1, "门口能停车吗？", "门口有车位。")
    embedder = FakeEmbedder()
    ranker = SemanticRanker(embedder, MemoryStore([stored_for(entry, [1.0, 0.0])]), MODEL)

    await ranker.rank(Language.ZH, "我手机13812345678，车停哪", [entry])

    sent = embedder.calls[0][0]
    assert "13812345678" not in sent
    assert "车停哪" in sent


@pytest.mark.asyncio
async def test_ranker_applies_similarity_floor_and_order() -> None:
    """低于相似度下限的不返回；按相似度从高到低。"""
    parking = Entry(1, "停车？", "门口车位。")
    wifi = Entry(2, "网络？", "有 Wi-Fi。")
    store = MemoryStore([stored_for(parking, [1.0, 0.0]), stored_for(wifi, [0.0, 1.0])])
    ranker = SemanticRanker(FakeEmbedder(), store, MODEL, min_similarity=0.5)

    assert await ranker.rank(Language.ZH, "车放哪", [parking, wifi]) == [1]


@pytest.mark.asyncio
async def test_ranker_times_out_instead_of_holding_the_reply() -> None:
    """查询向量化超时直接抛出，由检索退回关键词。"""
    entry = Entry(1, "停车？", "门口车位。")
    ranker = SemanticRanker(
        FakeEmbedder(delay=1.0),
        MemoryStore([stored_for(entry, [1.0, 0.0])]),
        MODEL,
        timeout_seconds=0.01,
    )

    with pytest.raises(asyncio.TimeoutError):
        await ranker.rank(Language.ZH, "车放哪", [entry])


class RepositoryStub:
    """返回固定知识。"""

    def __init__(self, entries: list[Entry]) -> None:
        """保存知识。"""
        self.entries = entries

    async def list_active(self) -> list[Entry]:
        """返回全部知识。"""
        return self.entries


class RankerStub:
    """返回固定语义排序或抛出异常。"""

    def __init__(self, ids: list[int] | None = None, error: Exception | None = None) -> None:
        """保存结果。"""
        self.ids = ids or []
        self.error = error

    async def rank(self, language, query, entries) -> list[int]:
        """返回固定排序。"""
        if self.error is not None:
            raise self.error
        return self.ids


@pytest.mark.asyncio
async def test_semantic_recall_adds_entries_keywords_cannot_find() -> None:
    """词面完全不重合的知识也能经语义召回进入证据；两路都命中的排在前面。"""
    parking = Entry(1, "门口能停车吗？", "门口有 2 个车位。")
    luggage = Entry(2, "可以寄存行李吗？", "可以免费寄存。")
    wifi = Entry(3, "房间有 Wi-Fi 吗？", "有。")
    service = KnowledgeService(RepositoryStub([parking, luggage, wifi]))

    keyword_only = await service.retrieve(Language.ZH, "箱子能先放你们那吗")
    fused = await service.with_semantic(RankerStub([3, 2])).retrieve(
        Language.ZH, "箱子能先放你们那吗"
    )

    assert [item.source_id for item in keyword_only] == [2]
    assert [item.source_id for item in fused] == [2, 3]


@pytest.mark.asyncio
async def test_semantic_failure_falls_back_to_keywords_and_logs_type_only(caplog) -> None:
    """语义检索出错时退回关键词结果，日志只有异常类型。"""
    entry = Entry(1, "可以寄存行李吗？", "可以免费寄存。")
    service = KnowledgeService(RepositoryStub([entry])).with_semantic(
        RankerStub(error=RuntimeError("secret body 13812345678"))
    )

    with caplog.at_level(logging.WARNING, logger="homestay_bot.services.knowledge_service"):
        result = await service.retrieve(Language.ZH, "箱子能先放你们那吗")

    assert [item.source_id for item in result] == [1]
    assert "error_type=RuntimeError" in caplog.text
    assert "13812345678" not in caplog.text


async def _database():
    """创建内存库与会话工厂。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _knowledge(entry_id: int, answer: str, *, enabled: bool = True) -> KnowledgeEntry:
    """构造一条中英文齐全的知识。"""
    return KnowledgeEntry(
        id=entry_id,
        category="测试",
        question_zh=f"问题{entry_id}？",
        answer_zh=answer,
        question_en=f"Question {entry_id}?",
        answer_en=f"Answer {entry_id}.",
        keywords=[],
        is_enabled=enabled,
    )


def _sync(factory, embedder) -> KnowledgeEmbeddingSync:
    """用生产中的短会话仓储装配补齐器。"""
    return KnowledgeEmbeddingSync(
        SessionKnowledgeRepository(factory),
        SessionKnowledgeVectorStore(factory),
        embedder,
        MODEL,
    )


@pytest.mark.asyncio
async def test_sync_fills_missing_vectors_once_and_skips_disabled_entries() -> None:
    """首轮为启用知识的中英文各补一条；再跑一轮不重复调用；停用知识不补。"""
    engine, factory = await _database()
    async with factory() as session:
        session.add_all([_knowledge(1, "门口有车位。"), _knowledge(2, "不提供。", enabled=False)])
        await session.commit()
    embedder = FakeEmbedder()

    first = await _sync(factory, embedder).sync_once()
    second = await _sync(factory, embedder).sync_once()

    assert (first.pending, first.saved) == (2, 2)
    assert second.pending == 0
    assert len(embedder.calls) == 1
    async with factory() as session:
        rows = list(await session.scalars(select(KnowledgeEmbedding)))
    assert {(row.entry_id, row.language) for row in rows} == {(1, "zh"), (1, "en")}
    await engine.dispose()


@pytest.mark.asyncio
async def test_sync_recomputes_only_the_changed_language_after_an_edit() -> None:
    """改了中文答案只重算中文向量，英文向量保持不动。"""
    engine, factory = await _database()
    async with factory() as session:
        session.add(_knowledge(1, "门口有车位。"))
        await session.commit()
    embedder = FakeEmbedder()
    await _sync(factory, embedder).sync_once()
    async with factory() as session:
        entry = await session.get(KnowledgeEntry, 1)
        assert entry is not None
        entry.answer_zh = "门口车位已取消。"
        await session.commit()

    report = await _sync(factory, embedder).sync_once()

    assert (report.pending, report.saved) == (1, 1)
    assert embedder.calls[-1] == ["问题1？\n门口车位已取消。"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_late_vector_for_an_edited_entry_is_discarded() -> None:
    """向量化期间管理员改了正文：算出的旧向量不落库，下一轮按新正文重算。"""
    engine, factory = await _database()
    async with factory() as session:
        session.add(_knowledge(1, "门口有车位。"))
        await session.commit()

    class EditingEmbedder(FakeEmbedder):
        """第一次调用时模拟管理员并发修改正文。"""

        async def embed(self, texts: list[str]) -> list[list[float]]:
            """先改正文并提交，再返回按旧正文算出的向量。"""
            if not self.calls:
                async with factory() as session:
                    entry = await session.get(KnowledgeEntry, 1)
                    assert entry is not None
                    entry.answer_zh = "门口车位已取消。"
                    entry.answer_en = "Parking is closed."
                    await session.commit()
            return await super().embed(texts)

    embedder = EditingEmbedder()
    first = await _sync(factory, embedder).sync_once()
    async with factory() as session:
        after_first = await session.scalar(select(func.count()).select_from(KnowledgeEmbedding))
    second = await _sync(factory, embedder).sync_once()

    assert (first.pending, first.saved) == (2, 0)
    assert after_first == 0
    assert (second.pending, second.saved) == (2, 2)
    await engine.dispose()


@pytest.mark.asyncio
async def test_reenabled_entry_reuses_its_vectors_without_new_calls() -> None:
    """停用后重新启用、正文未变时直接复用已有向量，不再外发。"""
    engine, factory = await _database()
    async with factory() as session:
        session.add(_knowledge(1, "门口有车位。"))
        await session.commit()
    embedder = FakeEmbedder()
    await _sync(factory, embedder).sync_once()
    for enabled in (False, True):
        async with factory() as session:
            entry = await session.get(KnowledgeEntry, 1)
            assert entry is not None
            entry.is_enabled = enabled
            await session.commit()

    report = await _sync(factory, embedder).sync_once()

    assert report.pending == 0
    assert len(embedder.calls) == 1
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'defect', ['duplicate', 'offset', 'dimensions', 'nan', 'inf', 'zero', 'model_dimensions'],
)
async def test_embedding_response_rejects_invalid_batch(defect: str) -> None:
    """外部响应的索引、维度和数值必须有效，不能将错配向量存入正式索引。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from homestay_bot.services.knowledge_embeddings import (
        EmbeddingUnavailableError,
        OpenAICompatibleEmbeddingClient,
    )

    rows = [SimpleNamespace(index=0, embedding=[1.0, 0.0]),
            SimpleNamespace(index=1, embedding=[0.0, 1.0])]
    model = 'test-model'
    if defect == 'duplicate':
        rows[1].index = 0
    elif defect == 'offset':
        rows[1].index = 2
    elif defect == 'dimensions':
        rows[1].embedding = [1.0]
    elif defect in ('nan', 'inf'):
        rows[1].embedding = [float(defect), 1.0]
    elif defect == 'zero':
        rows[1].embedding = [0.0, 0.0]
    else:
        model = MODEL
    sdk = SimpleNamespace(embeddings=SimpleNamespace(create=AsyncMock(
        return_value=SimpleNamespace(data=rows))))
    with pytest.raises(EmbeddingUnavailableError):
        await OpenAICompatibleEmbeddingClient(sdk, model).embed(['a', 'b'])


@pytest.mark.asyncio
async def test_embedding_response_restores_valid_order() -> None:
    """服务商允许乱序返回，完整有效的索引按输入顺序恢复。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from homestay_bot.services.knowledge_embeddings import OpenAICompatibleEmbeddingClient

    rows = [SimpleNamespace(index=1, embedding=[0.0, 1.0]),
            SimpleNamespace(index=0, embedding=[1.0, 0.0])]
    sdk = SimpleNamespace(embeddings=SimpleNamespace(create=AsyncMock(
        return_value=SimpleNamespace(data=rows))))
    assert await OpenAICompatibleEmbeddingClient(sdk, 'test-model').embed(['a', 'b']) == [
        [1.0, 0.0], [0.0, 1.0]]
