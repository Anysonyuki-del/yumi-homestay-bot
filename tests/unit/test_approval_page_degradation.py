"""审批详情在百居易参考数据不可用时仍须可看、可拒。"""

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from homestay_bot.domain.enums import ApprovalStatus
from homestay_bot.services.approval_page_service import ApprovalPageService


class _Approval:
    """最小审批单，只带页面与降级判定用得到的字段。"""

    def __init__(self, status: ApprovalStatus) -> None:
        """构造一条指定状态的审批单。"""
        self.id = 1
        self.approval_code = "AP-1"
        self.status = status
        self.guest_name = "客人"
        self.check_in_date = date(2026, 9, 10)
        self.check_out_date = date(2026, 9, 12)
        self.number_of_guests = 2
        self.room_type_preference = "大床房"
        self.special_requests = None
        self.created_at = None


class _Session:
    """提供审批和空审计查询，专注参考数据失败时的降级行为。"""

    def __init__(self, approval: object) -> None:
        """保存要返回的审批单。"""
        self._approval = approval

    async def get(self, model: object, approval_id: int) -> object:
        """按主键返回预置审批单。"""
        return self._approval

    async def scalars(self, statement: object) -> list[Any]:
        """这些参考数据降级用例不包含迟到审计；实际查询由浏览器回归覆盖。"""
        return []


class _Hostex:
    """可分别注入三个接口失败的百居易替身。"""

    def __init__(self, failing: set[str] | None = None) -> None:
        """记录哪些接口应当抛错，并统计调用。"""
        self.failing = failing or set()
        self.calls: list[str] = []

    async def list_properties(self) -> list[Any]:
        """房源字典。"""
        self.calls.append("properties")
        if "properties" in self.failing:
            raise TimeoutError("上游超时")
        return []

    async def list_reference_prices(self, start_date, end_date) -> list[Any]:
        """区间参考价。"""
        self.calls.append("prices")
        if "prices" in self.failing:
            raise TimeoutError("上游超时")
        return []

    async def list_income_methods(self) -> list[Any]:
        """收入方式。"""
        self.calls.append("income")
        if "income" in self.failing:
            raise TimeoutError("上游超时")
        return []


class _Sensitive:
    """返回固定脱敏来源。"""

    def read(self, approval: object) -> SimpleNamespace:
        """返回带手机号的敏感数据。"""
        return SimpleNamespace(
            guest_mobile="13800138000",
            guest_name="客人",
            special_requests=None,
        )


