"""统一管理后台模板环境与安全展示助手。"""

from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

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
    ReminderStatus,
    ReminderType,
    RoomOperationalStatus,
)
from homestay_bot.version import get_app_version, get_app_version_label

WUHAN_TIMEZONE = ZoneInfo("Asia/Shanghai")

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
        (RoomOperationalStatus, "not_started"): "未开始",
        (RoomOperationalStatus, "cleaning"): "保洁中",
        (RoomOperationalStatus, "pending_inspection"): "待检查",
        (RoomOperationalStatus, "ready"): "可入住",
        (RoomOperationalStatus, "occupied"): "已入住",
        (RoomOperationalStatus, "maintenance"): "维修中",
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


def datetime_zh(value: object) -> str:
    """把时间统一转换为武汉本地时间，避免后台误读 UTC。"""
    if not isinstance(value, datetime):
        return "—"
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    local = aware.astimezone(WUHAN_TIMEZONE)
    return f"{local.year}年{local.month}月{local.day}日 {local:%H:%M}"


def safe_external_url(value: object) -> str:
    """只允许 HTTPS 外链，拒绝脚本协议、凭据和畸形主机。"""
    if not isinstance(value, str) or len(value) > 2048:
        return "#"
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        return "#"
    return value


def base_template_context(_request: object) -> dict[str, str]:
    """为全部后台模板提供统一产品名称和发布版本。"""
    return {
        "app_name": "YuMi 管理后台",
        "app_version": get_app_version(),
        "app_version_label": get_app_version_label(),
    }


templates = Jinja2Templates(
    directory=Path(__file__).resolve().parent / "templates",
    context_processors=[base_template_context],
)
templates.env.filters.update(
    {
        "date_zh": date_zh,
        "datetime_zh": datetime_zh,
        "enum_zh": status_zh,
        "status_zh": status_zh,
    }
)
templates.env.globals.update(
    {
        "safe_external_url": safe_external_url,
    }
)
