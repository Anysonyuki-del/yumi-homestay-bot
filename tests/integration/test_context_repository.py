from datetime import date

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import BusinessTaskStatus, BusinessTaskType
from homestay_bot.domain.models import (
    Base,
    BusinessTask,
    Customer,
    PropertyProfile,
    StayOrder,
)
from homestay_bot.repositories.context import SQLAlchemyContextRepository


@pytest.mark.asyncio
async def test_model_context_includes_safe_active_orders_and_open_tasks() -> None:
    """模型上下文只加入订单和任务的运营摘要，不复制任务正文。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        customer = Customer(display_name="测试客户")
        property_profile = PropertyProfile(id=101, title="长江中心")
        session.add_all([customer, property_profile])
        await session.flush()
        session.add(
            StayOrder(
                hostex_reservation_code="R-CONTEXT",
                stay_code="S-CONTEXT",
                customer_id=customer.id,
                property_id=101,
                check_in_date=date(2026, 8, 1),
                check_out_date=date(2026, 8, 2),
                status="confirmed",
            )
        )
        session.add(
            BusinessTask(
                source_message_id="msg-context",
                task_type=BusinessTaskType.SUPPLIES,
                status=BusinessTaskStatus.PENDING_CONFIRMATION,
                customer_id=customer.id,
                description="补水，联系电话13800138000",
            )
        )
        await session.commit()

        context = await SQLAlchemyContextRepository(session).load_model_context(
            customer.id
        )

        assert context.active_orders == [
            {
                "property_id": 101,
                "property_title": "长江中心",
                "check_in_date": "2026-08-01",
                "check_out_date": "2026-08-02",
                "status": "confirmed",
            }
        ]
        assert context.open_tasks == [
            {
                "task_type": "supplies",
                "status": "pending_confirmation",
                "property_id": None,
                "service_date": None,
            }
        ]
        assert "13800138000" not in str(context)
        assert "补水" not in str(context)

    await engine.dispose()
