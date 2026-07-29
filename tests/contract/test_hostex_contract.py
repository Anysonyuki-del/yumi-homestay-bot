import os

import pytest

from homestay_bot.integrations.hostex_client import HostexClient

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_CONTRACT_TESTS") != "1",
    reason="仅在显式开启真实契约测试时访问百居易",
)


@pytest.mark.asyncio
async def test_live_hostex_can_list_properties() -> None:
    """使用已轮换的新 Token 验证百居易房间查询契约。"""
    token = os.environ["HOSTEX_ACCESS_TOKEN"]
    client = HostexClient(token)

    properties = await client.list_properties()

    assert properties
