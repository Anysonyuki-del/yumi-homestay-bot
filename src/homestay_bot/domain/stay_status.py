"""统一处理来自百居易及本地订单的入住状态。"""

from collections.abc import Collection
from datetime import date

_CHECKED_OUT_STATUSES: frozenset[str] = frozenset({"checked_out", "completed"})
_EXCLUDED_STATUSES: frozenset[str] = frozenset(
    {"cancelled", "canceled", "declined", "expired", "deleted"}
)


def normalize_stay_status(status: str | None) -> str:
    """将外部状态转为去空白的小写值，避免渠道格式差异影响业务判断。"""

    return (status or "").strip().lower()


def _has_normalized_status(status: str | None, statuses: Collection[str]) -> bool:
    """在一组已归一化状态中判断外部状态。"""

    return normalize_stay_status(status) in statuses


def is_checked_out_stay_status(status: str | None) -> bool:
    """判断订单是否已明确完成退房。"""

    return _has_normalized_status(status, _CHECKED_OUT_STATUSES)


def is_excluded_stay_status(status: str | None) -> bool:
    """判断订单是否因取消、拒绝或删除而不应参与入住备注计算。"""

    return _has_normalized_status(status, _EXCLUDED_STATUSES)


def is_current_stay(
    status: str | None, check_in_date: date, check_out_date: date, today: date
) -> bool:
    """判断订单是否是「当前或即将入住」的有效住宿。

    未取消、未退房、日期合法，且退房日在今天之后。给模型的「进行中订单」与住宿确认
    必须共用这一个判定：1.42.0 两处口径不一（前者不看日期），过期订单被当成进行中，
    住宿确认却找不到它，于是每条回复都追加「暂未找到有效订单」。
    """
    return (
        not is_excluded_stay_status(status)
        and not is_checked_out_stay_status(status)
        and check_in_date < check_out_date
        and check_out_date > today
    )
