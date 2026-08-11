import base64
import os
import socket
from typing import Any

import httpx
import pytest

from homestay_bot.domain.runtime_config import RuntimeConfigSnapshot
from homestay_bot.integrations.hostex_client import HostexBusinessError, HostexClient
from homestay_bot.integrations.wecom.api_client import WeComApiError
from homestay_bot.services.outbound_url_policy import (
    OutboundUrlPolicy,
    OutboundUrlRejected,
    PublicHttpsTransport,
)
from homestay_bot.services.runtime_config_tester import (
    RuntimeConfigTester,
    build_probe_anthropic_client,
    build_probe_openai_client,
)


def build_snapshot(**overrides: object) -> RuntimeConfigSnapshot:
    """构造不含真实凭据的完整候选快照。"""
    values: dict[str, object] = {
        "deepseek_api_key": "test-deepseek-secret",
        "deepseek_base_url": "https://api.deepseek.example/v1",
        "deepseek_model": "deepseek-v4-flash",
        "hostex_access_token": "test-hostex-secret",
        "hostex_webhook_secret_token": "test-hostex-webhook",
        "hostex_reconcile_interval_seconds": 900.0,
        "wecom_corp_id": "test-corp",
        "wecom_kf_secret": "test-kf-secret",
        "wecom_callback_token": "TestToken123",
        "wecom_encoding_aes_key": base64.b64encode(os.urandom(32)).decode().rstrip("="),
        "wecom_agent_id": 1000002,
        "wecom_agent_secret": "test-agent-secret",
        "wecom_contact_secret": None,
        "wecom_duty_userids": "owner",
        "wecom_poll_interval_seconds": 10.0,
    }
    values.update(overrides)
    return RuntimeConfigSnapshot(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_real_sdk_factories_reuse_controlled_http_clients_and_disable_retries() -> None:
    """两套真实 SDK 必须使用注入的受控 HTTP client，且 SDK 自身不得重试。"""
    openai_http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: None))
    anthropic_http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: None))
    openai = build_probe_openai_client(build_snapshot(), openai_http)
    anthropic = build_probe_anthropic_client(build_snapshot(), anthropic_http)
    try:
        assert openai._client is openai_http  # noqa: SLF001 - 安全装配契约
        assert anthropic._client is anthropic_http  # noqa: SLF001 - 安全装配契约
        assert openai.max_retries == 0
        assert anthropic.max_retries == 0
        assert str(anthropic.base_url).endswith("/v1/anthropic/")
    finally:
        await openai.close()
        await anthropic.close()


class OpenAICompletionsStub:
    """模拟 AsyncOpenAI 的 chat.completions 资源。"""

    def __init__(self, error: Exception | None = None) -> None:
        """保存可选异常与请求。"""
        self.error = error
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> object:
        """记录最小结构化对话调用。"""
        self.requests.append(kwargs)
        if self.error is not None:
            raise self.error
        return object()


class OpenAIClientStub:
    """模拟受控 AsyncOpenAI 客户端。"""

    def __init__(self, error: Exception | None = None) -> None:
        """保存资源树和关闭状态。"""
        self.chat = type("ChatStub", (), {"completions": OpenAICompletionsStub(error)})()
        self.closed = False

    async def close(self) -> None:
        """记录 SDK 客户端已关闭。"""
        self.closed = True


class AnthropicMessagesStub:
    """模拟 AsyncAnthropic 的 messages 资源。"""

    def __init__(self, error: Exception | None = None) -> None:
        """保存可选异常与请求。"""
        self.error = error
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> object:
        """记录最短 Anthropic 兼容调用。"""
        self.requests.append(kwargs)
        if self.error is not None:
            raise self.error
        return object()


class AnthropicClientStub:
    """模拟受控 AsyncAnthropic 客户端。"""

    def __init__(self, error: Exception | None = None) -> None:
        """保存资源和关闭状态。"""
        self.messages = AnthropicMessagesStub(error)
        self.closed = False

    async def close(self) -> None:
        """记录 SDK 客户端已关闭。"""
        self.closed = True


