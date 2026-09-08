"""房态主状态引擎：按订单事实 + 当地时刻推导「一眼看懂」的入住安排。

覆盖用户明确的营业规则：
- 下一位到店即视为上一位已退房（到店当天不再显示「退房待确认」）。
- 今日仅退房、无到店：过 15:00 默认按已退房进入下一轮。
- 同一客人连续订单为续住，不显示成周转。
- 只有 checkout_observed_on 等于退房日才算「已核验退房」，否则是「按计划」。
- 同步过期时一律「入住信息待核实」，不推断空房或在住。
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from homestay_bot.repositories.admin_operations import StayRecord
from homestay_bot.services.admin_operations_service import AdminOperationsService

TZ = ZoneInfo("Asia/Shanghai")


def _now(hour: int, minute: int = 0) -> datetime:
    """构造武汉当地 2026-09-09 的某个时刻。"""
    return datetime(2026, 9, 9, hour, minute, tzinfo=TZ)


def _stay(ci: str, co: str, *, cid: int | None = None, name: str | None = None,
          observed: str | None = None) -> StayRecord:
    """构造一笔订单事实。"""
    return StayRecord(
        property_id=1,
        check_in_date=date.fromisoformat(ci),
        check_out_date=date.fromisoformat(co),
        customer_id=cid,
        guest_name=name,
        checkout_observed_on=date.fromisoformat(observed) if observed else None,
    )


def status(now, stays, *, source_stale=False):
    """便捷调用状态引擎。"""
    return AdminOperationsService._primary_status(
        local_now=now, source_stale=source_stale, room_stays=list(stays)
    )


def test_arrival_implies_previous_checkout() -> None:
    """今日周转且不同客人：到店即算上一位已退房，主行是到店客人。"""
    stays = [
        _stay("2026-09-05", "2026-09-09", cid=1, name="张三"),  # 今日退房
        _stay("2026-09-09", "2026-09-12", cid=2, name="李四"),  # 今日到店
    ]
    r = status(_now(10), stays)  # 即使早于 12 点，只要有到店就算退房
    assert r.label == "今日到店"
    assert r.guest_name == "李四"
    assert r.is_consecutive is False
    assert "计划清洁" in r.schedule_line


def test_consecutive_same_guest_is_not_turnover() -> None:
    """同一客人连续订单为续住，不显示周转。"""
    stays = [
        _stay("2026-09-05", "2026-09-09", cid=7, name="王五"),
        _stay("2026-09-09", "2026-09-14", cid=7, name="王五"),
    ]
    r = status(_now(13), stays)
    assert r.label == "续住"
    assert r.guest_name == "王五"
    assert r.is_consecutive is True
    assert r.schedule_line == ""  # 续住不画周转条


def test_departure_only_before_15_shows_planned_checkout() -> None:
    """今日仅退房、15 点前、未核验：显示今日离店与计划退房。"""
    stays = [_stay("2026-09-06", "2026-09-09", cid=3, name="赵六")]
    r = status(_now(11), stays)
    assert r.label == "今日离店"
    assert r.guest_name == "赵六"
    assert r.checkout_verified is False


def test_departure_only_after_15_defaults_to_departed() -> None:
    """今日仅退房、过 15 点、未核验：默认按计划已退房进入下一轮。"""
    stays = [_stay("2026-09-06", "2026-09-09", cid=3, name="赵六")]
    r = status(_now(15, 1), stays)
    assert r.label == "按计划已退房"
    assert r.checkout_verified is False


def test_observed_checkout_is_verified_departure() -> None:
    """checkout_observed_on 等于退房日：算已核验退房，措辞不加「按计划」。"""
    stays = [_stay("2026-09-06", "2026-09-09", cid=3, name="赵六", observed="2026-09-09")]
    r = status(_now(11), stays)
    assert r.label == "已退房"
    assert r.checkout_verified is True


def test_arrival_only_shows_checkin() -> None:
    """今日仅到店：主行今日到店，计划条含 15:00 起入住。"""
    stays = [_stay("2026-09-09", "2026-09-12", cid=4, name="孙七")]
    r = status(_now(9), stays)
    assert r.label == "今日到店"
    assert r.guest_name == "孙七"
    assert "15:00" in r.schedule_line


def test_mid_stay_is_in_stay_period() -> None:
    """跨多日订单的中间日：住宿期内，不误报到离店。"""
    stays = [_stay("2026-09-06", "2026-09-12", cid=5, name="周八")]
    r = status(_now(14), stays)
    assert r.label == "住宿期内"
    assert r.guest_name == "周八"
    assert r.schedule_line == ""


def test_no_order_is_not_called_vacant_sellable() -> None:
    """今日无订单占用：中性表述，不叫近期空置、不断言可售。"""
    r = status(_now(12), [])
    assert r.label == "今日无订单占用"
    assert r.guest_name is None


def test_stale_source_defers_everything() -> None:
    """同步过期：一律待核实，不推断。"""
    stays = [_stay("2026-09-09", "2026-09-12", cid=6, name="吴九")]
    r = status(_now(16), stays, source_stale=True)
    assert r.label == "入住信息待核实"
    assert r.guest_name is None
