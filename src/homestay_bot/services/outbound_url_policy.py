"""为可配置的 DeepSeek 地址提供连接阶段 SSRF 与 DNS 重绑定防护。"""

import asyncio
import ipaddress
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import cast
from urllib.parse import urlsplit

import httpx

AddressInfo = tuple[object, ...]
Resolver = Callable[[str, int], Awaitable[Sequence[AddressInfo]]]

_METADATA_ADDRESSES = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("100.100.100.200"),
}
_RFC6052_WELL_KNOWN_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata",
    "metadata.google.internal",
}


class OutboundUrlRejected(ValueError):
    """表示外联地址未通过公网 HTTPS 安全策略。"""


class OutboundResolutionTimeout(OutboundUrlRejected):
    """表示公网域名预解析超过候选测试允许时间。"""


class OutboundRedirectRejected(httpx.TransportError):
    """表示供应商返回重定向；敏感鉴权请求禁止跟随。"""


class OutboundResponseTooLarge(httpx.TransportError):
    """表示供应商响应体超过连接测试允许的硬上限。"""


@dataclass(frozen=True, slots=True)
class ResolvedPublicTarget:
    """保存一次请求在连接前确认过的原域名与全部公网地址。"""

    hostname: str
    port: int
    addresses: tuple[str, ...]


async def _system_resolver(hostname: str, port: int) -> Sequence[AddressInfo]:
    """在线程池调用系统解析器，避免阻塞 FastAPI 事件循环。"""
    loop = asyncio.get_running_loop()
    return await loop.getaddrinfo(
        hostname,
        port,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )


class OutboundUrlPolicy:
    """只允许没有凭据和查询串的公网 HTTPS 地址。"""

    def __init__(
        self,
        *,
        resolver: Resolver = _system_resolver,
        resolve_timeout_seconds: float = 3.0,
    ) -> None:
        """注入 DNS 解析器，测试可覆盖混合地址与重绑定场景。"""
        if not 0.1 <= resolve_timeout_seconds <= 10.0:
            raise ValueError("DNS 解析超时时间无效")
        self._resolver = resolver
        self._resolve_timeout_seconds = resolve_timeout_seconds

    async def resolve(self, url: str) -> ResolvedPublicTarget:
        """解析并校验全部 A/AAAA；任一地址不安全就整体拒绝。"""
        try:
            parsed = urlsplit(url)
            port = parsed.port or 443
        except (TypeError, ValueError) as error:
            raise OutboundUrlRejected("外联地址格式无效") from error
        hostname = (parsed.hostname or "").rstrip(".").lower()
        if (
            parsed.scheme.lower() != "https"
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not 1 <= port <= 65535
        ):
            raise OutboundUrlRejected("外联地址不符合公网 HTTPS 策略")
        if hostname in _BLOCKED_HOSTNAMES or hostname.endswith(".localhost"):
            raise OutboundUrlRejected("外联主机不可访问")

        addresses: tuple[str, ...]
        literal = self._parse_ip(hostname)
        if literal is not None:
            addresses = (literal,)
        else:
            try:
                records = await asyncio.wait_for(
                    self._resolver(hostname, port),
                    timeout=self._resolve_timeout_seconds,
                )
            except TimeoutError as error:
                raise OutboundResolutionTimeout("外联域名解析超时") from error
            except (OSError, UnicodeError) as error:
                raise OutboundUrlRejected("外联域名无法安全解析") from error
            addresses = self._extract_addresses(records)
        if not addresses or any(not self._is_public_address(item) for item in addresses):
            raise OutboundUrlRejected("外联域名包含非公网地址")
        return ResolvedPublicTarget(hostname=hostname, port=port, addresses=addresses)

    @staticmethod
    def _parse_ip(hostname: str) -> str | None:
        """把字面 IP 规范化；域名返回 None 继续执行 DNS。"""
        try:
            return str(ipaddress.ip_address(hostname))
        except ValueError:
            return None

    @staticmethod
    def _extract_addresses(records: Sequence[AddressInfo]) -> tuple[str, ...]:
        """从 getaddrinfo 结果提取全部去重 A/AAAA，拒绝畸形记录。"""
        addresses: list[str] = []
        for record in records:
            try:
                socket_address = record[4]
                raw_address = socket_address[0]  # type: ignore[index]
                address = str(ipaddress.ip_address(str(raw_address)))
            except (IndexError, TypeError, ValueError) as error:
                raise OutboundUrlRejected("外联域名解析结果无效") from error
            if address not in addresses:
                addresses.append(address)
        return tuple(addresses)

    @staticmethod
    def _is_public_address(address: str) -> bool:
        """显式拒绝特殊地址，并检查 IPv6 内嵌 IPv4 的真实可达范围。"""
        parsed = ipaddress.ip_address(address)
        if isinstance(parsed, ipaddress.IPv6Address):
            if parsed.ipv4_mapped is not None:
                return OutboundUrlPolicy._is_direct_public_address(parsed.ipv4_mapped)
            if parsed in _RFC6052_WELL_KNOWN_NAT64:
                embedded = ipaddress.IPv4Address(int(parsed) & 0xFFFFFFFF)
                return OutboundUrlPolicy._is_direct_public_address(embedded)
        return OutboundUrlPolicy._is_direct_public_address(parsed)

    @staticmethod
    def _is_direct_public_address(
        address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    ) -> bool:
        """正向允许普通公网地址，同时显式拒绝所有特殊用途类别。"""
        if address in _METADATA_ADDRESSES:
            return False
        if (
            address.is_multicast
            or address.is_reserved
            or address.is_unspecified
            or address.is_loopback
            or address.is_link_local
            or address.is_private
        ):
            return False
        return address.is_global


