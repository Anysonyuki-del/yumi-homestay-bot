"""房间事件引擎：以订单为最小单位，绑定同一订单的身份、区间与计划绝对时刻。

覆盖 Spec §5 选取规则与 §6 周转间隔的服务端部分（倒计时文本由前端计算，另测）。
姓名与日期绝不跨订单拼接；12:00/15:00 为计划节点。
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from homestay_bot.repositories.admin_operations import StayRecord
from homestay_bot.services.admin_operations_service import AdminOperationsService

TZ = ZoneInfo("Asia/Shanghai")


def _now(h: int, m: int = 0) -> datetime:
    return datetime(2026, 9, 9, h, m, tzinfo=TZ)


def _stay(oid, ci, co, *, cid=None, name=None, observed=None) -> StayRecord:
    return StayRecord(
        property_id=1,
        check_in_date=date.fromisoformat(ci),
        check_out_date=date.fromisoformat(co),
        customer_id=cid,
        guest_name=name,
        checkout_observed_on=date.fromisoformat(observed) if observed else None,
        order_id=oid,
    )


def ev(now, stays):
    return AdminOperationsService._room_events(local_now=now, room_stays=list(stays))


def test_a03_checkout_is_departing_guest_not_incoming_or_future() -> None:
    """A03：今日 A 退房、B 到店、明天 C 到店 —— 退房=A，今日入住=B，不拼 C。"""
    stays = [
        _stay(10, "2026-09-08", "2026-09-09", cid=1, name="客人A"),
        _stay(11, "2026-09-09", "2026-09-10", cid=2, name="客人B"),
        _stay(12, "2026-09-10", "2026-09-11", cid=3, name="客人C"),
    ]
    r = ev(_now(4), stays)
    assert r.checkout is not None and r.checkout.order_id == 10 and r.checkout.guest_name == "客人A"
    assert r.checkin is not None and r.checkin.order_id == 11 and r.checkin.guest_name == "客人B"
    # 退房计划 12:00、入住计划 15:00，均为今天。
    assert r.checkout.target == _now(12)
    assert r.checkin.target == _now(15)


def test_a04_turnover_gap_is_three_hours_same_day_diff_guest() -> None:
    """A04：同日换客，计划周转间隔 = 15:00 − 12:00 = 180 分钟。"""
    stays = [
        _stay(10, "2026-09-08", "2026-09-09", cid=1, name="客人A"),
        _stay(11, "2026-09-09", "2026-09-10", cid=2, name="客人B"),
    ]
    r = ev(_now(4), stays)
    assert r.turnover_gap_minutes == 180
    assert r.is_consecutive is False


def test_a07_verified_checkout_has_no_fake_time() -> None:
    """A07：有退房核验日期但无时刻 —— 标记已核验，target 仍是计划 12:00 不伪造。"""
    stays = [_stay(10, "2026-09-06", "2026-09-09", cid=1, name="客人A", observed="2026-09-09")]
    r = ev(_now(11), stays)
    assert r.checkout is not None and r.checkout.verified is True


def test_a09_arrival_only_has_no_current_checkout() -> None:
    """A09：今天只有到店、无当前住宿 —— 不虚构当前住客退房行。"""
    stays = [_stay(11, "2026-09-09", "2026-09-12", cid=2, name="客人B")]
    r = ev(_now(9), stays)
    assert r.checkout is None
    assert r.checkin is not None and r.checkin.order_id == 11


def test_a10_consecutive_same_customer_is_not_turnover() -> None:
    """A10：相邻订单同一客户 —— 续住，不计算周转间隔。"""
    stays = [
        _stay(10, "2026-09-06", "2026-09-09", cid=7, name="客人W"),
        _stay(11, "2026-09-09", "2026-09-14", cid=7, name="客人W"),
    ]
    r = ev(_now(13), stays)
    assert r.is_consecutive is True
    assert r.turnover_gap_minutes is None


def test_a12_overlapping_candidates_are_ambiguous() -> None:
    """A12：同日多笔候选、区间重叠 —— 标记有多笔，不任取一位当真实住客。"""
    stays = [
        _stay(10, "2026-09-08", "2026-09-09", cid=1, name="客人A"),
        _stay(20, "2026-09-07", "2026-09-11", cid=9, name="客人Z"),  # 覆盖今天且早于今天开始
    ]
    r = ev(_now(4), stays)
    assert r.checkout is not None and r.checkout.ambiguous is True
    assert r.checkout.guest_name is None


def test_mid_stay_checkout_targets_future_noon() -> None:
    """跨多日住宿覆盖今天：退房事件取该订单未来退房日 12:00。"""
    stays = [_stay(10, "2026-09-06", "2026-09-12", cid=1, name="客人A")]
    r = ev(_now(14), stays)
    assert r.checkout is not None
    assert r.checkout.target == datetime(2026, 9, 12, 12, 0, tzinfo=TZ)


def test_intervals_carry_each_order_once_with_nights() -> None:
    """住宿条：每笔订单一段，晚数=退房日−入住日，身份随订单。"""
    stays = [
        _stay(10, "2026-09-08", "2026-09-09", cid=1, name="客人A"),
        _stay(11, "2026-09-09", "2026-09-11", cid=2, name="客人B"),
    ]
    r = ev(_now(4), stays)
    assert [i.order_id for i in r.intervals] == [10, 11]
    assert [i.nights for i in r.intervals] == [1, 2]


def test_event_labels_and_status_words_are_server_built() -> None:
    """绝对文案与初始状态词由服务端生成；退房「计划」、入住「起入住」。"""
    stays = [
        _stay(10, "2026-09-08", "2026-09-09", cid=1, name="客人A"),
        _stay(11, "2026-09-09", "2026-09-10", cid=2, name="客人B"),
    ]
    r = ev(_now(4), stays)
    assert r.checkout.target_label == "今天 12:00（计划）"
    assert r.checkout.status_word == "距计划退房"
    assert r.checkin.target_label == "今天 15:00 起入住"
    assert r.checkin.status_word == "距可入住时间"
    # 过了 12:00 未核验：退房待确认。
    r2 = ev(_now(12, 30), stays)
    assert r2.checkout.status_word == "退房待确认"
    # 过了 15:00：到店待确认。
    r3 = ev(_now(15, 30), stays)
    assert r3.checkin.status_word == "到店待确认"
    # 明天到店：绝对文案用「明天」。
    stays_future = [_stay(12, "2026-09-10", "2026-09-11", cid=3, name="客人C")]
    r4 = ev(_now(4), stays_future)
    assert r4.checkin.target_label == "明天 15:00 起入住"


def _items_for(now, stays):
    """跑 _merge + events，返回合并后的事件（模拟 _room_items 的合并步骤）。"""
    merged = AdminOperationsService._merge_consecutive_stays(list(stays))
    return AdminOperationsService._room_events(local_now=now, room_stays=merged), merged


def test_adjacent_same_name_orders_merge_into_one_stay() -> None:
    """同名相邻订单（客户号不同）合并为一段连续住宿，不显示换客周转。"""
    stays = [
        _stay(156, "2026-09-08", "2026-09-09", cid=156, name="续住客人"),
        _stay(157, "2026-09-09", "2026-09-10", cid=157, name="续住客人"),
    ]
    r, merged = _items_for(_now(11, 58), stays)
    # 合并成一段 9/8–9/10。
    assert len(merged) == 1
    assert merged[0].check_in_date.isoformat() == "2026-09-08"
    assert merged[0].check_out_date.isoformat() == "2026-09-10"
    # 今天 9/9 落在中间：住宿期内，无今日到店/离店、无周转间隔。
    assert r.turnover_gap_minutes is None
    assert r.is_consecutive is False  # 已合并为一段，不再是两单相接
    assert len(r.intervals) == 1 and r.intervals[0].nights == 2
    # 退房事件指向合并后的未来退房日 9/10 12:00。
    assert r.checkout is not None
    assert r.checkout.target == datetime(2026, 9, 10, 12, 0, tzinfo=TZ)


def test_different_guests_back_to_back_are_not_merged() -> None:
    """不同客人的背靠背订单仍是周转，不被误合并。"""
    stays = [
        _stay(10, "2026-09-08", "2026-09-09", cid=1, name="客人甲"),
        _stay(11, "2026-09-09", "2026-09-10", cid=2, name="客人乙"),
    ]
    _, merged = _items_for(_now(4), stays)
    assert len(merged) == 2
