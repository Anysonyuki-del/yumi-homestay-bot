"""住宿条几何与轨道分配（Spec §6）：计划 15:00/12:00 半天边界、非重叠复用轨道。"""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from homestay_bot.services.admin_operations_service import (
    AdminOperationsService,
    RoomStayInterval,
)

TZ = ZoneInfo("Asia/Shanghai")


def _iv(oid, ci, co, *, cid=None, name="客人示例", verified=False):
    return RoomStayInterval(
        order_id=oid,
        customer_id=cid,
        guest_name=name,
        check_in=date.fromisoformat(ci),
        check_out=date.fromisoformat(co),
        checkout_verified=verified,
    )


def _bars(intervals, *, days=6, w0_date="2026-09-07", now="2026-09-09T04:00:00"):
    w0 = datetime.combine(date.fromisoformat(w0_date), time(0, 0), tzinfo=TZ)
    return AdminOperationsService._timeline_bars(
        intervals=tuple(intervals),
        w0=w0,
        total_columns=days,
        span_hours=24.0 * days,
        local_now=datetime.fromisoformat(now).replace(tzinfo=TZ),
    )


def test_v02_single_night_geometry_matches_half_day_boundaries() -> None:
    """V02：9/8 15:00 → 9/9 12:00 一晚。宽度=21h/(24×6)，104px 日宽下≈91px。"""
    bars, lanes, overlap, anomaly = _bars([_iv(1, "2026-09-08", "2026-09-09")])
    b = bars[0]
    # 入住在 9/8（窗口第 2 天）15:00：left = (24 + 15) / 144 * 100
    assert abs(b.left_pct - (39 / 144 * 100)) < 0.01
    # 宽度 21 小时：21 / 144 * 100
    assert abs(b.width_pct - (21 / 144 * 100)) < 0.01
    # 104px 日宽 → 内容宽 624px，宽度像素 ≈ 91
    assert abs(b.width_pct / 100 * 624 - 91) < 1
    assert b.lane == 0 and lanes == 1 and not overlap and not anomaly


def test_v03_same_day_turnover_shares_lane_with_three_hour_gap() -> None:
    """V03：同日换客，前单 12:00 结束、后单 15:00 开始，同轨道，间隔 3h。"""
    bars, lanes, overlap, _ = _bars([
        _iv(1, "2026-09-08", "2026-09-09", cid=1, name="甲"),
        _iv(2, "2026-09-09", "2026-09-10", cid=2, name="乙"),
    ])
    assert lanes == 1 and not overlap
    assert all(b.lane == 0 for b in bars)
    a, c = bars[0], bars[1]
    gap = c.left_pct - (a.left_pct + a.width_pct)
    assert abs(gap - (3 / 144 * 100)) < 0.01  # 3 小时空隙


def test_v01_three_consecutive_orders_reuse_one_lane() -> None:
    """V01：三笔无重叠连续订单同一轨道，不形成楼梯。"""
    bars, lanes, overlap, _ = _bars([
        _iv(1, "2026-09-08", "2026-09-09", cid=1, name="甲"),
        _iv(2, "2026-09-09", "2026-09-10", cid=2, name="乙"),
        _iv(3, "2026-09-10", "2026-09-11", cid=3, name="丙"),
    ])
    assert lanes == 1 and not overlap
    assert [b.lane for b in bars] == [0, 0, 0]


def test_v04_real_overlap_adds_lane_and_flags() -> None:
    """V04：真实重叠才新增轨道并标记核对。"""
    bars, lanes, overlap, _ = _bars([
        _iv(1, "2026-09-08", "2026-09-11", cid=1, name="甲"),
        _iv(2, "2026-09-09", "2026-09-10", cid=2, name="乙"),
    ])
    assert lanes == 2 and overlap
    assert any(b.overlaps for b in bars)


def test_v05_cross_window_clips_but_keeps_nights() -> None:
    """V05：跨窗口住宿裁切并标延续，夜数保持真实。"""
    bars, lanes, _, _ = _bars([_iv(1, "2026-09-04", "2026-09-20")])
    b = bars[0]
    assert b.left_continues and b.right_continues
    assert b.left_pct == 0.0
    assert abs((b.left_pct + b.width_pct) - 100.0) < 0.01
    assert b.nights == 16  # 真实夜数不因裁切改变


def test_anomaly_checkout_not_after_checkin_is_flagged_not_faked() -> None:
    """退房不晚于入住：标记异常，不伪造几何。"""
    bars, lanes, overlap, anomaly = _bars([_iv(1, "2026-09-09", "2026-09-09")])
    assert anomaly and bars == []
