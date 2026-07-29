from contextlib import AbstractAsyncContextManager
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from homestay_bot.domain.enums import ApprovalStatus
from homestay_bot.domain.models import BookingApproval
from homestay_bot.domain.schemas import ConfirmBookingCommand
from homestay_bot.integrations.hostex_client import (
    CreateReservationRequest,
    CreateReservationResult,
    HostexTransportError,
    PropertyAvailability,
    Reservation,
    ReservationQuery,
)


class ApprovalRepository(Protocol):
    """定义审批服务所需的最小持久化接口。"""

    def transaction(self) -> AbstractAsyncContextManager[None]:
        """打开一个原子事务边界。"""

    async def get_for_update(self, approval_id: int) -> BookingApproval:
        """使用行锁读取审批单。"""

    async def save(self, approval: BookingApproval) -> None:
        """持久化审批单状态。"""


class PermissionChecker(Protocol):
    """定义员工下单权限检查接口。"""

    async def require_booking_approver(self, employee_id: int) -> None:
        """无权限时必须抛出业务异常。"""


class HostexBookingPort(Protocol):
    """定义安全下单流程使用的百居易接口。"""

    async def list_availabilities(
        self,
        property_ids: list[int],
        start_date: date,
        end_date: date,
    ) -> list[PropertyAvailability]:
        """读取目标房间的最新房态。"""

    async def create_reservation(
        self, request: CreateReservationRequest
    ) -> CreateReservationResult:
        """执行一次不可自动重放的订单创建。"""

    async def list_reservations(self, query: ReservationQuery) -> list[Reservation]:
        """查询创建后的可能匹配订单。"""


class BookingService:
    """只接受已授权员工确认，不向模型暴露写操作。"""

    def __init__(
        self,
        approvals: ApprovalRepository,
        permissions: PermissionChecker,
        hostex: HostexBookingPort,
    ) -> None:
        """注入仓储、权限和百居易端口，便于隔离验证。"""
        self._approvals = approvals
        self._permissions = permissions
        self._hostex = hostex

    async def confirm_and_create(
        self,
        approval_id: int,
        employee_id: int,
        command: ConfirmBookingCommand,
    ) -> BookingApproval:
        """锁定审批单、复查房态并最多创建一次百居易订单。"""
        await self._permissions.require_booking_approver(employee_id)
        if not command.payment_confirmed:
            raise ValueError("员工必须明确确认已经收款")

        async with self._approvals.transaction():
            approval = await self._approvals.get_for_update(approval_id)
            if approval.status is ApprovalStatus.BOOKED:
                return approval
            if approval.status is not ApprovalStatus.PENDING:
                return approval

            if not await self._is_property_available(approval, command.property_id):
                approval.status = ApprovalStatus.CONFLICT
                await self._approvals.save(approval)
                return approval

            approval.status = ApprovalStatus.CREATING
            approval.property_id = command.property_id
            approval.final_rate_amount = command.final_rate_amount
            approval.received_amount = command.received_amount
            approval.income_method_id = command.income_method_id
            approval.approved_by = employee_id
            approval.approved_at = datetime.now(UTC)
            await self._approvals.save(approval)

        try:
            result = await self._hostex.create_reservation(self._build_create_request(approval))
            approval.hostex_request_id = result.request_id
        except HostexTransportError:
            return await self._reconcile_or_mark_review(approval)

        return await self._reconcile_or_mark_review(approval)

    async def _is_property_available(self, approval: BookingApproval, property_id: int) -> bool:
        """要求入住日至退房日前一天全部可用。"""
        room_states = await self._hostex.list_availabilities(
            [property_id], approval.check_in_date, approval.check_out_date
        )
        if len(room_states) != 1:
            return False

        expected_dates: set[date] = set()
        current_date = approval.check_in_date
        while current_date < approval.check_out_date:
            expected_dates.add(current_date)
            current_date += timedelta(days=1)

        available_dates = {day.date for day in room_states[0].days if day.available}
        return expected_dates <= available_dates

    def _build_create_request(self, approval: BookingApproval) -> CreateReservationRequest:
        """把已审批字段映射成百居易直订请求。"""
        if (
            approval.property_id is None
            or approval.final_rate_amount is None
            or approval.received_amount is None
            or approval.income_method_id is None
        ):
            raise ValueError("审批单缺少创建订单所需字段")

        return CreateReservationRequest(
            property_id=approval.property_id,
            custom_channel_id=1,
            check_in_date=approval.check_in_date,
            check_out_date=approval.check_out_date,
            number_of_guests=approval.number_of_guests,
            guest_name=approval.guest_name,
            mobile=approval.guest_mobile,
            currency="CNY",
            rate_amount=approval.final_rate_amount,
            commission_amount=0,
            received_amount=approval.received_amount,
            income_method_id=approval.income_method_id,
            remarks=f"approval_code={approval.approval_code}",
        )

    async def _reconcile_or_mark_review(self, approval: BookingApproval) -> BookingApproval:
        """写后查询唯一精确订单；无法唯一确定时转人工核实。"""
        if approval.property_id is None:
            approval.status = ApprovalStatus.NEEDS_REVIEW
            await self._approvals.save(approval)
            return approval

        candidates = await self._hostex.list_reservations(
            ReservationQuery(
                property_id=approval.property_id,
                start_check_in_date=approval.check_in_date,
                end_check_in_date=approval.check_in_date,
                order_by="created_at",
                limit=20,
            )
        )
        matches = [
            item
            for item in candidates
            if item.check_in_date == approval.check_in_date
            and item.check_out_date == approval.check_out_date
            and item.guest_name == approval.guest_name
            and item.guest_phone == approval.guest_mobile
        ]

        async with self._approvals.transaction():
            locked = await self._approvals.get_for_update(approval.id)
            if len(matches) == 1:
                locked.status = ApprovalStatus.BOOKED
                locked.hostex_reservation_code = matches[0].reservation_code
            else:
                locked.status = ApprovalStatus.NEEDS_REVIEW
            await self._approvals.save(locked)
            return locked
