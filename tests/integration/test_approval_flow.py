from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta

import pytest

from homestay_bot.domain.enums import ApprovalStatus
from homestay_bot.domain.models import BookingApproval
from homestay_bot.domain.schemas import BookingRequest, ConfirmBookingCommand
from homestay_bot.integrations.hostex_client import (
    AvailabilityDay,
    CreateReservationResult,
    HostexBusinessError,
    HostexTransportError,
    PropertyAvailability,
    Reservation,
)
from homestay_bot.services.approval_service import ApprovalService
from homestay_bot.services.booking_service import BookingService


class InMemoryApprovalRepository:
    """用内存对象验证审批状态机，不伪造服务内部行为。"""

    def __init__(self, approval: BookingApproval) -> None:
        self.approval = approval

    @asynccontextmanager
    async def transaction(self):
        """提供与生产仓储一致的事务边界。"""
        yield

    async def get_for_update(self, approval_id: int) -> BookingApproval:
        """返回唯一审批单，模拟数据库行锁读取。"""
        assert approval_id == self.approval.id
        return self.approval

    async def save(self, approval: BookingApproval) -> None:
        """保存测试对象的最新状态。"""
        self.approval = approval


class AllowApprover:
    """允许指定测试员工执行下单。"""

    async def require_booking_approver(self, employee_id: int) -> None:
        """验证测试中的授权员工编号。"""
        assert employee_id == 1


class HostexStub:
    """提供可计数的百居易行为，用于验证写入次数。"""

    def __init__(
        self,
        *,
        available: bool = True,
        ambiguous: bool = False,
        business_error: bool = False,
        reservation_created_at: str | None = None,
    ) -> None:
        self.available = available
        self.ambiguous = ambiguous
        self.business_error = business_error
        self.reservation_created_at = (
            reservation_created_at or datetime.now(UTC).isoformat()
        )
        self.create_calls = 0

    async def list_availabilities(
        self,
        property_ids: list[int],
        start_date: date,
        end_date: date,
    ) -> list[PropertyAvailability]:
        """返回目标日期内单间房的确定房态。"""
        assert property_ids == [101]
        return [
            PropertyAvailability(
                property_id=101,
                days=[AvailabilityDay(date=start_date, available=self.available, remarks="")],
            )
        ]

    async def create_reservation(self, request) -> CreateReservationResult:
        """记录真实写调用次数，并可模拟结果不明确。"""
        self.create_calls += 1
        if self.business_error:
            raise HostexBusinessError(422, "RT-FAIL", "invalid request")
        if self.ambiguous:
            raise HostexTransportError("timeout")
        return CreateReservationResult(request_id="RT-CREATE")

    async def list_reservations(self, query) -> list[Reservation]:
        """返回创建后唯一匹配的直订订单。"""
        if self.ambiguous:
            return []
        return [
            Reservation(
                reservation_code="R-100",
                stay_code="S-100",
                property_id=101,
                check_in_date=date(2026, 8, 1),
                check_out_date=date(2026, 8, 2),
                status="accepted",
                guest_name="张三",
                guest_phone="13800138000",
                created_at=self.reservation_created_at,
                rates={"rate_amount": 399},
            )
        ]


class CaptureApprovalRepository:
    """保存新建审批单，验证资料映射和初始状态。"""

    def __init__(self) -> None:
        self.saved: BookingApproval | None = None

    async def add(self, approval: BookingApproval) -> BookingApproval:
        """模拟数据库分配主键并返回审批单。"""
        approval.id = 1
        self.saved = approval
        return approval


def pending_approval() -> BookingApproval:
    """创建具备完整客人资料的待审批对象。"""
    approval = BookingApproval(
        id=1,
        approval_code="APP-001",
        conversation_id=1,
        status=ApprovalStatus.PENDING,
        check_in_date=date(2026, 8, 1),
        check_out_date=date(2026, 8, 2),
        number_of_guests=2,
        guest_name="张三",
        guest_mobile="13800138000",
        room_type_preference="江景房",
    )
    approval.created_at = datetime.fromisoformat("2026-07-29T00:00:00+08:00")
    return approval


def valid_command() -> ConfirmBookingCommand:
    """创建员工已确认收款的有效下单命令。"""
    return ConfirmBookingCommand(
        property_id=101,
        final_rate_amount=399,
        received_amount=399,
        income_method_id=1,
        payment_confirmed=True,
    )


