import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import Language
from homestay_bot.domain.models import Base, KnowledgeCandidate, KnowledgeEntry
from homestay_bot.repositories.knowledge import SQLAlchemyKnowledgeRepository
from homestay_bot.services.knowledge_service import KnowledgeService


@pytest.mark.asyncio
async def test_repository_returns_only_enabled_knowledge() -> None:
    """知识仓储只能返回已启用条目，避免停用内容进入模型上下文。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        session.add_all(
            [
                KnowledgeEntry(
                    category="入住",
                    question_zh="几点入住？",
                    answer_zh="下午三点后。",
                    question_en="Check-in time?",
                    answer_en="After 3 PM.",
                    is_enabled=True,
                ),
                KnowledgeEntry(
                    category="旧政策",
                    question_zh="旧规则？",
                    answer_zh="不得使用。",
                    question_en="Old rule?",
                    answer_en="Do not use.",
                    is_enabled=False,
                ),
            ]
        )
        await session.commit()

        entries = await SQLAlchemyKnowledgeRepository(session).list_active()

        assert [entry.category for entry in entries] == ["入住"]

    await engine.dispose()


@pytest.mark.asyncio
async def test_committed_knowledge_changes_reach_the_next_request() -> None:
    """修改、停用、重新启用和新增提交后，新请求按最新状态取证；候选草稿永不入证。

    每一步都用新会话检索，模拟下一位客人的请求，不依赖会话缓存。
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def evidence(question: str) -> str:
        """用新会话检索并拼出交给模型的全部证据。"""
        async with factory() as session:
            snippets = await KnowledgeService(
                SQLAlchemyKnowledgeRepository(session)
            ).retrieve(Language.ZH, question)
        return "\n".join(f"{item.question}\n{item.answer}" for item in snippets)

    async with factory() as session:
        session.add(
            KnowledgeEntry(
                id=1,
                category="早餐",
                question_zh="民宿提供早餐吗？",
                answer_zh="提供免费早餐，每天 7:00–9:00。",
                question_en="Is breakfast served?",
                answer_en="Free breakfast is served 7:00–9:00.",
                keywords=["早餐"],
            )
        )
        session.add(
            KnowledgeCandidate(
                canonical_key="bathtub",
                canonical_question="房间有浴缸吗",
                category="卫浴",
                draft_payload={
                    "question_zh": "房间有浴缸吗？",
                    "answer_zh": "部分房间有独立浴缸。",
                },
            )
        )
        await session.commit()
    assert "提供免费早餐" in await evidence("有早餐吗")

    async with factory() as session:
        entry = await session.get(KnowledgeEntry, 1)
        assert entry is not None
        entry.answer_zh = "自 9 月起暂停提供早餐。"
        await session.commit()
    updated = await evidence("有早餐吗")
    assert "暂停提供早餐" in updated
    assert "提供免费早餐" not in updated

    async with factory() as session:
        entry = await session.get(KnowledgeEntry, 1)
        assert entry is not None
        entry.is_enabled = False
        await session.commit()
    assert await evidence("有早餐吗") == ""

    async with factory() as session:
        entry = await session.get(KnowledgeEntry, 1)
        assert entry is not None
        entry.is_enabled = True
        session.add(
            KnowledgeEntry(
                id=2,
                category="网络",
                question_zh="房间有 Wi-Fi 吗？",
                answer_zh="每间房都有独立 Wi-Fi，密码贴在房门背面。",
                question_en="Is there Wi-Fi?",
                answer_en="Every room has its own Wi-Fi.",
                keywords=["wifi"],
            )
        )
        await session.commit()
    assert "暂停提供早餐" in await evidence("有早餐吗")
    assert "密码贴在房门背面" in await evidence("房间 wifi 密码在哪")
    assert "独立浴缸" not in await evidence("房间有浴缸吗")

    await engine.dispose()
