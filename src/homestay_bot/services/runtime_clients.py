"""为可热切换的外部集成客户端提供不可变快照和请求租约。"""

import asyncio
import contextlib
import inspect
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from homestay_bot.domain.runtime_config import RuntimeConfigSnapshot
from homestay_bot.integrations.deepseek_client import (
    DeepSeekGuestAssistant,
    HostexReadOnlyToolExecutor,
)
from homestay_bot.integrations.deepseek_complaint import DeepSeekComplaintAnalyzer
from homestay_bot.integrations.deepseek_context_summarizer import DeepSeekContextSummarizer
from homestay_bot.integrations.deepseek_faq_drafter import DeepSeekFaqDrafter
from homestay_bot.integrations.deepseek_tourism import DeepSeekTourismSearcher
from homestay_bot.integrations.hostex_client import HostexClient
from homestay_bot.integrations.wecom.api_client import WeComApiClient
from homestay_bot.integrations.wecom.contact_client import WeComContactClient
from homestay_bot.routes.hostex_webhook import HostexWebhookService
from homestay_bot.routes.wecom_callback import WeComCallbackService
from homestay_bot.services.lifecycle_reminders import TourismReminderWeatherProvider
from homestay_bot.services.outbound_url_policy import (
    OutboundUrlPolicy,
    build_public_https_client,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _BundleCloseState:
    """保存不可变 bundle 内部唯一允许变化的关闭状态。"""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: bool = False


@dataclass(frozen=True, slots=True)
class RuntimeClientBundle:
    """固定一个 revision 的全部外部客户端、校验器和调度参数。"""

    revision: int
    hostex: Any
    wecom: Any
    contact_client: Any | None
    assistant: Any
    faq_drafter: Any
    tourism_searcher: Any
    reminder_weather: Any
    complaint_analyzer: Any
    context_summarizer: Any
    wecom_callback_service: Any
    hostex_webhook_service: Any
    agent_id: int
    duty_userids: tuple[str, ...]
    wecom_poll_interval_seconds: float
    hostex_reconcile_interval_seconds: float
    closeables: tuple[Any, ...] = field(repr=False, compare=False)
    _close_state: _BundleCloseState = field(
        default_factory=_BundleCloseState,
        repr=False,
        compare=False,
    )

    async def aclose(self) -> None:
        """按所有权清单关闭全部资源；重复调用不会重复释放连接池。"""
        async with self._close_state.lock:
            if self._close_state.closed:
                return
            self._close_state.closed = True
            first_error: BaseException | None = None
            unique_resources = list({id(item): item for item in self.closeables}.values())
            for resource in reversed(unique_resources):
                try:
                    await _close_resource(resource)
                except BaseException as error:
                    # 单个连接池关闭失败不能阻止其余资源得到释放。
                    if first_error is None:
                        first_error = error
            if first_error is not None:
                raise first_error


@dataclass(slots=True)
class _RegistryEntry:
    """记录一个已发布 bundle 的活动租约数和退役状态。"""

    bundle: RuntimeClientBundle
    leases: int = 0
    retired: bool = False


class RuntimeClientRegistry:
    """以短锁原子发布 bundle，并在最后旧租约退出后延迟关闭。"""

    def __init__(
        self,
        initial: RuntimeClientBundle,
        *,
        resource_health_setter: Callable[[bool], None] | None = None,
    ) -> None:
        """发布初始运行快照。"""
        self._lock = asyncio.Lock()
        self._entries = {id(initial): _RegistryEntry(initial)}
        self._current_id = id(initial)
        self._closed = False
        self._all_released = asyncio.Event()
        self._resource_health_setter = resource_health_setter

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[RuntimeClientBundle]:
        """租用一次业务操作所需的当前快照，并保证操作期间不被关闭。"""
        async with self._lock:
            if self._closed:
                raise RuntimeError("运行客户端注册表已关闭")
            entry = self._entries[self._current_id]
            entry.leases += 1
        try:
            yield entry.bundle
        finally:
            await self._release(entry)

    async def swap(self, candidate: RuntimeClientBundle) -> None:
        """立即发布候选；旧快照无租约时在锁外关闭，避免阻塞新请求。"""
        retired_bundle: RuntimeClientBundle | None = None
        async with self._lock:
            if self._closed:
                raise RuntimeError("运行客户端注册表已关闭")
            if id(candidate) in self._entries:
                raise ValueError("运行客户端候选已发布")
            previous = self._entries[self._current_id]
            previous.retired = True
            self._entries[id(candidate)] = _RegistryEntry(candidate)
            self._current_id = id(candidate)
            if previous.leases == 0:
                self._entries.pop(id(previous.bundle), None)
                retired_bundle = previous.bundle
        if retired_bundle is not None:
            await self._close_retired(retired_bundle)

    async def close(self) -> None:
        """停止新租约，并等待所有在途业务退出后关闭全部 bundle。"""
        bundles_to_close: list[RuntimeClientBundle] = []
        async with self._lock:
            if self._closed:
                wait_for_releases = bool(self._entries)
            else:
                self._closed = True
                for entry in self._entries.values():
                    entry.retired = True
                    if entry.leases == 0:
                        bundles_to_close.append(entry.bundle)
                for bundle in bundles_to_close:
                    self._entries.pop(id(bundle), None)
                wait_for_releases = bool(self._entries)
                if not wait_for_releases:
                    self._all_released.set()
        first_error: BaseException | None = None
        for bundle in bundles_to_close:
            try:
                await bundle.aclose()
            except BaseException as error:
                if first_error is None:
                    first_error = error
        if wait_for_releases:
            await self._all_released.wait()
        if first_error is not None:
            raise first_error

    async def _release(self, entry: _RegistryEntry) -> None:
        """释放一个租约，并在退役 bundle 的最后租约退出时关闭它。"""
        bundle_to_close: RuntimeClientBundle | None = None
        async with self._lock:
            entry.leases -= 1
            if entry.leases < 0:
                raise RuntimeError("运行客户端租约计数无效")
            if entry.retired and entry.leases == 0:
                self._entries.pop(id(entry.bundle), None)
                bundle_to_close = entry.bundle
        try:
            if bundle_to_close is not None:
                await self._close_retired(bundle_to_close)
        finally:
            async with self._lock:
                if self._closed and not self._entries:
                    self._all_released.set()

    async def _close_retired(self, bundle: RuntimeClientBundle) -> None:
        """旧bundle清理失败只降级资源健康，不能反转已完成的发布。"""
        try:
            await bundle.aclose()
        except BaseException as error:
            logger.warning(
                "退役运行客户端关闭失败：error_type=%s",
                type(error).__name__,
            )
            if self._resource_health_setter is not None:
                self._resource_health_setter(False)
        else:
            if self._resource_health_setter is not None:
                self._resource_health_setter(True)


async def _close_partial_resources(resources: list[Any]) -> None:
    """构造失败时逆序关闭已成功接管的唯一资源。"""
    unique_resources = list({id(item): item for item in resources}.values())
    for resource in reversed(unique_resources):
        # 保留原始构造异常；清理异常不得覆盖真正失败原因。
        with contextlib.suppress(BaseException):
            await _close_resource(resource)


async def _close_resource(resource: Any) -> None:
    """兼容SDK的close与HTTP客户端的aclose，并等待异步返回值。"""
    close = getattr(resource, "aclose", None) or getattr(resource, "close", None)
    if not callable(close):
        raise TypeError("运行客户端资源缺少关闭方法")
    result = close()
    if inspect.isawaitable(result):
        await result


async def build_runtime_client_bundle(
    snapshot: RuntimeConfigSnapshot,
    *,
    revision: int,
    callback_queue: Any,
    hostex_event_recorder: Any,
    knowledge: Any,
    faq_candidate_context: Any,
    safety_hmac_key: bytes,
    web_search_status_setter: Callable[[Any], None],
    outbound_url_policy: OutboundUrlPolicy | None = None,
) -> RuntimeClientBundle:
    """从完整快照构造一个 revision 的生产客户端，并明确连接池所有权。"""
    snapshot.validate()
    policy = outbound_url_policy or OutboundUrlPolicy()
    owned: list[Any] = []
    try:
        # SDK 成功构造后接管注入的 HTTP 客户端；构造失败时 builder 直接关闭裸客户端。
        openai_http = build_public_https_client(policy)
        owned.append(openai_http)
        deepseek_chat = AsyncOpenAI(
            api_key=snapshot.deepseek_api_key,
            base_url=snapshot.deepseek_base_url,
            http_client=openai_http,
            max_retries=0,
        )
        owned[-1] = deepseek_chat

        anthropic_http = build_public_https_client(policy)
        owned.append(anthropic_http)
        deepseek_anthropic = AsyncAnthropic(
            api_key=snapshot.deepseek_api_key,
            base_url=f"{snapshot.deepseek_base_url.rstrip('/')}/anthropic",
            http_client=anthropic_http,
            max_retries=0,
        )
        owned[-1] = deepseek_anthropic

        hostex = HostexClient(snapshot.hostex_access_token)
        owned.append(hostex)
        wecom = WeComApiClient(
            snapshot.wecom_corp_id,
            snapshot.wecom_kf_secret,
            snapshot.wecom_agent_secret,
        )
        owned.append(wecom)
        contact_client = (
            WeComContactClient(
                snapshot.wecom_corp_id,
                snapshot.wecom_contact_secret,
            )
            if snapshot.wecom_contact_secret is not None
            else None
        )
        if contact_client is not None:
            owned.append(contact_client)

        tourism_searcher = DeepSeekTourismSearcher(
            client=deepseek_anthropic,
            model=snapshot.deepseek_model,
            status_setter=web_search_status_setter,
        )
        reminder_weather = TourismReminderWeatherProvider(tourism_searcher)
        assistant = DeepSeekGuestAssistant(
            chat_client=deepseek_chat,
            tourism_searcher=tourism_searcher,
            knowledge=knowledge,
            model=snapshot.deepseek_model,
            safety_hmac_key=safety_hmac_key,
            tool_executor=HostexReadOnlyToolExecutor(hostex),
            faq_candidate_context=faq_candidate_context,
        )
        duty_userids = tuple(
            dict.fromkeys(
                item.strip()
                for item in snapshot.wecom_duty_userids.split(",")
                if item.strip()
            )
        )
        return RuntimeClientBundle(
            revision=revision,
            hostex=hostex,
            wecom=wecom,
            contact_client=contact_client,
            assistant=assistant,
            faq_drafter=DeepSeekFaqDrafter(
                client=deepseek_chat,
                model=snapshot.deepseek_model,
            ),
            tourism_searcher=tourism_searcher,
            reminder_weather=reminder_weather,
            complaint_analyzer=DeepSeekComplaintAnalyzer(
                client=deepseek_chat,
                model=snapshot.deepseek_model,
            ),
            context_summarizer=DeepSeekContextSummarizer(
                deepseek_chat,
                snapshot.deepseek_model,
            ),
            wecom_callback_service=WeComCallbackService.from_credentials(
                snapshot.wecom_callback_token,
                snapshot.wecom_encoding_aes_key,
                snapshot.wecom_corp_id,
                callback_queue,
            ),
            hostex_webhook_service=HostexWebhookService(
                snapshot.hostex_webhook_secret_token,
                hostex_event_recorder,
            ),
            agent_id=snapshot.wecom_agent_id,
            duty_userids=duty_userids,
            wecom_poll_interval_seconds=snapshot.wecom_poll_interval_seconds,
            hostex_reconcile_interval_seconds=(
                snapshot.hostex_reconcile_interval_seconds
            ),
            closeables=tuple(owned),
        )
    except BaseException:
        await _close_partial_resources(owned)
        raise
