"""以确定性业务规则治理已经失去价值的开放任务。"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from homestay_bot.domain.enums import (
    BusinessTaskStatus,
    BusinessTaskType,
    TaskClosureReason,
)
from homestay_bot.domain.task_lifecycle import (
    TaskLifecycleCandidate,
    local_service_window_expires_at,
    manual_contact_expires_at,
)

_CANCELLED_ORDER_STATUSES = frozenset(
    {"cancelled", "canceled", "declined", "expired", "deleted"}
)
_ORDER_BOUND_TYPES = frozenset(
    {
        BusinessTaskType.CLEANING,
        BusinessTaskType.MANUAL_CONTACT,
        BusinessTaskType.EARLY_CHECK_IN,
        BusinessTaskType.LATE_CHECK_OUT,
    }
)
_AUTO_EXPIRABLE_STATUSES = frozenset(
    {
        BusinessTaskStatus.PENDING_CONFIRMATION,
        BusinessTaskStatus.PENDING_ASSIGNMENT,
    }
)


@dataclass(frozen=True, slots=True)
class TaskLifecycleSweepResult:
    """记录一轮任务治理的扫描、失效与跳过数量。"""

    scanned: int
    expired: int
    skipped: int


class TaskLifecycleRepositoryPort(Protocol):
    """定义任务生命周期服务使用的批量读取和安全关闭接口。"""

    async def list_lifecycle_candidates(
        self,
        *,
        now: datetime,
        limit: int,
        order_id: int | None = None,
    ) -> tuple[TaskLifecycleCandidate, ...]:
        """读取有限开放候选，不加载任务正文。"""

    async def expire_if_safe(
        self,
        task_id: int,
        *,
        reason: TaskClosureReason,
        now: datetime,
    ) -> bool:
        """重新锁定任务并在仍满足安全边界时写入失效终态。"""


class TaskLifecycleService:
    """只根据订单、提醒窗口和执行证据决定任务是否失效。"""

    def __init__(self, repository: TaskLifecycleRepositoryPort) -> None:
        """注入短事务仓储。"""
        self._repository = repository

    @staticmethod
    def _reason(
        candidate: TaskLifecycleCandidate,
        now: datetime,
    ) -> TaskClosureReason | None:
        """返回唯一确定的失效原因；仍有执行价值时返回空。"""
        if (
            candidate.status not in _AUTO_EXPIRABLE_STATUSES
            or candidate.assigned_employee_id is not None
            or candidate.has_checklist
            or candidate.has_attachments
        ):
            return None
        normalized_order_status = (candidate.order_status or "").strip().lower()
        if (
            normalized_order_status in _CANCELLED_ORDER_STATUSES
            and candidate.task_type in _ORDER_BOUND_TYPES
        ):
            return TaskClosureReason.ORDER_CANCELLED
        deadline = candidate.expires_at
        if (
            deadline is None
            and candidate.task_type is BusinessTaskType.MANUAL_CONTACT
            and candidate.reminder_type is not None
            and candidate.reminder_scheduled_at is not None
        ):
            deadline = manual_contact_expires_at(
                candidate.reminder_type,
                candidate.reminder_scheduled_at,
            )
        if (
            deadline is None
            and candidate.task_type
            in {BusinessTaskType.EARLY_CHECK_IN, BusinessTaskType.LATE_CHECK_OUT}
            and candidate.service_date is not None
        ):
            deadline = local_service_window_expires_at(candidate.service_date)
        if deadline is None:
            return None
        aware_deadline = deadline.replace(tzinfo=UTC) if deadline.tzinfo is None else deadline
        return TaskClosureReason.WINDOW_EXPIRED if now >= aware_deadline else None

    async def sweep(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
        order_id: int | None = None,
    ) -> TaskLifecycleSweepResult:
        """有限扫描候选，并由仓储再次校验后安全写入失效。"""
        if not 1 <= limit <= 500:
            raise ValueError("任务生命周期批次必须介于 1 到 500")
        observed_at = now or datetime.now(UTC)
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        candidates = await self._repository.list_lifecycle_candidates(
            now=observed_at,
            limit=limit,
            order_id=order_id,
        )
        expired = 0
        for candidate in candidates:
            reason = self._reason(candidate, observed_at)
            if reason is None:
                continue
            if await self._repository.expire_if_safe(
                candidate.task_id,
                reason=reason,
                now=observed_at,
            ):
                expired += 1
        return TaskLifecycleSweepResult(
            scanned=len(candidates),
            expired=expired,
            skipped=len(candidates) - expired,
        )
