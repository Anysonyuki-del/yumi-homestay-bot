from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import BusinessTaskStatus, BusinessTaskType
from homestay_bot.domain.models import Base, Job, PropertyProfile, StayOrder
from homestay_bot.integrations.hostex_client import Reservation
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


@pytest.mark.asyncio
async def test_hostex_event_and_reservation_upsert_are_idempotent() -> None:
    """重复 Webhook 只入队一次，订单更新不得新增重复订单。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        repository = SQLAlchemyOperationsRepository(session)
        first_event = await repository.record_hostex_event(
            event_key="event-1",
            event_type="reservation_updated",
            reservation_code="R-1",
            payload={"unknown_future_field": "ignored"},
        )
        second_event = await repository.record_hostex_event(
            event_key="event-1",
            event_type="reservation_updated",
            reservation_code="R-1",
            payload={"unknown_future_field": "ignored"},
        )
        confirmed = Reservation(
            reservation_code="R-1",
            stay_code="S-1",
            property_id=101,
            check_in_date=date(2026, 8, 1),
            check_out_date=date(2026, 8, 2),
            status="confirmed",
            created_at="2026-07-31T00:00:00Z",
        )
        cancelled = confirmed.model_copy(update={"status": "cancelled"})
        first_order = await repository.upsert_reservation(confirmed)
        second_order = await repository.upsert_reservation(cancelled)
        await session.commit()

        job_count = await session.scalar(
            select(func.count(Job.id)).where(Job.job_type == "hostex_event")
        )
        order_count = await session.scalar(select(func.count(StayOrder.id)))

        assert first_event is True
        assert second_event is False
        assert job_count == 1
        assert first_order.id == second_order.id
        assert second_order.status == "cancelled"
        assert order_count == 1

    await engine.dispose()
