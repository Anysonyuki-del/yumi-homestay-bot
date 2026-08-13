"""统一处理来自百居易及本地订单的入住状态。"""

from collections.abc import Collection

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