class HttpClientStub:
    """记录传给两个 SDK 的受控 HTTP 客户端释放状态。"""

    def __init__(self) -> None:
        """初始化关闭状态。"""
        self.closed = False

    async def aclose(self) -> None:
        """记录 HTTP 连接池已关闭。"""
        self.closed = True


class HostexClientStub:
    """只暴露百居易只读探针，并记录释放状态。"""

    def __init__(self, error: Exception | None = None) -> None:
        """保存可选异常。"""
        self.error = error
        self.calls: list[str] = []
        self.closed = False

    async def probe_read_only(self) -> None:
        """只允许房源列表探针。"""
        self.calls.append("list_properties")
        if self.error is not None:
            raise self.error

    async def aclose(self) -> None:
        """记录候选客户端已关闭。"""
        self.closed = True


class WeComClientStub:
    """只暴露客服和应用鉴权探针，并记录释放状态。"""

    def __init__(self, error: Exception | None = None) -> None:
        """保存可选异常。"""
        self.error = error
        self.calls: list[str] = []
        self.closed = False

    async def probe_kf_credentials(self) -> None:
        """模拟客服 token 与列表探针。"""
        self.calls.extend(["kf_token", "list_kf_accounts"])
        if self.error is not None:
            raise self.error

    async def probe_agent_credentials(self, *, agent_id: int) -> None:
        """模拟指定 AgentId 的读取探针。"""
        self.calls.append(f"agent_get:{agent_id}")
        if self.error is not None:
            raise self.error

    async def probe_contact_permissions(self) -> None:
        """模拟可选通讯录 Secret 的只读权限探针。"""
        self.calls.extend(["contact_token", "contact_permission"])
        if self.error is not None:
            raise self.error

    async def aclose(self) -> None:
        """记录候选客户端已关闭。"""
        self.closed = True


class PolicyStub:
    """记录 DeepSeek 地址预检，可模拟策略拒绝。"""

    def __init__(self, error: Exception | None = None) -> None:
        """保存可选策略异常。"""
        self.error = error
        self.urls: list[str] = []

    async def resolve(self, url: str) -> object:
        """记录地址，并返回无需暴露解析细节的占位结果。"""
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        return object()


def build_tester(
    *,
    openai: OpenAIClientStub | None = None,
    anthropic: AnthropicClientStub | None = None,
    hostex: HostexClientStub | None = None,
    wecom: WeComClientStub | None = None,
    policy: PolicyStub | None = None,
) -> tuple[
    RuntimeConfigTester,
    OpenAIClientStub,
    AnthropicClientStub,
    HostexClientStub,
    WeComClientStub,
    list[HttpClientStub],
]:
    """装配全部使用 fake 的测试器，默认绝不访问真实网络。"""
    openai = openai or OpenAIClientStub()
    anthropic = anthropic or AnthropicClientStub()
    hostex = hostex or HostexClientStub()
    wecom = wecom or WeComClientStub()
    http_clients: list[HttpClientStub] = []

    def make_http_client() -> HttpClientStub:
        """为两个 SDK 分别创建可关闭的受控 HTTP 客户端。"""
        client = HttpClientStub()
        http_clients.append(client)
        return client

    tester = RuntimeConfigTester(
        url_policy=policy or PolicyStub(),
        http_client_factory=make_http_client,
        openai_client_factory=lambda snapshot, http_client: openai,
        anthropic_client_factory=lambda snapshot, http_client: anthropic,
        hostex_client_factory=lambda snapshot: hostex,
        wecom_client_factory=lambda snapshot: wecom,
    )
    return tester, openai, anthropic, hostex, wecom, http_clients


