from collections.abc import Callable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


def wuhan_today() -> date:
    """返回武汉时区当前自然日，供生产住宿日期边界统一使用。"""
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def _strict_date(value: str | date, *, field_name: str) -> date:
    """只接受日期对象或严格 ISO 日期字符串。"""
    if isinstance(value, datetime):
        raise ValueError(f"{field_name} 必须是日期")
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError(f"{field_name} 必须是 YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field_name} 必须是有效日期") from error
    if parsed.isoformat() != value:
        raise ValueError(f"{field_name} 必须是 YYYY-MM-DD")
    return parsed


def validate_stay_date_range(
    check_in_date: str | date,
    check_out_date: str | date,
    *,
    today_provider: Callable[[], date] = wuhan_today,
    max_advance_days: int = 365,
    max_stay_days: int = 30,
) -> tuple[date, date]:
    """在外部查询前统一校验入住日、退房日和最大住宿范围。"""
    check_in = _strict_date(check_in_date, field_name="入住日期")
    check_out = _strict_date(check_out_date, field_name="退房日期")
    today = today_provider()
    if check_in < today or check_in > today + timedelta(days=max_advance_days):
        raise ValueError("入住日期超出范围")
    if check_out <= check_in or check_out - check_in > timedelta(days=max_stay_days):
        raise ValueError("退房日期超出范围")
    return check_in, check_out
