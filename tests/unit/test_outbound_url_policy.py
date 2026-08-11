import socket

import httpx
import pytest

from homestay_bot.services.outbound_url_policy import (
    OutboundRedirectRejected,
    OutboundResponseTooLarge,
    OutboundUrlPolicy,
    OutboundUrlRejected,
    PublicHttpsTransport,
    build_public_https_client,
)


class RecordingTransport(httpx.AsyncBaseTransport):
    """记录策略交给底层连接池的已固定 IP 请求。"""

    def __init__(self, responses: list[httpx.Response] | None = None) -> None:
        """保存按顺序返回的测试响应。"""
        self.requests: list[httpx.Request] = []
        self.responses = responses or [httpx.Response(200, json={"ok": True})]
        self.closed = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """记录 URL、Host 与 TLS SNI 扩展，证明连接不会再次查询域名。"""
        self.requests.append(request)
        response = self.responses.pop(0)
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            content=response.content,
            request=request,
        )

    async def aclose(self) -> None:
        """记录策略传输层已释放底层连接池。"""
        self.closed = True


def resolver_for(*addresses: str):
    """构造返回固定 A/AAAA 记录的异步解析器。"""

    async def resolve(host: str, port: int) -> list[tuple[object, ...]]:
        """按地址族返回与 getaddrinfo 兼容的最小结果。"""
        del host, port
        return [
            (
                socket.AF_INET6 if ":" in address else socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (address, 443, 0, 0) if ":" in address else (address, 443),
            )
            for address in addresses
        ]

    return resolve


@pytest.mark.asyncio
async def test_policy_accepts_only_public_https_and_validates_every_answer() -> None:
    """全部 A/AAAA 都是公网地址时才允许 DeepSeek 根地址。"""
    policy = OutboundUrlPolicy(resolver=resolver_for("8.8.8.8", "2606:4700:4700::1111"))

    target = await policy.resolve("https://api.deepseek.example/v1")

    assert target.hostname == "api.deepseek.example"
    assert target.addresses == ("8.8.8.8", "2606:4700:4700::1111")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://api.deepseek.example",
        "https://localhost/v1",
        "https://127.0.0.1/v1",
        "https://[::1]/v1",
        "https://169.254.169.254/latest/meta-data",
        "https://100.100.100.200/latest/meta-data",
        "https://user:password@api.deepseek.example/v1",
        "https://api.deepseek.example/v1?api_key=secret",
    ],
)
async def test_policy_rejects_unsafe_url_forms_without_echoing_input(url: str) -> None:
    """协议、本机、元数据、用户信息和查询参数均不得进入外联请求。"""
    policy = OutboundUrlPolicy(resolver=resolver_for("8.8.8.8"))

    with pytest.raises(OutboundUrlRejected) as captured:
        await policy.resolve(url)

    assert "secret" not in str(captured.value)
    assert url not in str(captured.value)


@pytest.mark.asyncio
async def test_policy_rejects_one_private_answer_among_public_dns_answers() -> None:
    """DNS 同时返回公网和私网地址时必须整体拒绝，不能择一放行。"""
    policy = OutboundUrlPolicy(resolver=resolver_for("8.8.8.8", "10.0.0.8"))

    with pytest.raises(OutboundUrlRejected):
        await policy.resolve("https://api.deepseek.example/v1")


@pytest.mark.asyncio
async def test_transport_pins_validated_ip_and_preserves_host_and_tls_sni() -> None:
    """底层只能连接已校验 IP，同时保留原域名用于 Host 与证书校验。"""
    inner = RecordingTransport()
    transport = PublicHttpsTransport(
        OutboundUrlPolicy(resolver=resolver_for("8.8.8.8")),
        transport=inner,
    )
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.get("https://api.deepseek.example/v1/models")

    assert response.status_code == 200
    pinned = inner.requests[0]
    assert pinned.url.host == "8.8.8.8"
    assert pinned.headers["host"] == "api.deepseek.example"
    assert pinned.extensions["sni_hostname"] == "api.deepseek.example"
    assert inner.closed is True


@pytest.mark.asyncio
async def test_transport_rejects_redirect_without_issuing_second_request() -> None:
    """供应商重定向一律拒绝，禁止在第二地址泄露 Authorization。"""
    inner = RecordingTransport(
        [httpx.Response(302, headers={"location": "https://api.deepseek.example/v1/final"})]
    )
    transport = PublicHttpsTransport(
        OutboundUrlPolicy(resolver=resolver_for("8.8.8.8")),
        transport=inner,
    )
    async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
        with pytest.raises(OutboundRedirectRejected):
            await client.get("https://api.deepseek.example/v1/start")

    assert len(inner.requests) == 1


@pytest.mark.asyncio
async def test_transport_streams_with_hard_response_body_limit() -> None:
    """即使没有 Content-Length，也必须在流式读取超过 1 MiB 时立即失败。"""
    inner = RecordingTransport([httpx.Response(200, content=b"x" * (1024 * 1024 + 1))])
    transport = PublicHttpsTransport(
        OutboundUrlPolicy(resolver=resolver_for("8.8.8.8")),
        transport=inner,
        max_response_bytes=1024 * 1024,
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(OutboundResponseTooLarge):
            await client.get("https://api.deepseek.example/v1/models")


@pytest.mark.asyncio
async def test_controlled_client_disables_redirects_proxy_retries_and_bounds_timeouts() -> None:
    """供两套 SDK 复用的客户端必须固定安全传输、禁重定向且超时有界。"""
    client = build_public_https_client(
        OutboundUrlPolicy(resolver=resolver_for("8.8.8.8")),
        timeout_seconds=5.0,
    )
    try:
        assert client.follow_redirects is False
        assert client._trust_env is False  # noqa: SLF001 - 安全构造契约
        assert isinstance(client._transport, PublicHttpsTransport)  # noqa: SLF001
        assert client.timeout.connect == 3.0
        assert client.timeout.read == 5.0
        inner = client._transport._transport  # noqa: SLF001 - 验证底层连接池配置
        assert isinstance(inner, httpx.AsyncHTTPTransport)
        assert inner._pool._max_connections == 1  # noqa: SLF001
    finally:
        await client.aclose()
