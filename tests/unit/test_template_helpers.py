from datetime import UTC, date, datetime
from types import SimpleNamespace

from homestay_bot.display import PAGE_MESSAGE_MAX_LENGTH, refusal_message, task_refusal_label
from homestay_bot.domain.enums import (
    ApprovalStatus,
    BusinessTaskStatus,
    BusinessTaskType,
    RoomOccupancyStatus,
    RoomOperationalStatus,
    TaskClosureReason,
)
from homestay_bot.web import set_page_error, set_page_notice, templates


def test_templates_register_safe_chinese_helpers() -> None:
    """统一模板环境应提供中文状态和本地日期助手。"""
    environment = templates.env

    assert environment.filters["status_zh"](ApprovalStatus.PENDING) == "待审批"
    assert environment.filters["enum_zh"](ApprovalStatus.PENDING) == "待审批"
    assert environment.filters["status_zh"](RoomOperationalStatus.READY) == "可入住"
    assert environment.filters["status_zh"](RoomOccupancyStatus.UNKNOWN) == "入住信息待核实"
    assert (
        environment.filters["status_zh"](TaskClosureReason.ORDER_CANCELLED)
        == "关联订单已取消"
    )
    assert environment.filters["date_zh"](date(2026, 8, 11)) == "2026年8月11日"
    assert environment.filters["datetime_zh"](
        datetime(2026, 8, 10, 16, 30, tzinfo=UTC)
    ) == "2026年8月11日 00:30"
    assert environment.globals["safe_external_url"]("javascript:alert(1)") == "#"
    assert environment.globals["safe_external_url"]("https://example.com/a") == (
        "https://example.com/a"
    )


def test_all_templates_compile_with_unified_environment() -> None:
    """统一环境应能编译现有与新增模板，避免迁移前破坏旧页面。"""
    for template_name in templates.env.list_templates():
        templates.env.get_template(template_name)


def test_refusal_message_preserves_reason_count_and_next_step_within_cookie_budget() -> None:
    """长标题和多任务不能让原因与下一步被 Cookie 限长截断，定位保留完整日期。"""
    reason = "只有已完成、已取消或已失效的任务可以归档"
    next_step = "请取消勾选仍在处理中的任务后重试"

    def labels(room_limit):
        """提供超长房间名，触发房名与点名条数预算。"""
        return [
            task_refusal_label(
                number,
                BusinessTaskType.MAINTENANCE,
                "很长的停用房源标题" * 20,
                date(2026, 10, 2),
                BusinessTaskStatus.PENDING_INSPECTION,
                room_limit,
            )
            for number in range(10000, 10010)
        ]

    message = refusal_message(reason, labels, 10, next_step)
    assert len(message) <= PAGE_MESSAGE_MAX_LENGTH
    assert reason in message and "以下 10 条不符合" in message and message.endswith(next_step)
    assert "任务 #10000·维修·" in message
    assert "2026年10月2日（待检查）" in message
    assert "另 7 条" in message
    assert "…" in message

    # 编号极长时放不下任何定位标签，保留原因、总数和下一步，不截断原句。
    fallback = refusal_message(reason, lambda limit: ["任务 #" + "9" * 300], 1, next_step)
    assert fallback == f"{reason}。共 1 条不符合。{next_step}"
    assert len(fallback) <= PAGE_MESSAGE_MAX_LENGTH
    compact = refusal_message(
        reason, lambda limit: ["很" * 300] if limit == 12 else ["短房间标签"], 1, next_step
    )
    assert "短房间标签" in compact and compact.endswith(next_step)
    request = SimpleNamespace(session={})
    set_page_error(request, message)
    set_page_notice(request, "好" * (PAGE_MESSAGE_MAX_LENGTH + 1))
    assert request.session["page_error"] == message
    assert len(request.session["page_notice"]) == PAGE_MESSAGE_MAX_LENGTH


def test_task_refusal_label_shows_missing_fields_without_internal_values() -> None:
    """缺房间和日期时明确说明待补齐，避免把空值或内部枚举直接交给管理员。"""
    assert task_refusal_label(
        12,
        BusinessTaskType.SUPPLIES,
        None,
        None,
        BusinessTaskStatus.PENDING_CONFIRMATION,
    ) == "任务 #12·补给·房间待确认·日期待补齐（待确认）"


def test_complaint_message_origin_and_risk_read_as_chinese() -> None:
    """AC15：客诉页的消息来源与风险等级显示中文；未知风险显示「待核实」，不被误读成低风险。"""
    from homestay_bot.domain.enums import MessageOrigin
    from homestay_bot.web import complaint_risk_zh, status_zh

    assert [status_zh(origin) for origin in MessageOrigin] == ["客人", "人工客服", "机器人"]
    assert [complaint_risk_zh(level) for level in ("critical", "high", "normal")] == [
        "严重",
        "高",
        "一般",
    ]
    assert complaint_risk_zh("something-new") == "待核实"
    assert complaint_risk_zh(None) == "待核实"
