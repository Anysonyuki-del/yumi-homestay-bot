import os

import pytest

from homestay_bot.integrations.wecom.api_client import WeComApiClient

BASE_VARIABLES = {
    "WECOM_CORP_ID",
    "WECOM_KF_SECRET",
    "WECOM_AGENT_SECRET",
}

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_CONTRACT_TESTS") != "1"
    or any(not os.getenv(name) for name in BASE_VARIABLES),
    reason="仅在显式开启并提供企业微信基础凭据时执行",
)


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("WECOM_TEST_SYNC_TOKEN")
    or not os.getenv("WECOM_TEST_OPEN_KFID"),
    reason="同步消息还需要测试回调 Token 和客服账号",
)
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


@pytest.mark.asyncio
async def test_live_wecom_can_list_customer_service_accounts() -> None:
    """使用真实基础凭据验证只读客服账号发现契约。"""
    client = WeComApiClient(
        os.environ["WECOM_CORP_ID"],
        os.environ["WECOM_KF_SECRET"],
        os.environ["WECOM_AGENT_SECRET"],
    )
    try:
        account_ids = await client.list_kf_account_ids()
    finally:
        await client.aclose()

    assert account_ids
    assert all(item.startswith("wk") for item in account_ids)
