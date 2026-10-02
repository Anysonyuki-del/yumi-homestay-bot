"""供仓储、服务与页面共用的纯中文展示格式。"""

from collections.abc import Callable
from datetime import date, datetime
from enum import Enum

from homestay_bot.domain.enums import (
    ApprovalStatus,
    BusinessTaskStatus,
    BusinessTaskType,
    ComplaintReviewStatus,
    CredentialDeliveryStatus,
    CustomerMergeStatus,
    EmployeeRole,
    JobStatus,
    KnowledgeCandidateDraftStatus,
    KnowledgeCandidateStatus,
    MessageOrigin,
    ReminderStatus,
    ReminderType,
    RoomOccupancyStatus,
    RoomOperationalStatus,
    TaskClosureReason,
)

PAGE_MESSAGE_MAX_LENGTH = 200

_STATUS_LABELS: dict[tuple[type[Enum], str], str] = (
    {
        (ApprovalStatus, "pending"): "待审批",
        (ApprovalStatus, "creating"): "创建中",
        (ApprovalStatus, "booked"): "已预订",
        (ApprovalStatus, "rejected"): "已拒绝",
        (ApprovalStatus, "conflict"): "有冲突",
        (ApprovalStatus, "needs_review"): "需复核",
        (BusinessTaskStatus, "pending_confirmation"): "待确认",
        (BusinessTaskStatus, "pending_assignment"): "待分派",
        (BusinessTaskStatus, "assigned"): "已分派",
        (BusinessTaskStatus, "in_progress"): "进行中",
        (BusinessTaskStatus, "pending_inspection"): "待检查",
        (BusinessTaskStatus, "completed"): "已完成",
        (BusinessTaskStatus, "cancelled"): "已取消",
        (BusinessTaskStatus, "expired"): "已失效",
        (RoomOperationalStatus, "not_started"): "未开始",
        (RoomOperationalStatus, "cleaning"): "保洁中",
        (RoomOperationalStatus, "pending_inspection"): "待检查",
        (RoomOperationalStatus, "ready"): "可入住",
        (RoomOperationalStatus, "occupied"): "已入住",
        (RoomOperationalStatus, "maintenance"): "维修中",
        (RoomOccupancyStatus, "unknown"): "入住信息待核实",
        (RoomOccupancyStatus, "vacant"): "今日无订单占用",
        (RoomOccupancyStatus, "arriving_today"): "今日到店",
        (RoomOccupancyStatus, "occupied"): "住宿期内",
        (RoomOccupancyStatus, "departing_today"): "今日离店",
        (RoomOccupancyStatus, "turnover_today"): "今日周转",
        (TaskClosureReason, "order_cancelled"): "关联订单已取消",
        (TaskClosureReason, "window_expired"): "服务窗口已结束",
        (TaskClosureReason, "superseded"): "已被后续任务替代",
        (CredentialDeliveryStatus, "pending"): "待发送",
        (CredentialDeliveryStatus, "sent"): "已发送",
        (CredentialDeliveryStatus, "needs_review"): "需复核",
        (CredentialDeliveryStatus, "manual_followup"): "人工跟进",
        (CredentialDeliveryStatus, "cancelled"): "已取消",
        (ReminderStatus, "scheduled"): "已计划",
        (ReminderStatus, "platform_accepted"): "平台已受理",
        (ReminderStatus, "manual_followup"): "人工跟进",
        (ReminderStatus, "cancelled"): "已取消",
        (CustomerMergeStatus, "pending"): "待判断",
        (CustomerMergeStatus, "accepted"): "已合并",
        (CustomerMergeStatus, "rejected"): "已拒绝",
        (KnowledgeCandidateStatus, "open"): "待处理",
        (KnowledgeCandidateStatus, "snoozed"): "已暂缓",
        (KnowledgeCandidateStatus, "converted"): "已转知识",
        (KnowledgeCandidateDraftStatus, "none"): "无草稿",
        (KnowledgeCandidateDraftStatus, "pending"): "生成中",
        (KnowledgeCandidateDraftStatus, "ready"): "待审核",
        (KnowledgeCandidateDraftStatus, "failed"): "生成失败",
        (ComplaintReviewStatus, "pending_analysis"): "待分析",
        (ComplaintReviewStatus, "ready_for_review"): "待复核",
        (ComplaintReviewStatus, "editing"): "编辑中",
        (ComplaintReviewStatus, "send_queued"): "待发送",
        (ComplaintReviewStatus, "delivery_failed"): "发送失败",
        (ComplaintReviewStatus, "sent"): "已发送",
        (ComplaintReviewStatus, "returned"): "已退回",
        (ComplaintReviewStatus, "analysis_failed"): "分析失败",
        (ComplaintReviewStatus, "cancelled"): "已取消",
        (MessageOrigin, "guest"): "客人",
        (MessageOrigin, "servicer"): "人工客服",
        (MessageOrigin, "bot"): "机器人",
        (JobStatus, "pending"): "待执行",
        (JobStatus, "running"): "执行中",
        (JobStatus, "completed"): "已完成",
        (JobStatus, "failed"): "失败",
        (EmployeeRole, "admin"): "管理员",
        (EmployeeRole, "staff"): "员工",
        (BusinessTaskType, "cleaning"): "保洁",
        (BusinessTaskType, "maintenance"): "维修",
        (BusinessTaskType, "supplies"): "补给",
        (BusinessTaskType, "special_service"): "特殊服务",
        (BusinessTaskType, "early_check_in"): "提前入住",
        (BusinessTaskType, "late_check_out"): "延迟退房",
        (BusinessTaskType, "manual_contact"): "人工联系",
        (ReminderType, "pre_arrival"): "入住前提醒",
        (ReminderType, "arrival_day"): "入住日提醒",
        (ReminderType, "checkout"): "退房提醒",
        (ReminderType, "thank_you"): "感谢提醒",
    }
)


