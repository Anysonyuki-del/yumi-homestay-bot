import os
from datetime import date, timedelta

import pytest

from homestay_bot.integrations.hostex_client import HostexClient

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_CONTRACT_TESTS") != "1",
    reason="仅在显式开启真实契约测试时访问百居易",
)


@pytest.mark.asyncio
async def test_live_hostex_can_list_properties() -> None:
    """使用已轮换的新 Token 验证百居易全部只读查询契约。"""
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
