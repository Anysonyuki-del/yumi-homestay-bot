import base64
import os

import httpx
import pytest
from fastapi import FastAPI

from homestay_bot.integrations.wecom.callback_crypto import InvalidCallbackSignature
from homestay_bot.routes.wecom_callback import (
    InvalidCallbackPayload,
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

    async def enqueue_card_action(self, action: dict[str, str]) -> None:
        """记录员工点击卡片按钮的字段。"""
        self.calls.append(("card", action))


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


@pytest.mark.asyncio
async def test_callback_route_rejects_body_over_limit_before_verification() -> None:
    """超过上限的企业微信回调必须在验签前拒绝。"""

    class UnexpectedService:
        """大请求不应触发验签服务。"""

        async def verify_and_enqueue(self, *args: object) -> None:
            """如果被调用则说明请求体限制执行过晚。"""
            raise AssertionError("oversized callback reached verification")

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_callback_service] = lambda: UnexpectedService()
    transport = httpx.ASGITransport(app=app)
    body = b"x" * (256 * 1024 + 1)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/callbacks/wecom?msg_signature=bad&timestamp=100&nonce=200",
            content=body,
        )

    assert response.status_code == 413


@pytest.mark.asyncio
async def test_callback_service_rejects_xml_entity_expansion_before_decrypt() -> None:
    """企业微信外层 XML 禁止实体展开，恶意内容不得进入解密边界。"""

    class UnexpectedCrypto:
        """实体被拒绝后不应触发解密。"""

        def decrypt(self, *args: object) -> bytes:
            """如果调用则说明安全 XML 解析执行过晚。"""
            raise AssertionError("恶意 XML 不应进入解密")

    service = WeComCallbackService(UnexpectedCrypto(), CaptureSyncQueue())
    payload = (
        b'<!DOCTYPE xml [<!ENTITY bomb "expanded">]>'
        b"<xml><Encrypt>&bomb;</Encrypt></xml>"
    )

    with pytest.raises(InvalidCallbackPayload):
        await service.verify_and_enqueue(payload, "bad", "1", "2")


@pytest.mark.asyncio
async def test_callback_service_rejects_oversized_decrypted_fields() -> None:
    """解密后的同步 Token 和客服账号必须在入队前限制长度。"""

    class CryptoStub:
        """返回包含超长客服账号的已解密 XML。"""

        def decrypt(self, *args: object) -> bytes:
            """模拟已通过签名与解密的企业微信正文。"""
            return (
                "<xml><Event>kf_msg_or_event</Event><Token>sync-token</Token>"
                f"<OpenKfId>{'w' * 129}</OpenKfId></xml>"
            ).encode()

    queue = CaptureSyncQueue()
    service = WeComCallbackService(CryptoStub(), queue)

    with pytest.raises(InvalidCallbackPayload, match="字段过长"):
        await service.verify_and_enqueue(
            b"<xml><Encrypt>cipher</Encrypt></xml>",
            "signature",
            "100",
            "200",
        )

    assert queue.calls == []


@pytest.mark.asyncio
async def test_callback_route_rejects_oversized_signature_query() -> None:
    """异常超长验签参数必须由路由校验拒绝，不能进入回调服务。"""

    class UnexpectedService:
        """超长查询参数不应进入验签服务。"""

        async def verify_and_enqueue(self, *args: object) -> None:
            """若被调用则表示查询参数边界缺失。"""
            raise AssertionError("oversized query reached callback service")

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_callback_service] = lambda: UnexpectedService()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/callbacks/wecom",
            params={
                "msg_signature": "s" * 257,
                "timestamp": "100",
                "nonce": "200",
            },
            content="<xml><Encrypt>cipher</Encrypt></xml>",
        )

    assert response.status_code == 422


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


@pytest.mark.asyncio
async def test_callback_service_enqueues_card_button_click() -> None:
    """员工点转人工卡片按钮：验签后原样搬运点击人、按钮 key、卡片与更新码，交给 worker 复核。"""
    token = "callback-token"
    aes_key = base64.b64encode(os.urandom(32)).decode().rstrip("=")
    inner_xml = (
        b"<xml><ToUserName>corp-id</ToUserName><FromUserName>duty-1</FromUserName>"
        b"<MsgType>event</MsgType><Event>template_card_event</Event>"
        b"<EventKey>accept:7</EventKey><TaskId>handoff-abc</TaskId>"
        b"<CardType>button_interaction</CardType><ResponseCode>rc-1</ResponseCode>"
        b"<AgentID>1000002</AgentID></xml>"
    )
    encrypted, signature = encrypt_fixture(
        inner_xml, token=token, encoding_aes_key=aes_key, receive_id="corp-id",
        timestamp="100", nonce="200",
    )
    queue = CaptureSyncQueue()
    service = WeComCallbackService.from_credentials(token, aes_key, "corp-id", queue)

    await service.verify_and_enqueue(
        f"<xml><Encrypt>{encrypted}</Encrypt></xml>".encode(), signature, "100", "200"
    )

    assert queue.calls == [("card", {
        "userid": "duty-1", "key": "accept:7", "task_id": "handoff-abc",
        "response_code": "rc-1", "agent_id": "1000002",
    })]