@pytest.mark.asyncio
async def test_all_providers_succeed_with_read_only_minimal_probes_and_close() -> None:
    """三方成功时只执行最小只读请求，并在结果中标明回调仅本地校验。"""
    tester, openai, anthropic, hostex, wecom, http_clients = build_tester()

    result = await tester.test(build_snapshot())

    assert result.succeeded is True
    assert result.error_code is None
    assert result.to_safe_dict() == {
        "succeeded": True,
        "providers": {
            "deepseek": {
                "succeeded": True,
                "checks": {
                    "openai": {"succeeded": True},
                    "anthropic": {"succeeded": True},
                },
            },
            "hostex": {
                "succeeded": True,
                "checks": {"properties": {"succeeded": True}},
            },
            "wecom": {
                "succeeded": True,
                "callback_verification": "local_only",
                "checks": {
                    "kf": {"succeeded": True},
                    "agent": {"succeeded": True},
                    "callback": {"succeeded": True, "verification": "local_only"},
                },
            },
        },
    }
    openai_request = openai.chat.completions.requests[0]
    anthropic_request = anthropic.messages.requests[0]
    assert openai_request["max_tokens"] <= 2
    assert openai_request["response_format"] == {"type": "json_object"}
    assert anthropic_request["max_tokens"] <= 2
    assert hostex.calls == ["list_properties"]
    assert wecom.calls == ["kf_token", "list_kf_accounts", "agent_get:1000002"]
    assert openai.closed and anthropic.closed and hostex.closed and wecom.closed
    assert len(http_clients) == 2 and all(client.closed for client in http_clients)


@pytest.mark.asyncio
async def test_one_provider_failure_keeps_other_results_and_closes_every_client() -> None:
    """单项失败仍聚合全部分项，且任何临时客户端都不得泄漏。"""
    hostex_error = HostexBusinessError(401, "request-id", "raw secret response")
    tester, openai, anthropic, hostex, wecom, http_clients = build_tester(
        hostex=HostexClientStub(hostex_error)
    )

    result = await tester.test(build_snapshot())
    safe = result.to_safe_dict()

    assert result.succeeded is False
    assert result.error_code == "hostex_auth_failed"
    assert safe["providers"] == {
        "deepseek": {
            "succeeded": True,
            "checks": {
                "openai": {"succeeded": True},
                "anthropic": {"succeeded": True},
            },
        },
        "hostex": {
            "succeeded": False,
            "error_code": "hostex_auth_failed",
            "checks": {
                "properties": {"succeeded": False, "error_code": "hostex_auth_failed"}
            },
        },
        "wecom": {
            "succeeded": True,
            "callback_verification": "local_only",
            "checks": {
                "kf": {"succeeded": True},
                "agent": {"succeeded": True},
                "callback": {"succeeded": True, "verification": "local_only"},
            },
        },
    }
    assert "raw secret response" not in repr(safe)
    assert openai.closed and anthropic.closed and hostex.closed and wecom.closed
    assert all(client.closed for client in http_clients)


@pytest.mark.asyncio
async def test_timeout_and_trusted_ip_errors_use_stable_provider_codes() -> None:
    """超时与企业可信 IP 错误只映射为稳定码，不返回远端正文。"""
    openai = OpenAIClientStub(httpx.ReadTimeout("body contains secret"))
    wecom = WeComClientStub(WeComApiError(60020, "not allow to access from your ip: secret"))
    tester, _, _, _, _, _ = build_tester(openai=openai, wecom=wecom)

    result = await tester.test(build_snapshot())
    safe = result.to_safe_dict()

    assert result.error_code == "deepseek_timeout"
    assert safe["providers"] == {
        "deepseek": {
            "succeeded": False,
            "error_code": "deepseek_timeout",
            "checks": {
                "openai": {"succeeded": False, "error_code": "deepseek_timeout"},
                "anthropic": {"succeeded": True},
            },
        },
        "hostex": {
            "succeeded": True,
            "checks": {"properties": {"succeeded": True}},
        },
        "wecom": {
            "succeeded": False,
            "error_code": "wecom_trusted_ip_required",
            "callback_verification": "local_only",
            "checks": {
                "kf": {
                    "succeeded": False,
                    "error_code": "wecom_trusted_ip_required",
                },
                "agent": {
                    "succeeded": False,
                    "error_code": "wecom_trusted_ip_required",
                },
                "callback": {"succeeded": True, "verification": "local_only"},
            },
        },
    }
    assert "secret" not in repr(safe)


