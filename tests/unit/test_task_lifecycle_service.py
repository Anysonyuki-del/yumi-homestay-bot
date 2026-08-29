from datetime import UTC, date, datetime

from homestay_bot.domain.enums import (
    BusinessTaskOrigin,
    BusinessTaskStatus,
    BusinessTaskType,
    ReminderType,
    TaskClosureReason,
)
from homestay_bot.services.task_lifecycle_service import (
    TaskLifecycleCandidate,
    TaskLifecycleService,
)


class LifecycleRepositoryStub:
    """记录生命周期服务筛选并尝试失效的任务。"""

    def __init__(self, candidates: tuple[TaskLifecycleCandidate, ...]) -> None:
        """保存固定候选和最终失效调用。"""
        self.candidates = candidates
        self.expired: list[tuple[int, TaskClosureReason]] = []

    async def list_lifecycle_candidates(
        self,
        *,
        now: datetime,
        limit: int,
        order_id: int | None = None,
    ) -> tuple[TaskLifecycleCandidate, ...]:
        """返回符合订单范围的有限候选。"""
        selected = (
            self.candidates
            if order_id is None
            else tuple(item for item in self.candidates if item.order_id == order_id)
        )
        return selected[:limit]

    async def expire_if_safe(
        self,
        task_id: int,
        *,
        reason: TaskClosureReason,
        now: datetime,
    ) -> bool:
        """记录通过领域规则的失效请求。"""
        self.expired.append((task_id, reason))
        return True


def _candidate(
    task_id: int,
    *,
    task_type: BusinessTaskType,
    status: BusinessTaskStatus = BusinessTaskStatus.PENDING_CONFIRMATION,
    origin: BusinessTaskOrigin,
    order_status: str = "accepted",
    service_date: date = date(2026, 8, 28),
    assigned_employee_id: int | None = None,
    has_checklist: bool = False,
    has_attachments: bool = False,
    reminder_type: ReminderType | None = None,
    reminder_scheduled_at: datetime | None = None,
) -> TaskLifecycleCandidate:
    """构造不含客户正文的生命周期候选。"""
    return TaskLifecycleCandidate(
        task_id=task_id,
        order_id=task_id,
        task_type=task_type,
        status=status,
        origin_kind=origin,
        order_status=order_status,
        service_date=service_date,
        assigned_employee_id=assigned_employee_id,
        has_checklist=has_checklist,
        has_attachments=has_attachments,
        reminder_type=reminder_type,
        reminder_scheduled_at=reminder_scheduled_at,
    )


async def test_cancelled_order_expires_only_unstarted_turnover() -> None:
    """取消订单只能自动失效尚未开始且没有执行证据的周转任务。"""
    repository = LifecycleRepositoryStub(
        (
            _candidate(
                1,
                task_type=BusinessTaskType.CLEANING,
                status=BusinessTaskStatus.PENDING_ASSIGNMENT,
                origin=BusinessTaskOrigin.TURNOVER,
                order_status="cancelled",
            ),
            _candidate(
                2,
                task_type=BusinessTaskType.CLEANING,
                status=BusinessTaskStatus.ASSIGNED,
                origin=BusinessTaskOrigin.TURNOVER,
                order_status="cancelled",
                assigned_employee_id=9,
            ),
        )
    )

    result = await TaskLifecycleService(repository).sweep(
        now=datetime(2026, 8, 29, 12, tzinfo=UTC),
        limit=100,
    )

    assert result.scanned == 2
    assert result.expired == 1
    assert repository.expired == [(1, TaskClosureReason.ORDER_CANCELLED)]


async def test_manual_contact_expires_after_its_reminder_window() -> None:
    """入住前提醒在入住日提醒时点后失效，避免继续联系客人。"""
    repository = LifecycleRepositoryStub(
        (
            _candidate(
                3,
                task_type=BusinessTaskType.MANUAL_CONTACT,
                origin=BusinessTaskOrigin.LIFECYCLE_REMINDER,
                reminder_type=ReminderType.PRE_ARRIVAL,
                reminder_scheduled_at=datetime(2026, 8, 28, 10, tzinfo=UTC),
            ),
        )
    )

    result = await TaskLifecycleService(repository).sweep(
        now=datetime(2026, 8, 29, 3, tzinfo=UTC),
        limit=100,
    )

    assert result.expired == 1
    assert repository.expired == [(3, TaskClosureReason.WINDOW_EXPIRED)]


async def test_overdue_maintenance_remains_for_human_review() -> None:
    """维修需求日期已过也不得由时间规则自动关闭。"""
    repository = LifecycleRepositoryStub(
        (
            _candidate(
                4,
                task_type=BusinessTaskType.MAINTENANCE,
                origin=BusinessTaskOrigin.AI_SUGGESTION,
            ),
        )
    )

    result = await TaskLifecycleService(repository).sweep(
        now=datetime(2026, 8, 29, 12, tzinfo=UTC),
        limit=100,
    )

    assert result.expired == 0
    assert result.skipped == 1
    assert repository.expired == []
