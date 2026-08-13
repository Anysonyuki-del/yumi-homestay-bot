from datetime import date, timedelta

import pytest

from homestay_bot.domain.stay_status import (
    is_checked_out_stay_status,
    is_excluded_stay_status,
    normalize_stay_status,
)
from homestay_bot.services.latest_stay_note import (
    LatestStayCandidate,
    select_latest_stay_note,
)


def _candidate(
    *,
    order_id: int = 1,
    property_id: int = 101,
    property_title: str | None = "春和景明",
    check_in: date = date(2026, 8, 14),
    check_out: date = date(2026, 8, 16),
    status: str | None = "confirmed",
    observed_on: date | None = None,
) -> LatestStayCandidate:
    """构造只包含单元测试关心字段的入住候选。"""

    return LatestStayCandidate(
        order_id=order_id,
        customer_id=9,
        property_id=property_id,
        property_title=property_title,
        check_in_date=check_in,
        check_out_date=check_out,
        status=status,
        checkout_observed_on=observed_on,
    )


def test_normalize_and_classify_stay_status() -> None:
    """外部状态大小写和空白不应影响退房及排除判定。"""

    assert normalize_stay_status(" Completed ") == "completed"
    assert normalize_stay_status(None) == ""
    assert is_checked_out_stay_status("CHECKED_OUT") is True
    assert is_checked_out_stay_status("completed") is True
    assert is_checked_out_stay_status("confirmed") is False
    assert is_excluded_stay_status(" Canceled ") is True
    assert is_excluded_stay_status("cancelled") is True
    assert is_excluded_stay_status("confirmed") is False


def test_current_stay_wins_and_formats_without_leading_zero() -> None:
    """入住区间内应展示当前入住，并生成适合员工阅读的短备注。"""

    result = select_latest_stay_note(
        [
            _candidate(
                order_id=1,
                check_in=date(2026, 8, 1),
                check_out=date(2026, 8, 3),
            ),
            _candidate(order_id=2),
            _candidate(
                order_id=3,
                check_in=date(2026, 8, 20),
                check_out=date(2026, 8, 22),
            ),
        ],
        today=date(2026, 8, 14),
    )

    assert result.note == "8.14-8.16春和景明"
    assert result.selected_order_id == 2
    assert result.invalid_candidate_count == 0
    assert result.error_codes == ()


def test_latest_current_stay_uses_order_id_as_final_tie_breaker() -> None:
    """同时入住时先选更晚入住，日期相同再选更大的订单 ID。"""

    result = select_latest_stay_note(
        [
            _candidate(
                order_id=10,
                property_title="较早入住",
                check_in=date(2026, 8, 13),
                check_out=date(2026, 8, 17),
            ),
            _candidate(order_id=11, property_title="同日小订单"),
            _candidate(order_id=12, property_title="同日大订单"),
        ],
        today=date(2026, 8, 14),
    )

    assert result.note == "8.14-8.16同日大订单"
    assert result.selected_order_id == 12


def test_checked_out_status_is_not_treated_as_current_stay() -> None:
    """即使计划日期仍覆盖今天，已退房状态也不能抢占真实当前入住。"""

    result = select_latest_stay_note(
        [
            _candidate(order_id=1, property_title="已提前退房", status="completed"),
            _candidate(
                order_id=2,
                property_title="下一笔",
                check_in=date(2026, 8, 20),
                check_out=date(2026, 8, 21),
            ),
        ],
        today=date(2026, 8, 14),
    )

    assert result.note == "8.20-8.21下一笔"
    assert result.selected_order_id == 2


@pytest.mark.parametrize("days_after_checkout", [0, 1, 2, 3])
def test_finished_stay_is_retained_through_third_day(
    days_after_checkout: int,
) -> None:
    """实际退房日起保留到第三天，第四天才切换到下一笔订单。"""

    finished = _candidate(
        order_id=1,
        check_in=date(2026, 8, 10),
        check_out=date(2026, 8, 12),
        status="checked_out",
        observed_on=date(2026, 8, 12),
    )
    future = _candidate(
        order_id=2,
        property_title="下一站",
        check_in=date(2026, 8, 20),
        check_out=date(2026, 8, 22),
    )

    retained = select_latest_stay_note(
        [finished, future],
        today=date(2026, 8, 12) + timedelta(days=days_after_checkout),
    )

    assert retained.note == "8.10-8.12春和景明"


