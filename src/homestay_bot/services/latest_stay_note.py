"""从客户订单候选中计算 CRM 展示用的最新入住备注。"""

from dataclasses import dataclass
from datetime import date, timedelta

from homestay_bot.domain.stay_status import (
    is_checked_out_stay_status,
    is_excluded_stay_status,
)

INVALID_DATE_RANGE_ERROR = "LATEST_STAY_INVALID_DATE_RANGE"
INVALID_OBSERVATION_ERROR = "LATEST_STAY_INVALID_OBSERVATION"
_FINISHED_STAY_RETENTION_DAYS = 3


@dataclass(frozen=True, slots=True)
class LatestStayCandidate:
    """承载选择入住备注所需的最小订单信息。"""

    order_id: int
    customer_id: int
    property_id: int
    property_title: str | None
    check_in_date: date
    check_out_date: date
    status: str | None
    checkout_observed_on: date | None = None


@dataclass(frozen=True, slots=True)
class LatestStayNoteResult:
    """返回展示备注及可供仓储层记录的脏数据摘要。"""

    note: str | None
    selected_order_id: int | None
    invalid_candidate_count: int = 0
    error_codes: tuple[str, ...] = ()


def _display_property_name(candidate: LatestStayCandidate) -> str:
    """将缺失及百居易占位房名转换为员工可理解的房间编号。"""

    title = (candidate.property_title or "").strip()
    if not title or title == f"百居易房间 {candidate.property_id}":
        return f"房间 #{candidate.property_id}"
    return title


def _format_note(candidate: LatestStayCandidate) -> str:
    """按 M.D-M.D房间名 格式生成不带前导零的短备注。"""

    check_in = candidate.check_in_date
    check_out = candidate.check_out_date
    return (
        f"{check_in.month}.{check_in.day}-"
        f"{check_out.month}.{check_out.day}{_display_property_name(candidate)}"
    )


def _result(
    candidate: LatestStayCandidate | None,
    *,
    invalid_candidate_count: int,
    error_codes: tuple[str, ...],
) -> LatestStayNoteResult:
    """把候选及脏数据计数收敛成稳定的服务返回结构。"""

    if candidate is None:
        return LatestStayNoteResult(
            note=None,
            selected_order_id=None,
            invalid_candidate_count=invalid_candidate_count,
            error_codes=error_codes,
        )
    return LatestStayNoteResult(
        note=_format_note(candidate),
        selected_order_id=candidate.order_id,
        invalid_candidate_count=invalid_candidate_count,
        error_codes=error_codes,
    )


def _finished_anchor(candidate: LatestStayCandidate) -> date:
    """优先用实际退房观察日，旧订单则回退到计划退房日。"""

    return candidate.checkout_observed_on or candidate.check_out_date


def select_latest_stay_note(
    candidates: list[LatestStayCandidate],
    *,
    today: date,
) -> LatestStayNoteResult:
    """按当前入住、退房观察期、未来订单和历史订单的优先级选择备注。"""

    valid: list[LatestStayCandidate] = []
    invalid_count = 0
    invalid_date_range = False
    invalid_observation = False
    for candidate in candidates:
        # 日期倒置的数据不能参与区间判断，否则可能让 CRM 展示错误订单。
        if candidate.check_out_date <= candidate.check_in_date:
            invalid_count += 1
            invalid_date_range = True
            continue
        # 退房观察日必须发生在入住后且不得晚于查询日，矛盾终态不可展示。
        if (
            is_checked_out_stay_status(candidate.status)
            and candidate.checkout_observed_on is not None
            and not (
                candidate.check_in_date
                <= candidate.checkout_observed_on
                <= today
            )
        ):
            invalid_count += 1
            invalid_observation = True
            continue
        if not is_excluded_stay_status(candidate.status):
            valid.append(candidate)

    error_codes = tuple(
        code
        for code, present in (
            (INVALID_DATE_RANGE_ERROR, invalid_date_range),
            (INVALID_OBSERVATION_ERROR, invalid_observation),
        )
        if present
    )

    # 当前入住永远优先；明确已退房的订单不得仅凭计划日期伪装成当前入住。
    current = [
        candidate
        for candidate in valid
        if not is_checked_out_stay_status(candidate.status)
        and candidate.check_in_date <= today < candidate.check_out_date
    ]
    if current:
        selected = max(current, key=lambda item: (item.check_in_date, item.order_id))
        return _result(
            selected,
            invalid_candidate_count=invalid_count,
            error_codes=error_codes,
        )

    # 已结束候选既包含日期已经过去的订单，也包含已记录实际退房日的提前退房订单。
    past = [
        candidate
        for candidate in valid
        if candidate.check_out_date <= today
        or (
            is_checked_out_stay_status(candidate.status)
            and candidate.checkout_observed_on is not None
            and candidate.checkout_observed_on <= today
        )
    ]
    latest_past = (
        max(
            past,
            key=lambda item: (
                _finished_anchor(item),
                item.check_in_date,
                item.order_id,
            ),
        )
        if past
        else None
    )

    # 观察期含实际退房当天及其后三天，避免刚退房就被未来订单覆盖。
    if latest_past is not None and today <= (
        _finished_anchor(latest_past) + timedelta(days=_FINISHED_STAY_RETENTION_DAYS)
    ):
        return _result(
            latest_past,
            invalid_candidate_count=invalid_count,
            error_codes=error_codes,
        )

    future = [
        candidate
        for candidate in valid
        if not is_checked_out_stay_status(candidate.status)
        and candidate.check_in_date > today
    ]
    if future:
        selected = min(future, key=lambda item: (item.check_in_date, item.order_id))
        return _result(
            selected,
            invalid_candidate_count=invalid_count,
            error_codes=error_codes,
        )

    # 没有后续入住时继续保留最新历史订单，保证客户档案始终有可用上下文。
    return _result(
        latest_past,
        invalid_candidate_count=invalid_count,
        error_codes=error_codes,
    )
