"""微信 code2Session 客户端：契约解析、失败分类、零重试和不泄露凭据。"""
import asyncio
import json
import logging

import httpx
import pytest

from homestay_bot.integrations import wechat_miniprogram
from homestay_bot.integrations.wechat_miniprogram import (
    MiniProgramCredentials,
    WeChatLoginError,
    WeChatLoginFailure,
    WeChatMiniProgramClient,
    load_miniprogram_credentials,
)
from homestay_bot.logging import redact_exception_text

APP_ID = "wx0123456789abcdef"
SECRET = "SYNTHETIC-APP-SECRET"


def _client(
    handler: httpx.MockTransport | None = None,
    *,
    timeout_seconds: float = 5.0,
) -> WeChatMiniProgramClient:
    """用合成凭据和指定传输层构造客户端。"""
    return WeChatMiniProgramClient(
        MiniProgramCredentials(app_id=APP_ID, app_secret=SECRET),
        transport=handler,
        timeout_seconds=timeout_seconds,
    )


def _responding(
    *responses: httpx.Response,
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    """按顺序返回响应并记录请求，用于断言请求次数。"""
    requests: list[httpx.Request] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        """记录请求并弹出下一个响应。"""
        requests.append(request)
        return queue.pop(0)

    return httpx.MockTransport(handler), requests


@pytest.mark.parametrize(
    ("app_id", "secret", "loaded"),
    [
        (APP_ID, SECRET, True),
        (f"  {APP_ID} ", f" {SECRET} ", True),
        (None, SECRET, False),
        (APP_ID, None, False),
        (APP_ID, "", False),
        ("wx0123", SECRET, False),
        ("WX0123456789ABCDEF", SECRET, False),
        (APP_ID, "has space", False),
    ],
)
def test_credentials_load_only_when_both_values_are_well_formed(
    app_id: str | None,
    secret: str | None,
    loaded: bool,
) -> None:
    """缺一项或格式无效都视为未配置，调用方据此只停用小程序登录。"""
    credentials = load_miniprogram_credentials(app_id, secret)
    assert (credentials is not None) is loaded
    if credentials is not None:
        assert credentials.app_id == APP_ID
        assert SECRET not in repr(credentials)


@pytest.mark.parametrize(
    "payload",
    [
        {"openid": "o-ABC_123", "session_key": "k"},
        {"openid": "o-ABC_123", "session_key": "k", "errcode": 0, "errmsg": "ok"},
    ],
)
async def test_success_accepts_missing_or_zero_errcode(payload: dict[str, object]) -> None:
    """官方示例成功时带 errcode 0；缺失与 0 都应视为成功。"""
    transport, requests = _responding(
        httpx.Response(
            200,
            content=json.dumps(payload).encode(),
            headers={"content-type": "text/plain"},
        )
    )
    assert await _client(transport).exchange_code("code-1") == "o-ABC_123"
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("errcode", "failure"),
    [
        (40029, WeChatLoginFailure.CODE_INVALID),
        (40226, WeChatLoginFailure.LOGIN_BLOCKED),
        (45011, WeChatLoginFailure.UPSTREAM_BUSY),
        (-1, WeChatLoginFailure.UPSTREAM_BUSY),
        (40013, WeChatLoginFailure.UPSTREAM_UNAVAILABLE),
    ],
)
async def test_errcodes_map_to_failures_without_retry(
    errcode: int,
    failure: WeChatLoginFailure,
) -> None:
    """每类错误码只请求一次：code 一次性，自动重试只会再耗一次额度。"""
    transport, requests = _responding(
        httpx.Response(200, json={"errcode": errcode, "errmsg": "UPSTREAM-ERRMSG"})
    )
    with pytest.raises(WeChatLoginError) as raised:
        await _client(transport).exchange_code("code-1")
    assert raised.value.failure is failure
    assert raised.value.errcode == errcode
    assert "UPSTREAM-ERRMSG" not in str(raised.value)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"<html>gateway</html>"),
        httpx.Response(200, json=["openid"]),
        httpx.Response(200, json={"session_key": "k"}),
        httpx.Response(200, json={"openid": ""}),
        httpx.Response(200, json={"openid": "bad id:with-colon"}),
        httpx.Response(200, json={"openid": "o" * 65}),
        httpx.Response(200, json={"errcode": "0", "openid": "o-1"}),
        httpx.Response(200, json={"errcode": True, "openid": "o-1"}),
        httpx.Response(500, json={"openid": "o-1"}),
        httpx.Response(302, headers={"location": "https://evil.example/"}),
    ],
)
async def test_invalid_upstream_responses_are_unavailable(response: httpx.Response) -> None:
    """非 2xx、重定向、非对象、缺 openid 或字段类型错误都按上游不可用处理，且只请求一次。"""
    transport, requests = _responding(response)
    with pytest.raises(WeChatLoginError) as raised:
        await _client(transport).exchange_code("code-1")
    assert raised.value.failure is WeChatLoginFailure.UPSTREAM_UNAVAILABLE
    assert len(requests) == 1


async def test_oversized_response_is_rejected() -> None:
    """响应正文超过上限时不再继续读取。"""
    transport, _ = _responding(
        httpx.Response(200, content=b"{" + b" " * wechat_miniprogram.MAX_RESPONSE_BYTES + b"}")
    )
    with pytest.raises(WeChatLoginError) as raised:
        await _client(transport).exchange_code("code-1")
    assert raised.value.failure is WeChatLoginFailure.UPSTREAM_UNAVAILABLE


async def test_network_failure_hides_url_with_secret_and_code(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """网络异常文本含完整 URL；对外异常不链接原异常，日志也不含 secret 或 js_code。"""

    def handler(request: httpx.Request) -> httpx.Response:
        """模拟连接失败，异常信息里带着请求 URL。"""
        raise httpx.ConnectError(f"failed {request.url}", request=request)

    caplog.set_level(logging.DEBUG)
    with pytest.raises(WeChatLoginError) as raised:
        await _client(httpx.MockTransport(handler)).exchange_code("CODE-PLAINTEXT")
    assert raised.value.failure is WeChatLoginFailure.UPSTREAM_UNAVAILABLE
    assert raised.value.__cause__ is None and raised.value.__suppress_context__ is True
    assert SECRET not in caplog.text and "CODE-PLAINTEXT" not in caplog.text


async def test_total_timeout_bounds_slow_upstream() -> None:
    """httpx 超时按阶段计算，总时长另有上限，慢速上游不会占住在途名额。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        """模拟一直不返回的上游。"""
        await asyncio.sleep(10)
        return httpx.Response(200, json={"openid": "o-1"})

    with pytest.raises(WeChatLoginError) as raised:
        await asyncio.wait_for(
            _client(httpx.MockTransport(handler), timeout_seconds=0.05).exchange_code("c"),
            timeout=2,
        )
    assert raised.value.failure is WeChatLoginFailure.UPSTREAM_UNAVAILABLE


def test_exception_text_with_code2session_url_is_redacted() -> None:
    """若 code2Session URL 进入异常或日志文本，secret 与 js_code 都必须被遮住。"""
    text = (
        "ConnectError for https://api.weixin.qq.com/sns/jscode2session"
        f"?appid={APP_ID}&secret={SECRET}&js_code=CODE-PLAINTEXT&grant_type=authorization_code"
    )
    redacted = redact_exception_text(text)
    assert SECRET not in redacted
    assert "CODE-PLAINTEXT" not in redacted
    assert APP_ID in redacted
