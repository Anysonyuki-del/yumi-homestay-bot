"""业务任务失效判断所需的纯领域数据与时间计算。"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from homestay_bot.domain.enums import (
    BusinessTaskOrigin,
    BusinessTaskStatus,
    BusinessTaskType,
    ReminderType,
)

WUHAN_TIMEZONE = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class TaskLifecycleCandidate:
    """保存任务失效判断所需的最小非敏感投影。"""

    task_id: int
    order_id: int | None
    task_type: BusinessTaskType
    status: BusinessTaskStatus
    origin_kind: BusinessTaskOrigin
    order_status: str | None
    service_date: date | None
    assigned_employee_id: int | None
    has_checklist: bool
    has_attachments: bool
    reminder_type: ReminderType | None
    reminder_scheduled_at: datetime | None
    expires_at: datetime | None = None


def manual_contact_expires_at(
    reminder_type: ReminderType,
    scheduled_at: datetime,
) -> datetime:
    """按提醒语义返回人工联系任务的 UTC 截止时间。"""
    aware = scheduled_at.replace(tzinfo=UTC) if scheduled_at.tzinfo is None else scheduled_at
    local = aware.astimezone(WUHAN_TIMEZONE)
    if reminder_type is ReminderType.PRE_ARRIVAL:
        deadline = datetime.combine(
            local.date() + timedelta(days=1),
            time(10, 0),
            tzinfo=WUHAN_TIMEZONE,
        )
    elif reminder_type is ReminderType.ARRIVAL_DAY:
        deadline = datetime.combine(
            local.date() + timedelta(days=1),
            time.min,
            tzinfo=WUHAN_TIMEZONE,
        )
    elif reminder_type is ReminderType.CHECKOUT:
        deadline = datetime.combine(
            local.date(),
            time(18, 0),
            tzinfo=WUHAN_TIMEZONE,
        )
    else:
        deadline = local + timedelta(hours=48)
    return deadline.astimezone(UTC)


def local_service_window_expires_at(service_date: date) -> datetime:
    """把本地服务日结束转换为 UTC，供时效型建议统一判断。"""
    return datetime.combine(
        service_date + timedelta(days=1),
        time.min,
        tzinfo=WUHAN_TIMEZONE,
    ).astimezone(UTC)
