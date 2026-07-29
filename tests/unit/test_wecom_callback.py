import base64
import os

import httpx
import pytest
from fastapi import FastAPI

from homestay_bot.integrations.wecom.callback_crypto import InvalidCallbackSignature
from homestay_bot.routes.wecom_callback import (
    WeComCallbackService,
    get_callback_service,
    router,
)
from tests.unit.test_callback_crypto import encrypt_fixture


class CaptureSyncQueue:
    """捕获回调解析出的消息同步参数。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def enqueue_wecom_sync(self, token: str, open_kfid: str) -> None:
        """记录待同步的回调 Token 与客服账号。"""
        self.calls.append((token, open_kfid))


@pytest.mark.asyncio
async def test_callback_service_decrypts_and_enqueues_sync() -> None:
    """有效回调应提取同步 Token 与客服账号并快速入队。"""
    token = "callback-token"
    aes_key = base64.b64encode(os.urandom(32)).decode().rstrip("=")
    inner_xml = (
        b"<xml><Event>kf_msg_or_event</Event><Token>sync-token</Token>"
        b"<OpenKfId>wk-1</OpenKfId></xml>"
    )
    encrypted, signature = encrypt_fixture(
        inner_xml,
        token=token,
        encoding_aes_key=aes_key,
        receive_id="corp-id",
        timestamp="100",
        nonce="200",
    )
    outer_xml = f"<xml><Encrypt>{encrypted}</Encrypt></xml>".encode()
    queue = CaptureSyncQueue()
    service = WeComCallbackService.from_credentials(
        token, aes_key, "corp-id", queue
    )

    await service.verify_and_enqueue(outer_xml, signature, "100", "200")

    assert queue.calls == [("sync-token", "wk-1")]


@pytest.mark.asyncio
async def test_callback_route_returns_401_for_invalid_signature() -> None:
    """无效签名应返回 401，且不能伪装成企业微信成功响应。"""

    class RejectingService:
        """固定拒绝回调的测试服务。"""

        async def verify_and_enqueue(self, *args: object) -> None:
            """模拟签名校验失败。"""
            raise InvalidCallbackSignature("bad")

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_callback_service] = lambda: RejectingService()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/callbacks/wecom?msg_signature=bad&timestamp=100&nonce=200",
            content="<xml><Encrypt>cipher</Encrypt></xml>",
        )

    assert response.status_code == 401


def test_callback_service_verifies_url_echo() -> None:
    """企业微信配置回调 URL 时应返回解密后的 echostr。"""
    token = "callback-token"
    aes_key = base64.b64encode(os.urandom(32)).decode().rstrip("=")
    encrypted, signature = encrypt_fixture(
        b"echo-value",
        token=token,
        encoding_aes_key=aes_key,
        receive_id="corp-id",
        timestamp="100",
        nonce="200",
    )
    service = WeComCallbackService.from_credentials(
        token, aes_key, "corp-id", CaptureSyncQueue()
    )

    echo = service.verify_url(encrypted, signature, "100", "200")

    assert echo == "echo-value"