def _service(hostex: _Hostex, approval: object) -> ApprovalPageService:
    """按依赖构造被测服务。"""
    return ApprovalPageService(
        session=_Session(approval),
        hostex=hostex,
        booking=SimpleNamespace(),
        sensitive_data=_Sensitive(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", ["properties", "prices", "income"])
async def test_detail_survives_any_single_reference_failure(broken: str) -> None:
    """三个参考接口各自失败时，本地审批仍要能看，且只禁用确认下单。

    此前 get_detail 无条件顺序 await 三个接口且无降级，上游一挂，员工连已有的
    审批基本信息和本地拒绝入口都打不开——查看历史审批也白白承担外部依赖。
    """
    approval = _Approval(ApprovalStatus.PENDING)
    service = _service(_Hostex({broken}), approval)

    detail = await service.get_detail(1)

    assert detail["approval"].approval_code == "AP-1"
    assert detail["masked_mobile"] == "13800138000"  # 1.41.0 起后台显示完整手机号
    # 只有失败的那一项被标记，不因为一个挂了就整体作废。
    assert len(detail["reference_unavailable"]) == 1
    # 下单依赖实时房态与价格，缺任何一项都不许确认；绝不用旧数据兜底放行。
    assert detail["can_confirm"] is False


@pytest.mark.asyncio
async def test_detail_allows_confirmation_when_all_reference_data_is_present() -> None:
    """参考数据齐备时确认路径不受影响。"""
    service = _service(_Hostex(), _Approval(ApprovalStatus.PENDING))

    detail = await service.get_detail(1)

    assert detail["reference_unavailable"] == []
    assert detail["can_confirm"] is True


@pytest.mark.asyncio
async def test_reference_groups_keep_room_identity_and_unmatched_prices() -> None:
    """同渠道撞号不猜房间，跨渠道同编号仍可区分，空房和未知币种明确展示。"""
    from homestay_bot.integrations.hostex_client import ListingCalendarDay, Property

    class Catalog(_Hostex):
        async def list_properties(self):
            """合成跨渠道同号、重复归属及无渠道房间。"""
            return [Property(id=1, title="庭院", channels=[
                {"channel_type": "booking_site", "listing_id": "same", "currency": "CNY"},
                {"channel_type": "airbnb", "listing_id": "ambiguous"},
            ]), Property(id=2, title="江景", channels=[
                {"channel_type": "airbnb", "listing_id": "same", "currency": "USD"},
                {"channel_type": "airbnb", "listing_id": "ambiguous"},
            ]), Property(id=3, title="空房")]

        async def list_reference_prices(self, start_date, end_date):
            """每种身份各一行，包含不能归属的价格。"""
            return [ListingCalendarDay(channel_type=channel, listing_id=listing,
                date=start_date, price=price, inventory=1) for channel, listing, price in [
                    ("booking_site", "same", 300), ("airbnb", "same", 100),
                    ("airbnb", "ambiguous", 90), ("other", "missing", 80)]]

    detail = await _service(Catalog(), _Approval(ApprovalStatus.PENDING)).get_detail(1)
    groups = detail["reference_price_groups"]
    assert [(g["title"], [r["price"] for r in g["prices"]]) for g in groups] == [
        ("庭院", [300]), ("江景", [100]), ("空房", [])]
    assert groups[1]["prices"][0]["currency_label"] == "USD"
    assert [r["price"] for r in detail["unmatched_reference_prices"]] == [90, 80]
    assert detail["can_confirm"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("channel,currency_label,currency_note,quote_allowed", [
    ({"channel_type": "booking_site"}, "¥", "直订默认人民币", True),
    ({"channel_type": "booking_site", "currency": None}, "¥", "直订默认人民币", True),
    ({"channel_type": "booking_site", "currency": "CNY"}, "¥", None, True),
    ({"channel_type": "booking_site", "currency": "USD"}, "USD", None, False),
    ({"channel_type": "booking_site", "currency": ""}, "币种未确认", None, False),
    ({"channel_type": "airbnb"}, "币种未确认", None, False),
    ({"channel_type": "airbnb", "currency": None}, "币种未确认", None, False),
    ({"channel_type": "airbnb", "currency": "CNY"}, "¥", None, True),
    ({"channel_type": "airbnb", "currency": "USD"}, "USD", None, False),
    ({"channel_type": "airbnb", "currency": ""}, "币种未确认", None, False),
])
async def test_reference_currency_policy_matches_guest_quote_and_approval_display(
    channel, currency_label, currency_note, quote_allowed,
) -> None:
    """同一渠道资料经两个正式调用方，缺币种直订可报价且标注，未知 OTA 不冒充人民币。"""
    from homestay_bot.integrations.deepseek_client import HostexReadOnlyToolExecutor
    from homestay_bot.integrations.hostex_client import (
        ListingCalendarDay,
        Property,
        PropertyAvailability,
    )

    class Catalog(_Hostex):
        async def list_properties(self):
            """保留字段缺失、null 与空字符串，走实际模型解析和页面投影。"""
            return [Property(id=101, title="合成房间", channels=[
                {"listing_id": "price", **channel},
            ])]

        async def list_reference_prices(self, start_date, end_date):
            """只返回合成渠道夜价，不涉及真实上游。"""
            return [ListingCalendarDay(channel_type=channel["channel_type"], listing_id="price",
                date=start_date, price=399, inventory=1)]

        async def list_availabilities(self, property_ids, start_date, end_date):
            """完整一晚合成库存使报价能经过正式工具入口。"""
            return [PropertyAvailability(property_id=101,
                days=[{"date": start_date, "available": True}])]

    hostex = Catalog()
    detail = await _service(hostex, _Approval(ApprovalStatus.PENDING)).get_detail(1)
    price = detail["reference_price_groups"][0]["prices"][0]
    assert price["currency_label"] == currency_label
    assert price.get("currency_note") == currency_note
    executor = HostexReadOnlyToolExecutor(hostex, local_date_provider=lambda: date(2026, 9, 9))
    rows = await executor.execute("search_reference_price", {
        "check_in_date": "2026-09-10", "check_out_date": "2026-09-11",
    })
    assert [(row["property_id"], row["nightly_reference_prices"]) for row in rows] == (
        [(101, [{"date": "2026-09-10", "price": 399.0}])] if quote_allowed else []
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [ApprovalStatus.BOOKED, ApprovalStatus.REJECTED])
async def test_finished_approvals_do_not_touch_hostex_at_all(
    status: ApprovalStatus,
) -> None:
    """已结束的审批不需要下单参考数据，不该再请求外部接口。"""
    hostex = _Hostex()
    service = _service(hostex, _Approval(status))

    detail = await service.get_detail(1)

    assert hostex.calls == []
    assert detail["can_confirm"] is False
    assert detail["reference_unavailable"] == []