def status_zh(value: object) -> str:
    """把受控枚举转换为中文；未知值只展示其非敏感文本。"""
    if isinstance(value, Enum):
        return _STATUS_LABELS.get(
            (type(value), str(value.value)), str(value.value).replace("_", " ")
        )
    if value is None:
        return "—"
    return str(value)


def date_zh(value: object) -> str:
    """以紧凑中文格式展示日期。"""
    if isinstance(value, datetime):
        value = value.date()
    if not isinstance(value, date):
        return "—"
    return f"{value.year}年{value.month}月{value.day}日"


def task_refusal_label(
    task_id: int,
    task_type: BusinessTaskType,
    room: str | None,
    service_date: date | None,
    status: BusinessTaskStatus,
    room_limit: int = 12,
) -> str:
    """用任务定位字段点名拒绝项；只缩短房间名，保留完整日期和状态。"""
    room_label = " ".join((room or "").split()) or "房间待确认"
    if len(room_label) > room_limit:
        room_label = room_label[: room_limit - 1] + "…"
    date_label = date_zh(service_date) if service_date is not None else "日期待补齐"
    return (
        f"任务 #{task_id}·{status_zh(task_type)}·{room_label}·{date_label}"
        f"（{status_zh(status)}）"
    )


def refusal_message(
    reason: str,
    labels: Callable[[int], list[str]],
    total: int,
    next_step: str,
) -> str:
    """按会话提示预算点名最多三项，始终保留拒绝原因、总数和下一步。"""
    candidates = (labels(12), labels(6))
    for count in range(min(3, total), 0, -1):
        # 同样的条数先缩短房间名，仍放不下才减少完整任务标签。
        for items in candidates:
            remainder = f"，另 {total - count} 条" if total > count else ""
            message = (
                f"{reason}。以下 {total} 条不符合：{'；'.join(items[:count])}"
                f"{remainder}。{next_step}"
            )
            if len(message) <= PAGE_MESSAGE_MAX_LENGTH:
                return message
    return f"{reason}。共 {total} 条不符合。{next_step}"