@pytest.mark.asyncio
async def test_create_pending_approval_maps_guest_request() -> None:
    """客人确认资料后只生成待审批单，不直接创建订单。"""
    repository = CaptureApprovalRepository()
    service = ApprovalService(repository, code_factory=lambda: "APP-NEW")
    request = BookingRequest(
        check_in_date=date(2026, 8, 1),
        check_out_date=date(2026, 8, 2),
        number_of_guests=2,
        guest_name="张三",
        guest_mobile="13800138000",
        room_type_preference="江景房",
    )

    approval = await service.create_pending(conversation_id=1, request=request)

    assert approval.approval_code == "APP-NEW"
    assert approval.status == ApprovalStatus.PENDING
    assert approval.property_id is None


@pytest.mark.asyncio
async def test_confirming_same_approval_twice_creates_one_reservation() -> None:
    """数据库状态机保证重复点击不会重复落单。"""
    approval = pending_approval()
    repository = InMemoryApprovalRepository(approval)
    hostex = HostexStub()
    service = BookingService(repository, AllowApprover(), hostex)

    first = await service.confirm_and_create(1, employee_id=1, command=valid_command())
    second = await service.confirm_and_create(1, employee_id=1, command=valid_command())

    assert first.status == ApprovalStatus.BOOKED
    assert second.status == ApprovalStatus.BOOKED
    assert first.hostex_reservation_code == "R-100"
    assert hostex.create_calls == 1


@pytest.mark.asyncio
async def test_room_conflict_stops_before_create() -> None:
    """下单前房态冲突时不得调用创建订单接口。"""
    repository = InMemoryApprovalRepository(pending_approval())
    hostex = HostexStub(available=False)
    service = BookingService(repository, AllowApprover(), hostex)

    result = await service.confirm_and_create(1, employee_id=1, command=valid_command())

    assert result.status == ApprovalStatus.CONFLICT
    assert hostex.create_calls == 0


@pytest.mark.asyncio
async def test_ambiguous_create_result_requires_manual_review_without_retry() -> None:
    """创建订单超时后不得自动重放写请求。"""
    repository = InMemoryApprovalRepository(pending_approval())
    hostex = HostexStub(ambiguous=True)
    service = BookingService(repository, AllowApprover(), hostex)

    result = await service.confirm_and_create(1, employee_id=1, command=valid_command())

    assert result.status == ApprovalStatus.NEEDS_REVIEW
    assert hostex.create_calls == 1


@pytest.mark.asyncio
async def test_business_create_error_moves_to_review_instead_of_stuck_creating() -> None:
    """明确业务失败也必须离开 CREATING 并保留可解释错误码。"""
    repository = InMemoryApprovalRepository(pending_approval())
    hostex = HostexStub(business_error=True)
    service = BookingService(repository, AllowApprover(), hostex)

    result = await service.confirm_and_create(
        1, employee_id=1, command=valid_command()
    )

    assert result.status == ApprovalStatus.NEEDS_REVIEW
    assert result.failure_code == 422
    assert hostex.create_calls == 1


@pytest.mark.asyncio
async def test_repeated_confirmation_reconciles_creating_without_new_write() -> None:
    """遗留 CREATING 审批再次确认时只能核验，不能再次创建订单。"""
    approval = pending_approval()
    approval.status = ApprovalStatus.CREATING
    approval.property_id = 101
    approval.final_rate_amount = 399
    approval.received_amount = 399
    approval.income_method_id = 1
    approval.approved_at = datetime.now(UTC)
    repository = InMemoryApprovalRepository(approval)
    hostex = HostexStub()
    service = BookingService(repository, AllowApprover(), hostex)

    result = await service.confirm_and_create(
        1, employee_id=1, command=valid_command()
    )

    assert result.status == ApprovalStatus.BOOKED
    assert hostex.create_calls == 0


@pytest.mark.asyncio
async def test_reconciliation_does_not_link_an_old_matching_order() -> None:
    """姓名日期相同但创建时间早于本次审批的旧订单不得被误关联。"""
    approval = pending_approval()
    approval.status = ApprovalStatus.CREATING
    approval.property_id = 101
    approval.final_rate_amount = 399
    approval.received_amount = 399
    approval.income_method_id = 1
    approval.approved_at = datetime.now(UTC)
    repository = InMemoryApprovalRepository(approval)
    hostex = HostexStub(
        reservation_created_at=(
            approval.approved_at - timedelta(days=1)
        ).isoformat()
    )
    service = BookingService(repository, AllowApprover(), hostex)

    result = await service.confirm_and_create(
        1, employee_id=1, command=valid_command()
    )

    assert result.status == ApprovalStatus.NEEDS_REVIEW
    assert result.hostex_reservation_code is None
