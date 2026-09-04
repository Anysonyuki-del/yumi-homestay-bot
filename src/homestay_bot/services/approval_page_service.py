from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import ApprovalStatus
from homestay_bot.domain.models import AuditLog, BookingApproval
from homestay_bot.domain.schemas import ConfirmBookingCommand
from homestay_bot.services.approval_sensitive_data import ApprovalSensitiveData
from homestay_bot.services.booking_service import BookingService


class ApprovalHostexPort(Protocol):
    """定义审批详情页读取百居易字典和参考数据的接口。"""

    async def list_properties(self) -> Sequence[Any]:
        """返回可供员工最终选择的物理房间。"""

    async def list_reference_prices(
        self, start_date: date | str, end_date: date | str
    ) -> Sequence[Any]:
        """返回入住区间的渠道日历参考价。"""

    async def list_income_methods(self) -> Sequence[Any]:
        """返回百居易账户可用的收入方式。"""


@dataclass(frozen=True)
class ApprovalPageView:
    """仅暴露审批模板需要的只读字段，避免 ORM 密文进入视图层。"""

    id: int
    approval_code: str
    status: ApprovalStatus
    check_in_date: date
    check_out_date: date
    number_of_guests: int
    guest_name: str
    room_type_preference: str
    special_requests: str | None


_REJECTABLE_STATUSES = frozenset(
    {
        ApprovalStatus.PENDING,
        ApprovalStatus.NEEDS_REVIEW,
        ApprovalStatus.CONFLICT,
    }
)


class ApprovalPageService:
    """汇总审批详情，并把确认动作交给安全下单状态机。"""

    def __init__(
        self,
        *,
        session: AsyncSession,
        hostex: ApprovalHostexPort,
        booking: BookingService,
        sensitive_data: ApprovalSensitiveData,
    ) -> None:
        """注入当前会话、百居易只读接口、下单与敏感数据服务。"""
        self._session = session
        self._hostex = hostex
        self._booking = booking
        self._sensitive_data = sensitive_data

    async def get_detail(self, approval_id: int) -> dict[str, Any]:
        """读取审批单，并并行所需小规模参考数据。"""
        approval = await self._session.get(BookingApproval, approval_id)
        if approval is None:
            raise LookupError(f"审批单不存在: {approval_id}")
        properties = await self._hostex.list_properties()
        prices = await self._hostex.list_reference_prices(
            approval.check_in_date, approval.check_out_date
        )
        income_methods = await self._hostex.list_income_methods()
        sensitive = self._sensitive_data.read(approval)
        return {
            "approval": self._to_view(approval),
            "masked_mobile": (
                self.mask_mobile(sensitive.guest_mobile)
                if sensitive.guest_mobile is not None
                else "已清理"
            ),
            "properties": [item.model_dump(mode="json") for item in properties],
            "reference_prices": [
                item.model_dump(mode="json") for item in prices
            ],
            "income_methods": [
                item.model_dump(mode="json") for item in income_methods
            ],
        }

    async def list_pending(
        self, *, offset: int, limit: int
    ) -> list[ApprovalPageView]:
        """按稳定顺序分页返回需要员工关注的审批单。"""
        statement = (
            select(BookingApproval)
            .where(
                BookingApproval.status.in_(
                    {
                        ApprovalStatus.PENDING,
                        ApprovalStatus.CREATING,
                        ApprovalStatus.NEEDS_REVIEW,
                        ApprovalStatus.CONFLICT,
                    }
                )
            )
            .order_by(BookingApproval.created_at.desc(), BookingApproval.id.desc())
            .offset(offset)
            .limit(limit)
        )
        approvals = list((await self._session.scalars(statement)).all())
        return [self._to_view(approval) for approval in approvals]

    async def confirm(
        self,
        approval_id: int,
        employee_id: int,
        command: ConfirmBookingCommand,
    ) -> BookingApproval:
        """把一次性表单确认交给具备幂等保护的下单服务。"""
        return await self._booking.confirm_and_create(
            approval_id, employee_id, command
        )

    async def reject(
        self,
        approval_id: int,
        employee_id: int,
        reason: str,
    ) -> BookingApproval:
        """把待处理审批标记为已拒绝，并把拒绝原因写入审计。

        ApprovalStatus.REJECTED 此前没有任何写入点，审批只能确认不能拒绝。
        原因必须在拒绝当下记入审计：数据保留逻辑会清理已拒绝审批，之后无从追溯。
        """
        cleaned = reason.strip()
        if not cleaned:
            raise ValueError("拒绝原因不能为空")
        approval = await self._session.scalar(
            select(BookingApproval)
            .where(BookingApproval.id == approval_id)
            .with_for_update()
        )
        if approval is None:
            raise LookupError("审批单不存在")
        if approval.status not in _REJECTABLE_STATUSES:
            raise ValueError("当前审批状态不能拒绝")
        previous = approval.status
        approval.status = ApprovalStatus.REJECTED
        self._session.add(
            AuditLog(
                actor_employee_id=employee_id,
                action="booking_approval_rejected",
                target_type="booking_approval",
                target_id=str(approval_id),
                details={
                    "from_status": previous.value,
                    "reason": cleaned[:500],
                },
            )
        )
        await self._session.flush()
        return approval

    @staticmethod
    def mask_mobile(mobile: str) -> str:
        """保留手机号首三位和末四位，短号码使用通用掩码。"""
        if len(mobile) >= 7:
            return f"{mobile[:3]}{'*' * (len(mobile) - 7)}{mobile[-4:]}"
        return "*" * len(mobile)

    def _to_view(self, approval: BookingApproval) -> ApprovalPageView:
        """解密模板所需字段并复制到不可变视图，禁止泄露 ORM 密文字段。"""
        sensitive = self._sensitive_data.read(approval)
        return ApprovalPageView(
            id=approval.id,
            approval_code=approval.approval_code,
            status=approval.status,
            check_in_date=approval.check_in_date,
            check_out_date=approval.check_out_date,
            number_of_guests=approval.number_of_guests,
            guest_name=sensitive.guest_name or "已清理",
            room_type_preference=approval.room_type_preference,
            special_requests=sensitive.special_requests,
        )
