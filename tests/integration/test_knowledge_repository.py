import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.models import Base, KnowledgeEntry
from homestay_bot.repositories.knowledge import SQLAlchemyKnowledgeRepository


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
