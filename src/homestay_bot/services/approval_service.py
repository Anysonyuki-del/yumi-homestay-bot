import secrets
from collections.abc import Callable
from typing import Protocol

from homestay_bot.domain.enums import ApprovalStatus
from homestay_bot.domain.models import BookingApproval
from homestay_bot.domain.schemas import BookingRequest
from homestay_bot.services.approval_sensitive_data import ApprovalSensitiveData


class PendingApprovalRepository(Protocol):
    """定义创建待审批单所需的最小仓储接口。"""

    async def add(self, approval: BookingApproval) -> BookingApproval:
        """持久化并返回已分配主键的审批单。"""

    async def get_by_source_message_id(
        self, source_message_id: str
    ) -> BookingApproval | None:
        """按来源消息读取已存在的审批单。"""


def generate_approval_code() -> str:
    """生成不可预测且适合放入百居易备注的审批编号。"""
    return f"APP-{secrets.token_urlsafe(12)}"


class ApprovalService:
    """把客人确认的资料转换成不占房的待审批单。"""

    def __init__(
        self,
        repository: PendingApprovalRepository,
        *,
        sensitive_data: ApprovalSensitiveData,
        code_factory: Callable[[], str] = generate_approval_code,
    ) -> None:
        """注入仓储、敏感数据服务和编号生成器，保证测试可重复。"""
        self._repository = repository
        self._sensitive_data = sensitive_data
        self._code_factory = code_factory

    async def create_pending(
        self,
        conversation_id: int,
        request: BookingRequest,
        *,
        source_message_id: str | None = None,
    ) -> BookingApproval:
        """校验日期并创建尚未选择具体房间的审批单。"""
        if request.check_out_date <= request.check_in_date:
            raise ValueError("退房日期必须晚于入住日期")

        if source_message_id is not None:
            existing = await self._repository.get_by_source_message_id(
                source_message_id
            )
            if existing is not None:
                return existing
        approval = BookingApproval(
            approval_code=self._code_factory(),
            source_message_id=source_message_id,
            conversation_id=conversation_id,
            status=ApprovalStatus.PENDING,
            check_in_date=request.check_in_date,
            check_out_date=request.check_out_date,
            number_of_guests=request.number_of_guests,
            room_type_preference=request.room_type_preference,
        )
        # 阶段 2B 只保存用途隔离密文，审批表不再持久化旧明文字段。
        self._sensitive_data.write(
            approval,
            guest_name=request.guest_name,
            guest_mobile=request.guest_mobile,
            special_requests=request.special_requests,
        )
        return await self._repository.add(approval)
