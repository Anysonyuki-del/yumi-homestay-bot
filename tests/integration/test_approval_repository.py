from datetime import date

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import ApprovalStatus, EmployeeRole
from homestay_bot.domain.models import Base, BookingApproval, Conversation, Employee
from homestay_bot.repositories.approvals import (
    SQLAlchemyApprovalRepository,
    SQLAlchemyPermissionChecker,
)


@pytest.mark.asyncio
async def test_repository_adds_and_locks_approval() -> None:
    """真实 ORM 仓储应分配主键并能在事务中锁定读取审批单。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        conversation = Conversation(open_kfid="wk-1", external_userid="wm-1")
        session.add(conversation)
        await session.flush()
        repository = SQLAlchemyApprovalRepository(session)
        approval = BookingApproval(
            approval_code="APP-DB",
            conversation_id=conversation.id,
            status=ApprovalStatus.PENDING,
            check_in_date=date(2026, 8, 1),
            check_out_date=date(2026, 8, 2),
            number_of_guests=2,
            guest_name="张三",
            guest_mobile="13800138000",
            room_type_preference="江景房",
        )
        saved = await repository.add(approval)
        await session.commit()

    async with factory() as session:
        repository = SQLAlchemyApprovalRepository(session)
        async with repository.transaction():
            locked = await repository.get_for_update(saved.id)

        assert locked.approval_code == "APP-DB"

    await engine.dispose()


@pytest.mark.asyncio
async def test_permission_checker_rejects_regular_customer_service() -> None:
    """普通客服不能获得最终创建订单权限。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        employee = Employee(
            wecom_userid="staff-1",
            name="客服甲",
            role=EmployeeRole.CUSTOMER_SERVICE,
        )
        session.add(employee)
        await session.commit()
        checker = SQLAlchemyPermissionChecker(session)

        with pytest.raises(PermissionError):
            await checker.require_booking_approver(employee.id)

    await engine.dispose()