class PublicHttpsTransport(httpx.AsyncBaseTransport):
    """在连接边界固定已校验公网 IP，同时保留域名证书校验。"""

    def __init__(
        self,
        policy: OutboundUrlPolicy,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        max_response_bytes: int = 1024 * 1024,
    ) -> None:
        """默认底层禁用重试；每次请求均在本传输层完成解析与 IP 固定。"""
        if not 1024 <= max_response_bytes <= 4 * 1024 * 1024:
            raise ValueError("外联响应体上限无效")
        self._policy = policy
        self._transport = transport or httpx.AsyncHTTPTransport(
            retries=0,
            limits=httpx.Limits(
                max_connections=1,
                max_keepalive_connections=1,
                keepalive_expiry=5.0,
            ),
        )
        self._max_response_bytes = max_response_bytes

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """解析全部地址后连接字面 IP，彻底消除校验后再次 DNS 的窗口。"""
        target = await self._policy.resolve(str(request.url))
        # 选择已全部校验集合中的第一个地址；URL 使用字面 IP 后，httpcore 不再解析域名。
        pinned_url = request.url.copy_with(host=target.addresses[0])
        authority = target.hostname
        try:
            if ipaddress.ip_address(target.hostname).version == 6:
                authority = f"[{target.hostname}]"
        except ValueError:
            pass
        if target.port != 443:
            authority = f"{authority}:{target.port}"
        headers = [
            (name, value)
            for name, value in request.headers.raw
            if name.lower() != b"host"
        ]
        headers.append((b"host", authority.encode("ascii")))
        extensions = dict(request.extensions)
        # httpcore 用该扩展设置 TLS SNI 与证书域名，连接目标仍是 pinned_url 的字面 IP。
        extensions["sni_hostname"] = target.hostname
        pinned_request = httpx.Request(
            request.method,
            pinned_url,
            headers=headers,
            extensions=extensions,
        )
        # 直接保留 AsyncClient 已构造的异步请求流，避免重新包装成同步 ByteStream。
        pinned_request.stream = request.stream
        response = await self._transport.handle_async_request(pinned_request)
        if 300 <= response.status_code < 400:
            await response.aclose()
            raise OutboundRedirectRejected("外联服务返回不允许的重定向", request=request)
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = 0
            if declared_size > self._max_response_bytes:
                await response.aclose()
                raise OutboundResponseTooLarge("外联响应超过安全上限", request=request)
        response.stream = _LimitedAsyncByteStream(
            cast(httpx.AsyncByteStream, response.stream),
            max_bytes=self._max_response_bytes,
            request=request,
        )
        return response

    async def aclose(self) -> None:
        """释放底层连接池。"""
        await self._transport.aclose()


class _LimitedAsyncByteStream(httpx.AsyncByteStream):
    """流式累计实际响应字节，不能只信供应商 Content-Length。"""

    def __init__(
        self,
        stream: httpx.AsyncByteStream,
        *,
        max_bytes: int,
        request: httpx.Request,
    ) -> None:
        """保存底层响应流和硬上限。"""
        self._stream = stream
        self._max_bytes = max_bytes
        self._request = request

    async def __aiter__(self) -> AsyncIterator[bytes]:
        """逐块转发响应，超过上限立即关闭并抛出稳定传输异常。"""
        consumed = 0
        async for chunk in self._stream:
            consumed += len(chunk)
            if consumed > self._max_bytes:
                await self._stream.aclose()
                raise OutboundResponseTooLarge(
                    "外联响应超过安全上限",
                    request=self._request,
                )
            yield chunk

    async def aclose(self) -> None:
        """释放底层响应流和连接。"""
        await self._stream.aclose()


def build_public_https_client(
    policy: OutboundUrlPolicy,
    *,
    timeout_seconds: float = 5.0,
    max_response_bytes: int = 1024 * 1024,
) -> httpx.AsyncClient:
    """构造可供候选探针和后续生产 SDK 复用的受控 HTTP 客户端。"""
    if not 1.0 <= timeout_seconds <= 15.0:
        raise ValueError("外联测试超时时间无效")
    timeout = httpx.Timeout(
        timeout_seconds,
        connect=min(timeout_seconds, 3.0),
        read=timeout_seconds,
        write=min(timeout_seconds, 3.0),
        pool=1.0,
    )
    return httpx.AsyncClient(
        transport=PublicHttpsTransport(
            policy,
            max_response_bytes=max_response_bytes,
        ),
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    )
