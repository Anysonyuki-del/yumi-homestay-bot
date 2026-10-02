import logging
from contextlib import AbstractAsyncContextManager
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from homestay_bot.domain.enums import ApprovalStatus
from homestay_bot.domain.models import BookingApproval
from homestay_bot.domain.schemas import ConfirmBookingCommand
from homestay_bot.integrations.hostex_client import (
    CreateReservationRequest,
    CreateReservationResult,
    HostexBusinessError,
    HostexTransportError,
    PropertyAvailability,
    Reservation,
    ReservationQuery,
)
from homestay_bot.services.approval_sensitive_data import ApprovalSensitiveData

logger = logging.getLogger(__name__)

# 「创建中」多久没有结果才算卡住。确认页面的重复提交与后台 recover_stale_creating
# 共用这一个阈值：不满时原建单可能仍在等百居易返回，此时去核验、转需复核，就会放开
# A2 回退并发起第二次建单（Codex AR6）。
CREATING_STALE_AFTER = timedelta(minutes=5)


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite 读回的时间不带时区，按入库时的 UTC 解读，与内存里的值才能比较。"""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class ApprovalRepository(Protocol):
    """定义审批服务所需的最小持久化接口。"""

    def transaction(self) -> AbstractAsyncContextManager[None]:
        """打开一个原子事务边界。"""

    async def get_for_update(self, approval_id: int) -> BookingApproval:
        """使用行锁读取审批单。"""

    async def save(self, approval: BookingApproval) -> None:
        """持久化审批单状态。"""

    async def record_late_result(self, approval_id: int, details: dict[str, Any]) -> None:
        """在当前加锁事务中登记迟到结果，不提交或改变审批状态。"""


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
        sensitive_data: ApprovalSensitiveData,
    ) -> None:
        """注入仓储、权限、百居易端口和审批敏感数据服务。"""
        self._approvals = approvals
        self._permissions = permissions
        self._hostex = hostex
        self._sensitive_data = sensitive_data

    async def confirm_and_create(
        self,
        approval_id: int,
        employee_id: int,
        command: ConfirmBookingCommand,
    ) -> BookingApproval:
        """锁定审批单、复查房态并最多创建一次百居易订单。"""
        if not command.payment_confirmed:
            raise ValueError("员工必须明确确认已经收款")

        recovering_creating = False
        async with self._approvals.transaction():
            # 权限读取必须和审批行锁共用同一显式事务，避免 SQLAlchemy 自动事务嵌套。
            await self._permissions.require_booking_approver(employee_id)
            approval = await self._approvals.get_for_update(approval_id)
            if approval.status is ApprovalStatus.BOOKED:
                return approval
            if approval.status is ApprovalStatus.CREATING:
                approved_at = _as_utc(approval.approved_at)
                if (
                    approved_at is not None
                    and datetime.now(UTC) - approved_at < CREATING_STALE_AFTER
                ):
                    # 原建单可能还在进行：不核验、不改状态，原样返回「创建中」。
                    return approval
                recovering_creating = True
            elif approval.status is not ApprovalStatus.PENDING:
                return approval

            if recovering_creating:
                pass
            elif not await self._is_property_available(
                approval, command.property_id
            ):
                approval.status = ApprovalStatus.CONFLICT
                await self._approvals.save(approval)
                return approval
            else:
                approval.status = ApprovalStatus.CREATING
                approval.property_id = command.property_id
                approval.final_rate_amount = command.final_rate_amount
                approval.received_amount = command.received_amount
                approval.income_method_id = command.income_method_id
                approval.approved_by = employee_id
                approval.approved_at = datetime.now(UTC)
                await self._approvals.save(approval)

        # 本轮确认的标记：每次进入 CREATING 都写入新的 approved_at。写回结果前据此判断
        # 审批是否仍属于这一轮，迟到的旧结果不能覆盖新一轮或终态（Codex AR6）。
        attempt = _as_utc(approval.approved_at)
        if recovering_creating:
            return await self._reconcile_or_mark_review(
                approval, attempt=attempt, create_result="not_attempted"
            )

        try:
            result = await self._hostex.create_reservation(self._build_create_request(approval))
        except HostexBusinessError as error:
            return await self._mark_needs_review(
                approval,
                attempt=attempt,
                create_result="business_error",
                failure_code=error.error_code,
                failure_message="百居易返回错误，订单是否已创建待核验",
                request_id=error.request_id,
            )
        except HostexTransportError:
            return await self._reconcile_or_mark_review(
                approval, attempt=attempt, create_result="transport_error"
            )

        # 请求编号不能在这里直接赋给 approval：事务外修改已持久化对象会让会话自动开启
        # 事务，随后 _reconcile_or_mark_review 再显式开事务就报「A transaction is already
        # begun」，上游已建单而本地停在 CREATING（Codex AR3，首次确认同样触发）。
        # 改为在写后核验的加锁事务里一并写入。
        return await self._reconcile_or_mark_review(
            approval, attempt=attempt, create_result="success", request_id=result.request_id
        )

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

        sensitive = self._sensitive_data.require_for_booking(approval)
        return CreateReservationRequest(
            property_id=approval.property_id,
            custom_channel_id=1,
            check_in_date=approval.check_in_date,
            check_out_date=approval.check_out_date,
            number_of_guests=approval.number_of_guests,
            guest_name=sensitive.guest_name,
            mobile=sensitive.guest_mobile,
            currency="CNY",
            rate_amount=approval.final_rate_amount,
            commission_amount=0,
            received_amount=approval.received_amount,
            income_method_id=approval.income_method_id,
            remarks=f"approval_code={approval.approval_code}",
        )

    async def _reconcile_or_mark_review(
        self,
        approval: BookingApproval,
        *,
        attempt: datetime | None,
        create_result: str,
        request_id: str | None = None,
    ) -> BookingApproval:
        """写后查询唯一精确订单；无法唯一确定时转人工核实。

        request_id 是本次建单的上游请求编号，与最终状态在同一加锁事务内写入。
        attempt 是本轮确认时间，写入前核对；create_result 保留建单阶段来源，
        核验失败不能把已经成功的建单结果改写为拒绝。
        """
        if approval.property_id is None:
            return await self._mark_needs_review(
                approval,
                attempt=attempt,
                create_result=create_result,
                failure_message="审批单缺少核验所需房间",
                request_id=request_id,
            )

        try:
            candidates = await self._hostex.list_reservations(
                ReservationQuery(
                    property_id=approval.property_id,
                    start_check_in_date=approval.check_in_date,
                    end_check_in_date=approval.check_in_date,
                    order_by="created_at",
                    limit=20,
                )
            )
        except (HostexBusinessError, HostexTransportError):
            return await self._mark_needs_review(
                approval,
                attempt=attempt,
                create_result=create_result,
                verify_result="failed",
                failure_message="创建结果暂时无法自动核验",
                request_id=request_id,
            )
        sensitive = self._sensitive_data.require_for_booking(approval)
        matches = [
            item
            for item in candidates
            if item.check_in_date == approval.check_in_date
            and item.check_out_date == approval.check_out_date
            and item.guest_name == sensitive.guest_name
            and item.guest_phone == sensitive.guest_mobile
            and self._matches_creation_window(approval, item)
            and self._matches_rate_when_reported(approval, item)
        ]

        async with self._approvals.transaction():
            locked = await self._approvals.get_for_update(approval.id)
            if not self._same_round(locked, attempt):
                await self._record_late_result(
                    locked, attempt=attempt, create_result=create_result,
                    verify_result="matched" if matches else "unmatched",
                    request_id=request_id,
                    reservation_codes=[item.reservation_code for item in matches[:20]],
                )
                return locked
            if request_id is not None:
                locked.hostex_request_id = request_id
            if len(matches) == 1:
                locked.status = ApprovalStatus.BOOKED
                locked.hostex_reservation_code = matches[0].reservation_code
            else:
                locked.status = ApprovalStatus.NEEDS_REVIEW
            await self._approvals.save(locked)
            return locked

    @staticmethod
    def _same_round(locked: BookingApproval, attempt: datetime | None) -> bool:
        """加锁后的数据库现值是否仍是本轮的「创建中」。

        不是就说明期间已被别人处理（恢复转需复核、回退后新一轮确认、人工回填、拒绝），
        本轮结果迟到，写入会覆盖更新的事实；调用方登记日志和审计后丢弃。
        """
        return (
            locked.status is ApprovalStatus.CREATING
            and _as_utc(locked.approved_at) == attempt
        )

    async def _record_late_result(
        self,
        locked: BookingApproval,
        *,
        attempt: datetime | None,
        create_result: str,
        verify_result: str,
        request_id: str | None,
        reservation_codes: list[str] | None = None,
        error_code: int | None = None,
    ) -> None:
        """保存丢弃结果的最小证据；先记录诊断，审计失败仍上抛并由事务回滚。"""
        details: dict[str, Any] = {
            "create_result": create_result,
            "verify_result": verify_result,
            "request_id": request_id or None,
            "reservation_codes": (reservation_codes or [])[:20],
            "round_approved_at": attempt.isoformat() if attempt is not None else None,
            "current_status": locked.status.value,
        }
        if error_code is not None:
            details["error_code"] = error_code
        # 只记录轮次、请求和订单标识；格式化消息本身保留这些字段，不依赖日志
        # formatter 是否输出 extra，也不包含客人资料或上游原始正文。
        logger.warning(
            "丢弃不属于当前确认轮次的建单结果 approval_id=%s details=%s",
            locked.id, details,
        )
        await self._approvals.record_late_result(locked.id, details)

    @staticmethod
    def _matches_creation_window(
        approval: BookingApproval, reservation: Reservation
    ) -> bool:
        """只接受审批写入窗口附近创建的订单，避免误关联历史同名订单。"""
        if approval.approved_at is None:
            return False
        try:
            created_at = datetime.fromisoformat(
                reservation.created_at.replace("Z", "+00:00")
            )
        except ValueError:
            return False
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        approved_at = approval.approved_at
        if approved_at.tzinfo is None:
            approved_at = approved_at.replace(tzinfo=UTC)
        return (
            approved_at - timedelta(minutes=2)
            <= created_at
            <= approved_at + timedelta(minutes=30)
        )

    @staticmethod
    def _matches_rate_when_reported(
        approval: BookingApproval, reservation: Reservation
    ) -> bool:
        """百居易返回金额字段时必须与员工确认金额一致。"""
        reported_rate = reservation.rates.get(
            "rate_amount", reservation.rates.get("total_amount")
        )
        if reported_rate is None:
            return False
        try:
            return int(reported_rate) == approval.final_rate_amount
        except (TypeError, ValueError):
            return False

    async def _mark_needs_review(
        self,
        approval: BookingApproval,
        *,
        attempt: datetime | None,
        create_result: str,
        verify_result: str = "skipped",
        failure_code: int | None = None,
        failure_message: str | None = None,
        request_id: str | None = None,
    ) -> BookingApproval:
        """锁定审批并转人工核验，确保任何错误都不会遗留 CREATING。"""
        async with self._approvals.transaction():
            locked = await self._approvals.get_for_update(approval.id)
            if not self._same_round(locked, attempt):
                await self._record_late_result(
                    locked, attempt=attempt, create_result=create_result,
                    verify_result=verify_result, request_id=request_id,
                    error_code=failure_code,
                )
                return locked
            # 错误信封的请求编号只作为迟到诊断；保持本轮原有的成功编号回填语义。
            if request_id is not None and create_result == "success":
                locked.hostex_request_id = request_id
            locked.status = ApprovalStatus.NEEDS_REVIEW
            locked.failure_code = failure_code
            locked.failure_message = failure_message
            await self._approvals.save(locked)
            return locked
