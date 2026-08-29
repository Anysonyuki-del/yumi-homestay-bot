from datetime import UTC, date, datetime

from homestay_bot.domain.enums import (
    ApprovalStatus,
    RoomOccupancyStatus,
    RoomOperationalStatus,
    TaskClosureReason,
)
from homestay_bot.web import templates


def test_templates_register_safe_chinese_helpers() -> None:
    """统一模板环境应提供中文状态和本地日期助手。"""
    environment = templates.env

    assert environment.filters["status_zh"](ApprovalStatus.PENDING) == "待审批"
    assert environment.filters["enum_zh"](ApprovalStatus.PENDING) == "待审批"
    assert environment.filters["status_zh"](RoomOperationalStatus.READY) == "可入住"
    assert environment.filters["status_zh"](RoomOccupancyStatus.UNKNOWN) == "房态待确认"
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
