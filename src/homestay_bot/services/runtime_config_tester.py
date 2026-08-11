"""以只读、受控网络探针验证候选运行配置。"""

import base64
import binascii
import hashlib
import os
import struct
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from anthropic import AsyncAnthropic
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from openai import AsyncOpenAI

from homestay_bot.domain.runtime_config import RuntimeConfigSnapshot
from homestay_bot.integrations.hostex_client import (
    HostexBusinessError,
    HostexClient,
)
from homestay_bot.integrations.wecom.api_client import WeComApiClient, WeComApiError
from homestay_bot.integrations.wecom.callback_crypto import WeComCallbackCrypto
from homestay_bot.services.outbound_url_policy import (
    OutboundRedirectRejected,
    OutboundResponseTooLarge,
    OutboundUrlPolicy,
    OutboundUrlRejected,
    build_public_https_client,
)
from homestay_bot.services.runtime_config_service import (
    RuntimeConfigCheckTestResult,
    RuntimeConfigProviderTestResult,
    RuntimeConfigTestResult,
)

HttpClientFactory = Callable[[], Any]
SdkClientFactory = Callable[[RuntimeConfigSnapshot, Any], Any]
ProviderClientFactory = Callable[[RuntimeConfigSnapshot], Any]

_WECOM_TRUSTED_IP_CODES = {60020, 60021}
_WECOM_AUTH_CODES = {40001, 40003, 40013, 40014, 41001, 42001}


def build_probe_openai_client(
    snapshot: RuntimeConfigSnapshot,
    http_client: httpx.AsyncClient,
) -> AsyncOpenAI:
    """构造禁 SDK 重试并复用受控传输层的 OpenAI 兼容客户端。"""
    return AsyncOpenAI(
        api_key=snapshot.deepseek_api_key,
        base_url=snapshot.deepseek_base_url,
        http_client=http_client,
        max_retries=0,
    )


def build_probe_anthropic_client(
    snapshot: RuntimeConfigSnapshot,
    http_client: httpx.AsyncClient,
) -> AsyncAnthropic:
    """构造禁 SDK 重试并复用受控传输层的 Anthropic 兼容客户端。"""
    return AsyncAnthropic(
        api_key=snapshot.deepseek_api_key,
        base_url=f"{snapshot.deepseek_base_url.rstrip('/')}/anthropic",
        http_client=http_client,
        max_retries=0,
    )


