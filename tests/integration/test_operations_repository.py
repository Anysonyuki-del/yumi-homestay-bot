from datetime import date

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import BusinessTaskStatus, BusinessTaskType
from homestay_bot.domain.models import Base, PropertyProfile
from homestay_bot.repositories.operations import SQLAlchemyOperationsRepository


@pytest.mark.asyncio
async def test_turnover_task_dedupe_key_is_unique() -> None:
    """同一房间同一服务日只能生成一个周转保洁任务。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        session.add(PropertyProfile(id=101, title="测试房间"))
        await session.flush()
        repository = SQLAlchemyOperationsRepository(session)

        first = await repository.create_turnover(
            property_id=101,
            service_date=date(2026, 8, 1),
        )
        second = await repository.create_turnover(
            property_id=101,
            service_date=date(2026, 8, 1),
        )
        await session.commit()

        assert first.id == second.id
        assert first.task_type is BusinessTaskType.CLEANING
        assert first.status is BusinessTaskStatus.PENDING_ASSIGNMENT

    await engine.dispose()
