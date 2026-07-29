from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.models import BookingApproval
from homestay_bot.domain.schemas import ConfirmBookingCommand
from homestay_bot.services.booking_service import BookingService


class ApprovalHostexPort(Protocol):
    """定义审批详情页读取百居易字典和参考数据的接口。"""

    async def list_properties(self) -> list[Any]:
        """返回可供员工最终选择的物理房间。"""

    async def list_reference_prices(
        self, start_date: object, end_date: object
    ) -> list[Any]:
        """返回入住区间的渠道日历参考价。"""

    async def list_income_methods(self) -> list[Any]:
        """返回百居易账户可用的收入方式。"""


class ApprovalPageService:
    """汇总审批详情，并把确认动作交给安全下单状态机。"""

    def __init__(
        self,
        *,
        session: AsyncSession,
        hostex: ApprovalHostexPort,
        booking: BookingService,
    ) -> None:
        """注入当前会话、百居易只读接口和下单服务。"""
        self._session = session
        self._hostex = hostex
        self._booking = booking

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
        return {
            "approval": approval,
            "masked_mobile": self.mask_mobile(approval.guest_mobile),
            "properties": [item.model_dump(mode="json") for item in properties],
            "reference_prices": [
                item.model_dump(mode="json") for item in prices
            ],
            "income_methods": [
                item.model_dump(mode="json") for item in income_methods
            ],
        }

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

    @staticmethod
    def mask_mobile(mobile: str) -> str:
        """保留手机号首三位和末四位，短号码使用通用掩码。"""
        if len(mobile) >= 7:
            return f"{mobile[:3]}{'*' * (len(mobile) - 7)}{mobile[-4:]}"
        return "*" * len(mobile)
