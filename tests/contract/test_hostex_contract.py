import os
from datetime import date, timedelta

import pytest

from homestay_bot.integrations.hostex_client import (
    HostexClient,
    ReservationQuery,
)
from homestay_bot.routes.hostex_webhook import HostexWebhookService

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_CONTRACT_TESTS") != "1",
    reason="仅在显式开启真实契约测试时访问百居易",
)


@pytest.mark.asyncio
async def test_live_hostex_can_list_properties() -> None:
    """使用临时注入的 Token 验证百居易全部只读查询契约。"""
    token = os.environ["HOSTEX_ACCESS_TOKEN"]
    client = HostexClient(token)
    start_date = date.today() + timedelta(days=30)
    end_date = start_date + timedelta(days=1)
    try:
        properties = await client.list_properties()
        room_types = await client.list_room_types()
        availabilities = await client.list_availabilities(
            [properties[0].id], start_date, end_date
        )
        reference_prices = await client.list_reference_prices(
            start_date, end_date
        )
        income_methods = await client.list_income_methods()
    finally:
        await client.aclose()

    assert properties
    assert isinstance(room_types, list)
    assert availabilities
    assert isinstance(reference_prices, list)
    assert isinstance(income_methods, list)


@pytest.mark.asyncio
async def test_live_hostex_can_query_reconciliation_window() -> None:
    """验证一期对账使用的近期订单只读查询契约。"""
    client = HostexClient(os.environ["HOSTEX_ACCESS_TOKEN"])
    today = date.today()
    try:
        reservations = await client.list_reservations(
            ReservationQuery(
                start_check_in_date=today - timedelta(days=1),
                end_check_in_date=today + timedelta(days=15),
            )
        )
    finally:
        await client.aclose()

    assert isinstance(reservations, list)
    assert all(item.reservation_code for item in reservations)


@pytest.mark.asyncio
async def test_hostex_webhook_sample_keeps_only_sync_whitelist() -> None:
    """Webhook 样例解析不得把客户联系方式保存到事件原文。"""
    recorded: list[dict[str, object]] = []

    class Recorder:
        """记录服务最终交给仓储的安全字段。"""

        async def record_hostex_event(self, **fields):
            """保存白名单参数。"""
            recorded.append(fields)
            return True

    service = HostexWebhookService("contract-secret", Recorder())
    await service.receive(
        "contract-secret",
        {
            "event_id": "event-contract-1",
            "event": "reservation_updated",
            "data": {
                "reservation_code": "R-CONTRACT",
                "guest_phone": "13800138000",
                "door_password": "839201",
            },
        },
    )

    assert recorded[0]["payload"] == {
        "event_type": "reservation_updated",
        "reservation_code": "R-CONTRACT",
    }
    assert "13800138000" not in str(recorded)
    assert "839201" not in str(recorded)