def test_finished_stay_switches_to_future_on_fourth_day() -> None:
    """实际退房后的第四天才允许显示最近一笔未来订单。"""
    finished = _candidate(
        order_id=1,
        check_in=date(2026, 8, 10),
        check_out=date(2026, 8, 12),
        status="checked_out",
        observed_on=date(2026, 8, 12),
    )
    future = _candidate(
        order_id=2,
        property_title="下一站",
        check_in=date(2026, 8, 20),
        check_out=date(2026, 8, 22),
    )

    switched = select_latest_stay_note(
        [finished, future], today=date(2026, 8, 16)
    )

    assert switched.note == "8.20-8.22下一站"


def test_future_completed_order_with_impossible_observation_is_rejected() -> None:
    """退房观察日早于入住日的矛盾终态订单不得显示在 CRM。"""
    result = select_latest_stay_note(
        [
            _candidate(
                order_id=1,
                check_in=date(2026, 8, 20),
                check_out=date(2026, 8, 22),
                status="completed",
                observed_on=date(2026, 8, 14),
            )
        ],
        today=date(2026, 8, 14),
    )

    assert result.note is None
    assert result.invalid_candidate_count == 1
    assert result.error_codes == ("LATEST_STAY_INVALID_OBSERVATION",)


def test_latest_past_stay_remains_when_there_is_no_future_stay() -> None:
    """超过观察期但没有后续订单时，仍保留最近一次有效入住。"""

    result = select_latest_stay_note(
        [
            _candidate(
                order_id=1,
                check_in=date(2026, 7, 1),
                check_out=date(2026, 7, 3),
            ),
            _candidate(
                order_id=2,
                check_in=date(2026, 8, 1),
                check_out=date(2026, 8, 3),
            ),
        ],
        today=date(2026, 8, 14),
    )

    assert result.note == "8.1-8.3春和景明"
    assert result.selected_order_id == 2


def test_cancelled_future_stays_are_ignored() -> None:
    """已取消及失效订单不得成为未来备注，需回退到有效候选。"""

    result = select_latest_stay_note(
        [
            _candidate(
                order_id=1,
                property_title="已取消",
                check_in=date(2026, 8, 17),
                check_out=date(2026, 8, 18),
                status=" CANCELLED ",
            ),
            _candidate(
                order_id=2,
                property_title="有效房间",
                check_in=date(2026, 8, 20),
                check_out=date(2026, 8, 21),
            ),
        ],
        today=date(2026, 8, 14),
    )

    assert result.note == "8.20-8.21有效房间"
    assert result.selected_order_id == 2


def test_nearest_future_stay_uses_smaller_order_id_as_tie_breaker() -> None:
    """未来订单同日入住时使用较小订单 ID，保证选择稳定且符合最近排序。"""

    result = select_latest_stay_note(
        [
            _candidate(
                order_id=20,
                property_title="大订单",
                check_in=date(2026, 8, 20),
                check_out=date(2026, 8, 21),
            ),
            _candidate(
                order_id=10,
                property_title="小订单",
                check_in=date(2026, 8, 20),
                check_out=date(2026, 8, 22),
            ),
        ],
        today=date(2026, 8, 14),
    )

    assert result.note == "8.20-8.22小订单"
    assert result.selected_order_id == 10


def test_missing_or_hostex_placeholder_title_uses_property_id() -> None:
    """房源名缺失或仍是百居易占位名时，应显示稳定易懂的房间编号。"""

    missing = select_latest_stay_note(
        [_candidate(property_id=88, property_title=None)],
        today=date(2026, 8, 14),
    )
    placeholder = select_latest_stay_note(
        [_candidate(property_id=99, property_title="百居易房间 99")],
        today=date(2026, 8, 14),
    )

    assert missing.note == "8.14-8.16房间 #88"
    assert placeholder.note == "8.14-8.16房间 #99"


def test_invalid_dates_are_skipped_with_stable_error_summary() -> None:
    """退房不晚于入住的脏数据不能让 CRM 崩溃，并需提供稳定错误摘要。"""

    result = select_latest_stay_note(
        [
            _candidate(
                order_id=1,
                check_in=date(2026, 8, 16),
                check_out=date(2026, 8, 16),
            ),
            _candidate(
                order_id=2,
                check_in=date(2026, 8, 18),
                check_out=date(2026, 8, 17),
            ),
        ],
        today=date(2026, 8, 14),
    )

    assert result.note is None
    assert result.selected_order_id is None
    assert result.invalid_candidate_count == 2
    assert result.error_codes == ("LATEST_STAY_INVALID_DATE_RANGE",)


def test_empty_candidates_return_an_empty_result() -> None:
    """客户没有任何订单时返回空备注，而不是异常。"""

    result = select_latest_stay_note([], today=date(2026, 8, 14))

    assert result.note is None
    assert result.selected_order_id is None
    assert result.invalid_candidate_count == 0
    assert result.error_codes == ()