class RuntimeConfigTester:
    """聚合 DeepSeek、百居易和企业微信的无业务写入测试。"""

    def __init__(
        self,
        *,
        url_policy: OutboundUrlPolicy | Any | None = None,
        http_client_factory: HttpClientFactory | None = None,
        openai_client_factory: SdkClientFactory = build_probe_openai_client,
        anthropic_client_factory: SdkClientFactory = build_probe_anthropic_client,
        hostex_client_factory: ProviderClientFactory | None = None,
        wecom_client_factory: ProviderClientFactory | None = None,
    ) -> None:
        """注入可替换客户端工厂；单元测试默认不需要任何真实网络。"""
        self._url_policy = url_policy or OutboundUrlPolicy()
        self._http_client_factory = http_client_factory or (
            lambda: build_public_https_client(self._url_policy)
        )
        self._openai_client_factory = openai_client_factory
        self._anthropic_client_factory = anthropic_client_factory
        self._hostex_client_factory = hostex_client_factory or (
            lambda snapshot: HostexClient(snapshot.hostex_access_token, timeout_seconds=5.0)
        )
        self._wecom_client_factory = wecom_client_factory or (
            lambda snapshot: WeComApiClient(
                snapshot.wecom_corp_id,
                snapshot.wecom_kf_secret,
                snapshot.wecom_agent_secret,
                contact_secret=snapshot.wecom_contact_secret,
                timeout_seconds=5.0,
            )
        )

    async def test(self, snapshot: RuntimeConfigSnapshot) -> RuntimeConfigTestResult:
        """执行全部分项并返回不含正文、URL、Query 和凭据的安全聚合。"""
        try:
            snapshot.validate()
        except ValueError:
            return RuntimeConfigTestResult(False, "runtime_config_invalid")

        deepseek = await self._test_deepseek(snapshot)
        hostex = await self._test_hostex(snapshot)
        wecom = await self._test_wecom(snapshot)
        providers = (deepseek, hostex, wecom)
        first_failure = next((item.error_code for item in providers if not item.succeeded), None)
        return RuntimeConfigTestResult(
            succeeded=first_failure is None,
            error_code=first_failure,
            providers=providers,
        )

    async def _test_deepseek(
        self,
        snapshot: RuntimeConfigSnapshot,
    ) -> RuntimeConfigProviderTestResult:
        """分别执行 OpenAI 与 Anthropic 两套极小模型调用。"""
        anthropic_url = f"{snapshot.deepseek_base_url.rstrip('/')}/anthropic"
        try:
            await self._url_policy.resolve(snapshot.deepseek_base_url)
            await self._url_policy.resolve(anthropic_url)
        except OutboundUrlRejected:
            return RuntimeConfigProviderTestResult(
                "deepseek",
                False,
                "deepseek_url_blocked",
            )

        openai_error = await self._run_openai_probe(snapshot)
        anthropic_error = await self._run_anthropic_probe(snapshot)
        error_code = openai_error or anthropic_error
        return RuntimeConfigProviderTestResult(
            "deepseek",
            error_code is None,
            error_code,
            checks=(
                RuntimeConfigCheckTestResult(
                    "openai",
                    openai_error is None,
                    openai_error,
                ),
                RuntimeConfigCheckTestResult(
                    "anthropic",
                    anthropic_error is None,
                    anthropic_error,
                ),
            ),
        )

    async def _run_openai_probe(self, snapshot: RuntimeConfigSnapshot) -> str | None:
        """使用 JSON 输出模式发送最多两个 token 的 OpenAI 兼容请求。"""
        http_client = self._http_client_factory()
        sdk_client: Any | None = None
        error_code: str | None = None
        try:
            sdk_client = self._openai_client_factory(snapshot, http_client)
            await sdk_client.chat.completions.create(
                model=snapshot.deepseek_model,
                messages=[{"role": "user", "content": "仅回复空 JSON 对象。"}],
                response_format={"type": "json_object"},
                max_tokens=2,
                temperature=0,
            )
        except Exception as error:
            error_code = self._map_error("deepseek", error)
        finally:
            error_code = await self._close_clients(
                "deepseek",
                sdk_client,
                http_client,
                existing_error=error_code,
            )
        return error_code

    async def _run_anthropic_probe(self, snapshot: RuntimeConfigSnapshot) -> str | None:
        """发送最多两个 token 的 Anthropic 兼容请求，覆盖旅游接口鉴权。"""
        http_client = self._http_client_factory()
        sdk_client: Any | None = None
        error_code: str | None = None
        try:
            sdk_client = self._anthropic_client_factory(snapshot, http_client)
            await sdk_client.messages.create(
                model=snapshot.deepseek_model,
                max_tokens=2,
                messages=[{"role": "user", "content": "回复 OK"}],
            )
        except Exception as error:
            error_code = self._map_error("deepseek", error)
        finally:
            error_code = await self._close_clients(
                "deepseek",
                sdk_client,
                http_client,
                existing_error=error_code,
            )
        return error_code

    async def _test_hostex(
        self,
        snapshot: RuntimeConfigSnapshot,
    ) -> RuntimeConfigProviderTestResult:
        """百居易只调用 GET /properties，并始终关闭临时客户端。"""
        client: Any | None = None
        error_code: str | None = None
        try:
            client = self._hostex_client_factory(snapshot)
            await client.probe_read_only()
        except Exception as error:
            error_code = self._map_error("hostex", error)
        finally:
            error_code = await self._close_clients(
                "hostex",
                client,
                existing_error=error_code,
            )
        return RuntimeConfigProviderTestResult(
            "hostex",
            error_code is None,
            error_code,
            checks=(
                RuntimeConfigCheckTestResult(
                    "properties",
                    error_code is None,
                    error_code,
                ),
            ),
        )

    async def _test_wecom(
        self,
        snapshot: RuntimeConfigSnapshot,
    ) -> RuntimeConfigProviderTestResult:
        """本地自检回调加解密，再只读验证 KF、Agent 与可选通讯录权限。"""
        callback_error: str | None = None
        try:
            self._verify_callback_locally(snapshot)
        except (ValueError, binascii.Error):
            callback_error = "wecom_callback_invalid"

        client: Any | None = None
        checks: list[RuntimeConfigCheckTestResult] = []
        cleanup_error: str | None = None
        try:
            client = self._wecom_client_factory(snapshot)
            checks.append(
                await self._run_wecom_check(
                    "kf",
                    client.probe_kf_credentials,
                )
            )
            checks.append(
                await self._run_wecom_check(
                    "agent",
                    lambda: client.probe_agent_credentials(
                        agent_id=snapshot.wecom_agent_id
                    ),
                )
            )
            if snapshot.wecom_contact_secret is not None:
                checks.append(
                    await self._run_wecom_check(
                        "contact",
                        client.probe_contact_permissions,
                    )
                )
        except Exception as error:
            # 只有构造器失败才会来到这里；已构造客户端的细项各自隔离。
            constructor_error = self._map_error("wecom", error)
            checks.extend(
                [
                    RuntimeConfigCheckTestResult("kf", False, constructor_error),
                    RuntimeConfigCheckTestResult("agent", False, constructor_error),
                ]
            )
            if snapshot.wecom_contact_secret is not None:
                checks.append(
                    RuntimeConfigCheckTestResult("contact", False, constructor_error)
                )
        finally:
            cleanup_error = await self._close_clients(
                "wecom",
                client,
                existing_error=None,
            )
        checks.append(
            RuntimeConfigCheckTestResult(
                "callback",
                callback_error is None,
                callback_error,
                verification="local_only",
            )
        )
        first_api_error = next(
            (item.error_code for item in checks if not item.succeeded),
            None,
        )
        error_code = cleanup_error or first_api_error
        return RuntimeConfigProviderTestResult(
            "wecom",
            error_code is None,
            error_code,
            callback_verification="local_only",
            checks=tuple(checks),
        )

    async def _run_wecom_check(
        self,
        name: str,
        probe: Callable[[], Awaitable[None]],
    ) -> RuntimeConfigCheckTestResult:
        """隔离单项企业微信只读探针，确保后续 Secret 仍会继续验证。"""
        try:
            await probe()
        except Exception as error:
            error_code = self._map_error("wecom", error)
            return RuntimeConfigCheckTestResult(name, False, error_code)
        return RuntimeConfigCheckTestResult(name, True)

    @staticmethod
    def _verify_callback_locally(snapshot: RuntimeConfigSnapshot) -> None:
        """严格解码 AESKey 并合成密文完成签名、解密和 CorpID 自检。"""
        key = base64.b64decode(
            f"{snapshot.wecom_encoding_aes_key}=",
            validate=True,
        )
        if len(key) != 32:
            raise ValueError("EncodingAESKey 长度无效")
        message = b"<xml><Event>runtime_config_probe</Event></xml>"
        plaintext = (
            os.urandom(16)
            + struct.pack("!I", len(message))
            + message
            + snapshot.wecom_corp_id.encode()
        )
        padder = PKCS7(256).padder()
        padded = padder.update(plaintext) + padder.finalize()
        encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
        encrypted = base64.b64encode(
            encryptor.update(padded) + encryptor.finalize()
        ).decode()
        timestamp = "1"
        nonce = "runtime-config"
        signature = hashlib.sha1(
            "".join(
                sorted(
                    [snapshot.wecom_callback_token, timestamp, nonce, encrypted]
                )
            ).encode()
        ).hexdigest()
        decrypted = WeComCallbackCrypto(
            snapshot.wecom_callback_token,
            snapshot.wecom_encoding_aes_key,
            snapshot.wecom_corp_id,
        ).decrypt(encrypted, signature, timestamp, nonce)
        if decrypted != message:
            raise ValueError("企业微信回调本地自检失败")

    @classmethod
    async def _close_clients(
        cls,
        provider: str,
        *clients: Any | None,
        existing_error: str | None,
    ) -> str | None:
        """关闭全部已构造客户端；关闭失败也只返回供应商级稳定码。"""
        close_failed = False
        for client in clients:
            if client is None:
                continue
            close = getattr(client, "close", None) or getattr(client, "aclose", None)
            if close is None:
                close_failed = True
                continue
            try:
                await close()
            except Exception:
                close_failed = True
        if existing_error is not None:
            return existing_error
        return f"{provider}_cleanup_failed" if close_failed else None

    @staticmethod
    def _map_error(provider: str, error: Exception) -> str:
        """把异常映射为供应商级稳定码，绝不返回异常正文。"""
        if isinstance(error, OutboundUrlRejected):
            return "deepseek_url_blocked"
        if isinstance(error, OutboundRedirectRejected):
            return "deepseek_redirect_rejected"
        if isinstance(error, OutboundResponseTooLarge):
            return "deepseek_response_too_large"
        if isinstance(error, (TimeoutError, httpx.TimeoutException)):
            return f"{provider}_timeout"
        if isinstance(error, WeComApiError):
            if error.error_code in _WECOM_TRUSTED_IP_CODES:
                return "wecom_trusted_ip_required"
            if error.error_code in _WECOM_AUTH_CODES:
                return "wecom_auth_failed"
            if error.error_code == 45009:
                return "wecom_rate_limited"
            return "wecom_api_failed"
        if isinstance(error, HostexBusinessError):
            if error.error_code in {401, 403}:
                return "hostex_auth_failed"
            if error.error_code == 429:
                return "hostex_rate_limited"
            return "hostex_api_failed"
        status_code = getattr(error, "status_code", None)
        if isinstance(error, httpx.HTTPStatusError):
            status_code = error.response.status_code
        if status_code in {401, 403}:
            return f"{provider}_auth_failed"
        if status_code == 429:
            return f"{provider}_rate_limited"
        if isinstance(error, (httpx.TransportError, ValueError, TypeError, KeyError)):
            return f"{provider}_connection_failed"
        return f"{provider}_unavailable"