@pytest.mark.asyncio
async def test_unsafe_deepseek_url_is_rejected_before_client_creation() -> None:
    """DeepSeek 地址策略失败时不得构造可发请求的客户端。"""
    created = False
    hostex = HostexClientStub()
    wecom = WeComClientStub()

    def create_openai(snapshot: RuntimeConfigSnapshot, http_client: object) -> OpenAIClientStub:
        """记录任何越过地址策略的 SDK 客户端构造。"""
        nonlocal created
        del snapshot, http_client
        created = True
        return OpenAIClientStub()

    tester = RuntimeConfigTester(
        url_policy=PolicyStub(OutboundUrlRejected("外联地址不安全")),
        http_client_factory=HttpClientStub,
        openai_client_factory=create_openai,
        anthropic_client_factory=lambda snapshot, http_client: AnthropicClientStub(),
        hostex_client_factory=lambda snapshot: hostex,
        wecom_client_factory=lambda snapshot: wecom,
    )

    result = await tester.test(build_snapshot(deepseek_base_url="https://127.0.0.1"))

    assert created is False
    assert result.error_code == "deepseek_url_blocked"
    assert hostex.closed and wecom.closed


@pytest.mark.asyncio
async def test_invalid_callback_aes_key_fails_locally_without_skipping_api_close() -> None:
    """回调参数格式失败只做本地判定，同时企业微信 API 客户端仍正常释放。"""
    tester, openai, anthropic, hostex, wecom, http_clients = build_tester()

    result = await tester.test(build_snapshot(wecom_encoding_aes_key="!" * 43))

    assert result.succeeded is False
    assert result.to_safe_dict()["providers"]["wecom"] == {
        "succeeded": False,
        "error_code": "wecom_callback_invalid",
        "callback_verification": "local_only",
        "checks": {
            "kf": {"succeeded": True},
            "agent": {"succeeded": True},
            "callback": {
                "succeeded": False,
                "error_code": "wecom_callback_invalid",
                "verification": "local_only",
            },
        },
    }
    assert openai.closed and anthropic.closed and hostex.closed and wecom.closed
    assert all(client.closed for client in http_clients)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token",
    ["", "invalid-token", "A" * 33, "测试Token"],
)
async def test_invalid_callback_token_maps_to_local_callback_failure(token: str) -> None:
    """回调 Token 只允许 1 至 32 位英文或数字，失败不得影响 API 关闭。"""
    tester, _, _, _, wecom, _ = build_tester()

    result = await tester.test(build_snapshot(wecom_callback_token=token))
    callback = result.to_safe_dict()["providers"]["wecom"]["checks"]["callback"]

    assert callback == {
        "succeeded": False,
        "error_code": "wecom_callback_invalid",
        "verification": "local_only",
    }
    assert wecom.closed is True


@pytest.mark.asyncio
async def test_optional_contact_secret_runs_independent_read_only_permission_check() -> None:
    """配置 Contact Secret 时新增独立权限细项，不影响 KF 与 Agent 结果。"""
    tester, _, _, _, wecom, _ = build_tester()

    result = await tester.test(build_snapshot(wecom_contact_secret="contact-secret"))
    wecom_result = result.to_safe_dict()["providers"]["wecom"]

    assert wecom.calls == [
        "kf_token",
        "list_kf_accounts",
        "agent_get:1000002",
        "contact_token",
        "contact_permission",
    ]
    assert wecom_result["checks"]["contact"] == {"succeeded": True}


