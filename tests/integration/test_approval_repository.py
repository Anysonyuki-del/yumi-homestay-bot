from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import ApprovalStatus, EmployeeRole
from homestay_bot.domain.models import Base, BookingApproval, Conversation, Employee
from homestay_bot.domain.schemas import ConfirmBookingCommand
from homestay_bot.integrations.hostex_client import (
    AvailabilityDay,
    PropertyAvailability,
)
from homestay_bot.repositories.approvals import (
    SQLAlchemyApprovalRepository,
    SQLAlchemyPermissionChecker,
)
from homestay_bot.services.booking_service import BookingService


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


@pytest.mark.asyncio
async def test_production_repository_and_permission_share_one_transaction() -> None:
    """真实权限查询和审批行锁必须位于同一事务，不能嵌套 begin。"""

    class UnavailableHostex:
        """返回确定的房态冲突，使测试停在任何写入之前。"""

        async def list_availabilities(self, property_ids, start_date, end_date):
            """返回目标日期不可用。"""
            return [
                PropertyAvailability(
                    property_id=property_ids[0],
                    days=[
                        AvailabilityDay(date=start_date, available=False)
                    ],
                )
            ]

        async def create_reservation(self, request):
            """房态冲突时绝不应执行。"""
            raise AssertionError("不应创建订单")

        async def list_reservations(self, query):
            """房态冲突时无需核验订单。"""
            return []

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        employee = Employee(
            wecom_userid="admin-1",
            name="管理员",
            role=EmployeeRole.ADMIN,
        )
        conversation = Conversation(open_kfid="wk-1", external_userid="wm-1")
        session.add_all([employee, conversation])
        await session.flush()
        approval = BookingApproval(
            approval_code="APP-TXN",
            conversation_id=conversation.id,
            status=ApprovalStatus.PENDING,
            check_in_date=date(2026, 8, 1),
            check_out_date=date(2026, 8, 2),
            number_of_guests=2,
            guest_name="张三",
            guest_mobile="13800138000",
            room_type_preference="江景房",
        )
        session.add(approval)
        await session.commit()

    async with factory() as session:
        service = BookingService(
            SQLAlchemyApprovalRepository(session),
            SQLAlchemyPermissionChecker(session),
            UnavailableHostex(),
        )

        result = await service.confirm_and_create(
            approval.id,
            employee.id,
            ConfirmBookingCommand(
                property_id=101,
                final_rate_amount=399,
                received_amount=399,
                income_method_id=1,
                payment_confirmed=True,
            ),
        )

        assert result.status is ApprovalStatus.CONFLICT

    await engine.dispose()


@pytest.mark.asyncio
async def test_stale_creating_approval_moves_to_manual_review() -> None:
    """进程中断遗留的 CREATING 审批应自动退出不确定状态。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)

    async with factory() as session:
        conversation = Conversation(open_kfid="wk-1", external_userid="wm-1")
        session.add(conversation)
        await session.flush()
        approval = BookingApproval(
            approval_code="APP-STALE",
            conversation_id=conversation.id,
            status=ApprovalStatus.CREATING,
            check_in_date=date(2026, 8, 1),
            check_out_date=date(2026, 8, 2),
            number_of_guests=2,
            guest_name="张三",
            guest_mobile="13800138000",
            room_type_preference="江景房",
            approved_at=now - timedelta(minutes=10),
        )
        session.add(approval)
        await session.commit()
        repository = SQLAlchemyApprovalRepository(session)

        recovered = await repository.recover_stale_creating(
            before=now - timedelta(minutes=5)
        )
        await session.refresh(approval)

        assert recovered == 1
        assert approval.status is ApprovalStatus.NEEDS_REVIEW
        assert approval.failure_message == "创建进程中断，需人工核验百居易后台"

    await engine.dispose()
