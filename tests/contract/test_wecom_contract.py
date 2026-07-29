import os

import pytest

from homestay_bot.integrations.wecom.api_client import WeComApiClient

REQUIRED_VARIABLES = {
    "WECOM_CORP_ID",
    "WECOM_KF_SECRET",
    "WECOM_AGENT_SECRET",
    "WECOM_TEST_SYNC_TOKEN",
    "WECOM_TEST_OPEN_KFID",
}

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_CONTRACT_TESTS") != "1"
    or any(not os.getenv(name) for name in REQUIRED_VARIABLES),
    reason="仅在显式开启并提供企业微信测试回调参数时执行",
)


@pytest.mark.asyncio
async def test_live_wecom_can_sync_customer_service_messages() -> None:
    """验证真实企业微信凭据和客服消息同步契约。"""
    client = WeComApiClient(
        os.environ["WECOM_CORP_ID"],
        os.environ["WECOM_KF_SECRET"],
        os.environ["WECOM_AGENT_SECRET"],
    )
    try:
        page = await client.sync_messages(
            cursor="",
            token=os.environ["WECOM_TEST_SYNC_TOKEN"],
            open_kfid=os.environ["WECOM_TEST_OPEN_KFID"],
        )
    finally:
        await client.aclose()

    assert page.has_more in {0, 1}