@pytest.mark.asyncio
async def test_http_client_factory_failure_is_isolated_to_one_deepseek_check() -> None:
    """一个受控连接池构造失败不能中断另一套 SDK 或其他供应商测试。"""
    calls = 0
    anthropic = AnthropicClientStub()
    hostex = HostexClientStub()
    wecom = WeComClientStub()

    def make_http_client() -> HttpClientStub:
        """首个 OpenAI 连接池构造失败，Anthropic 随后正常创建。"""
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("secret factory failure")
        return HttpClientStub()

    tester = RuntimeConfigTester(
        url_policy=PolicyStub(),
        http_client_factory=make_http_client,
        openai_client_factory=lambda snapshot, http_client: OpenAIClientStub(),
        anthropic_client_factory=lambda snapshot, http_client: anthropic,
        hostex_client_factory=lambda snapshot: hostex,
        wecom_client_factory=lambda snapshot: wecom,
    )

    result = await tester.test(build_snapshot())
    deepseek = result.to_safe_dict()["providers"]["deepseek"]

    assert deepseek["checks"] == {
        "openai": {"succeeded": False, "error_code": "deepseek_unavailable"},
        "anthropic": {"succeeded": True},
    }
    assert hostex.closed and wecom.closed and anthropic.closed
    assert "secret factory failure" not in repr(result.to_safe_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        ("timeout", "deepseek_timeout"),
        ("redirect", "deepseek_redirect_rejected"),
        ("too_large", "deepseek_response_too_large"),
    ],
)
async def test_real_sdks_map_wrapped_transport_causes(
    failure: str,
    expected_code: str,
) -> None:
    """两套真实 SDK 包装底层异常后，仍应按 cause 类型映射稳定码。"""

    async def resolver(host: str, port: int) -> list[tuple[object, ...]]:
        """返回固定公网地址，实际连接由 MockTransport 截获。"""
        del host, port
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443))]

    policy = OutboundUrlPolicy(resolver=resolver)

    def make_http_client() -> httpx.AsyncClient:
        """为每套 SDK 创建独立受控传输层。"""

        def responder(request: httpx.Request) -> httpx.Response:
            """在 IP 固定之后模拟三类底层失败。"""
            if failure == "timeout":
                raise httpx.ReadTimeout("secret response", request=request)
            if failure == "redirect":
                return httpx.Response(
                    302,
                    headers={"location": "https://redirect.example/secret"},
                )
            return httpx.Response(200, content=b"x" * 2049)

        return httpx.AsyncClient(
            transport=PublicHttpsTransport(
                policy,
                transport=httpx.MockTransport(responder),
                max_response_bytes=2048,
            ),
            follow_redirects=False,
            trust_env=False,
        )

    tester = RuntimeConfigTester(
        url_policy=policy,
        http_client_factory=make_http_client,
        hostex_client_factory=lambda snapshot: HostexClientStub(),
        wecom_client_factory=lambda snapshot: WeComClientStub(),
    )

    result = await tester.test(build_snapshot())
    deepseek = result.to_safe_dict()["providers"]["deepseek"]

    assert deepseek["error_code"] == expected_code
    assert deepseek["checks"] == {
        "openai": {"succeeded": False, "error_code": expected_code},
        "anthropic": {"succeeded": False, "error_code": expected_code},
    }
    assert "secret response" not in repr(result.to_safe_dict())
    assert "redirect.example" not in repr(result.to_safe_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403])
async def test_real_hostex_wrapped_http_status_maps_to_auth_failure(status_code: int) -> None:
    """HostexTransportError 包装 401/403 后仍应识别为鉴权失败。"""

    def responder(request: httpx.Request) -> httpx.Response:
        """返回没有正文依赖的鉴权状态。"""
        return httpx.Response(status_code, json={"secret": "must-not-leak"})

    hostex = HostexClient("token", transport=httpx.MockTransport(responder))
    tester, _, _, _, _, _ = build_tester(hostex=hostex)  # type: ignore[arg-type]

    result = await tester.test(build_snapshot())

    assert result.to_safe_dict()["providers"]["hostex"] == {
        "succeeded": False,
        "error_code": "hostex_auth_failed",
        "checks": {
            "properties": {"succeeded": False, "error_code": "hostex_auth_failed"}
        },
    }
    assert hostex.is_closed is True
    assert "must-not-leak" not in repr(result.to_safe_dict())
