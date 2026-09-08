"""管理员手动创建任务：来源标记、状态、约束与拒绝路径。"""

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import (
    BusinessTaskOrigin,
    BusinessTaskStatus,
    BusinessTaskType,
    EmployeeRole,
)
from homestay_bot.domain.errors import OperationRefused
from homestay_bot.domain.models import AuditLog, Base, BusinessTask, Employee, PropertyProfile
from homestay_bot.repositories.operations import SQLAlchemyOperationsRepository


async def _factory():
    """建内存库并返回会话工厂。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed(session) -> None:
    """写入一个启用房间和一个启用管理员。"""
    session.add(PropertyProfile(id=101, title="测试房间", is_active=True))
    session.add(PropertyProfile(id=102, title="停用房间", is_active=False))
    session.add(
        Employee(id=1, wecom_userid="admin", name="管理员", role=EmployeeRole.ADMIN, is_active=True)
    )
    session.add(
        Employee(id=2, wecom_userid="staff", name="保洁", role=EmployeeRole.STAFF, is_active=True)
    )
    await session.flush()


@pytest.mark.asyncio
async def test_manual_task_defaults_to_pending_assignment_with_manual_origin() -> None:
    """不带执行人时创建为待分派，来源标记为手动，且满足执行字段约束。"""
    factory = await _factory()
    async with factory() as session:
        await _seed(session)
        repo = SQLAlchemyOperationsRepository(session)

        task = await repo.create_manual_task(
            task_type=BusinessTaskType.MAINTENANCE,
            property_id=101,
            service_date=date(2026, 9, 20),
            description="  热水器维修  ",
            actor_employee_id=1,
        )
        await session.flush()

        assert task.status is BusinessTaskStatus.PENDING_ASSIGNMENT
        assert task.origin_kind is BusinessTaskOrigin.MANUAL
        assert task.dedupe_key is None
        assert task.description == "热水器维修"  # 去除首尾空白
        assert task.assigned_employee_id is None
        # 真的落库（若违反 CHECK 约束这里会抛）。
        persisted = await session.scalar(select(BusinessTask).where(BusinessTask.id == task.id))
        assert persisted is not None
        audit = await session.scalar(
            select(AuditLog).where(AuditLog.action == "business_task_manual_created")
        )
        assert audit is not None and audit.actor_employee_id == 1


@pytest.mark.asyncio
async def test_manual_task_with_assignee_is_born_assigned() -> None:
    """指定启用执行人时直接进入已分派。"""
    factory = await _factory()
    async with factory() as session:
        await _seed(session)
        repo = SQLAlchemyOperationsRepository(session)

        task = await repo.create_manual_task(
            task_type=BusinessTaskType.CLEANING,
            property_id=101,
            service_date=date(2026, 9, 20),
            description="加急保洁",
            actor_employee_id=1,
            assigned_employee_id=2,
        )
        await session.flush()

        assert task.status is BusinessTaskStatus.ASSIGNED
        assert task.assigned_employee_id == 2


@pytest.mark.asyncio
async def test_manual_contact_type_is_refused() -> None:
    """manual_contact 是生命周期派生类型，不允许手动创建。"""
    factory = await _factory()
    async with factory() as session:
        await _seed(session)
        repo = SQLAlchemyOperationsRepository(session)
        with pytest.raises(OperationRefused, match="不支持手动创建"):
            await repo.create_manual_task(
                task_type=BusinessTaskType.MANUAL_CONTACT,
                property_id=101,
                service_date=date(2026, 9, 20),
                description="x",
                actor_employee_id=1,
            )


@pytest.mark.asyncio
async def test_manual_task_rejects_blank_description_and_bad_property() -> None:
    """空描述、停用/不存在房间、停用执行人都在服务端被拒。"""
    factory = await _factory()
    async with factory() as session:
        await _seed(session)
        repo = SQLAlchemyOperationsRepository(session)

        with pytest.raises(OperationRefused, match="任务说明"):
            await repo.create_manual_task(
                task_type=BusinessTaskType.SUPPLIES,
                property_id=101,
                service_date=date(2026, 9, 20),
                description="   ",
                actor_employee_id=1,
            )
        with pytest.raises(OperationRefused, match="房间"):
            await repo.create_manual_task(
                task_type=BusinessTaskType.SUPPLIES,
                property_id=102,  # 停用
                service_date=date(2026, 9, 20),
                description="补毛巾",
                actor_employee_id=1,
            )
        with pytest.raises(OperationRefused, match="房间"):
            await repo.create_manual_task(
                task_type=BusinessTaskType.SUPPLIES,
                property_id=999,  # 不存在
                service_date=date(2026, 9, 20),
                description="补毛巾",
                actor_employee_id=1,
            )
        with pytest.raises(OperationRefused, match="执行"):
            await repo.create_manual_task(
                task_type=BusinessTaskType.SUPPLIES,
                property_id=101,
                service_date=date(2026, 9, 20),
                description="补毛巾",
                actor_employee_id=1,
                assigned_employee_id=999,
            )
