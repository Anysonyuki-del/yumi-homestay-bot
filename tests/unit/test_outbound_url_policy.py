import asyncio
import socket
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from homestay_bot.services.outbound_url_policy import (
    OutboundRedirectRejected,
    OutboundResolutionTimeout,
    OutboundResponseTooLarge,
    OutboundUrlPolicy,
    OutboundUrlRejected,
    PublicHttpsTransport,
    ResolvedPublicTarget,
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
@pytest.mark.parametrize(
    "address",
    [
        "224.0.0.1",
        "239.1.1.1",
        "ff02::1",
        "ff0e::1",
        "::ffff:127.0.0.1",
        "64:ff9b::7f00:1",
    ],
)
async def test_policy_rejects_multicast_and_embedded_non_public_addresses(
    address: str,
) -> None:
    """多播及 IPv4-mapped/NAT64 中嵌入的非公网地址必须拒绝。"""
    policy = OutboundUrlPolicy(resolver=resolver_for(address))

    with pytest.raises(OutboundUrlRejected):
        await policy.resolve("https://api.deepseek.example/v1")


@pytest.mark.asyncio
async def test_policy_accepts_public_ipv4_ipv6_mapped_and_nat64_addresses() -> None:
    """合法公网 IPv4、IPv6、IPv4-mapped 和 RFC6052 NAT64 不应被过度拒绝。"""
    addresses = (
        "8.8.8.8",
        "2606:4700:4700::1111",
        "::ffff:8.8.8.8",
        "64:ff9b::808:808",
    )
    policy = OutboundUrlPolicy(resolver=resolver_for(*addresses))

    target = await policy.resolve("https://api.deepseek.example/v1")

    assert target.addresses == (
        "8.8.8.8",
        "2606:4700:4700::1111",
        "::ffff:8.8.8.8",
        "64:ff9b::808:808",
    )


@pytest.mark.asyncio
async def test_policy_maps_dns_timeout_without_echoing_hostname_or_query() -> None:
    """DNS 解析超时必须成为受控异常，不能无限等待或回显候选地址。"""

    async def timeout_resolver(host: str, port: int) -> list[tuple[object, ...]]:
        """模拟系统解析器超时。"""
        del host, port
        raise TimeoutError("secret-hostname")

    policy = OutboundUrlPolicy(resolver=timeout_resolver, resolve_timeout_seconds=1.0)

    with pytest.raises(OutboundResolutionTimeout) as captured:
        await policy.resolve("https://api.deepseek.example/v1")

    assert "api.deepseek.example" not in str(captured.value)
    assert "secret-hostname" not in str(captured.value)


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
@pytest.mark.parametrize(
    ("url", "expected_host"),
    [
        ("https://[2606:4700:4700::1111]/v1", "[2606:4700:4700::1111]"),
        ("https://[2606:4700:4700::1111]:8443/v1", "[2606:4700:4700::1111]:8443"),
    ],
)
async def test_transport_formats_ipv6_literal_host_authority(
    url: str,
    expected_host: str,
) -> None:
    """IPv6 字面地址的 Host 必须使用方括号，非默认端口保留端口。"""
    inner = RecordingTransport()
    transport = PublicHttpsTransport(
        OutboundUrlPolicy(resolver=resolver_for("8.8.8.8")),
        transport=inner,
    )
    async with httpx.AsyncClient(transport=transport) as client:
        await client.get(url)

    assert inner.requests[0].headers["host"] == expected_host


@pytest.mark.asyncio
async def test_real_tls_connection_uses_original_hostname_for_sni_and_certificate(
    tmp_path: Path,
) -> None:
    """真实 TLS 握手连接字面 IP，但 SNI 与证书校验必须使用原域名。"""
    hostname = "api.deepseek.example"
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .sign(private_key, hashes.SHA256())
    )
    certificate_path = tmp_path / "server.crt"
    key_path = tmp_path / "server.key"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(certificate_path, key_path)
    observed_sni: list[str | None] = []
    observed_host: list[str] = []

    def record_server_name(
        ssl_socket: ssl.SSLObject,
        server_name: str | None,
        context: ssl.SSLContext,
    ) -> None:
        """记录客户端真实 TLS 握手携带的 SNI 域名。"""
        del ssl_socket, context
        observed_sni.append(server_name)

    server_context.set_servername_callback(record_server_name)

    async def handle_client(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """读取真实 HTTPS 请求头并返回最小响应。"""
        request = await reader.readuntil(b"\r\n\r\n")
        host_line = next(
            line
            for line in request.split(b"\r\n")
            if line.lower().startswith(b"host:")
        )
        observed_host.append(host_line.split(b":", 1)[1].strip().decode())
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(
        handle_client,
        "127.0.0.1",
        0,
        ssl=server_context,
    )
    port = server.sockets[0].getsockname()[1]

    class LocalPinnedPolicy:
        """仅供 TLS 契约测试固定到本机字面 IP。"""

        async def resolve(self, url: str) -> ResolvedPublicTarget:
            """返回原域名和测试服务器地址。"""
            del url
            return ResolvedPublicTarget(hostname, port, ("127.0.0.1",))

    client_context = ssl.create_default_context(cafile=str(certificate_path))
    inner = httpx.AsyncHTTPTransport(verify=client_context, retries=0)
    transport = PublicHttpsTransport(LocalPinnedPolicy(), transport=inner)  # type: ignore[arg-type]
    try:
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.get(f"https://{hostname}:{port}/probe")
        assert response.text == "OK"
    finally:
        server.close()
        await server.wait_closed()

    assert observed_sni == [hostname]
    assert observed_host == [f"{hostname}:{port}"]


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
