import asyncio
import gc
import weakref
from dataclasses import FrozenInstanceError

import pytest
from starlette.requests import Request

from homestay_bot.domain.runtime_config import RuntimeConfigSnapshot
from homestay_bot.routes.hostex_webhook import (
    HostexWebhookService,
    get_hostex_webhook_service,
)
from homestay_bot.routes.wecom_callback import WeComCallbackService, get_callback_service
from homestay_bot.services import runtime_clients
from homestay_bot.services.runtime_clients import (
    RuntimeClientBundle,
    RuntimeClientRegistry,
)


class CloseProbe:
    """记录运行客户端资源被关闭的次数。"""

    def __init__(self) -> None:
        """初始化关闭计数。"""
        self.calls = 0
        self.called = asyncio.Event()

    async def aclose(self) -> None:
        """记录一次异步关闭。"""
        self.calls += 1
        self.called.set()


class OrderedCloseProbe(CloseProbe):
    """记录资源关闭顺序。"""

    def __init__(self, name: str, order: list[str]) -> None:
        """保存资源名称和共享顺序列表。"""
        super().__init__()
        self._name = name
        self._order = order

    async def aclose(self) -> None:
        """记录关闭次数与名称。"""
        await super().aclose()
        self._order.append(self._name)


class FailingCloseProbe(CloseProbe):
    """模拟退役连接池关闭失败。"""

    async def aclose(self) -> None:
        """记录关闭后抛出清理异常。"""
        await super().aclose()
        raise RuntimeError("close failed")


class RetryCloseProbe(CloseProbe):
    """按指定次数失败后允许关闭成功。"""

    def __init__(self, failures: int) -> None:
        """保存剩余失败次数。"""
        super().__init__()
        self._failures = failures

    async def aclose(self) -> None:
        """失败期抛错，后续调用成功。"""
        await super().aclose()
        if self._failures > 0:
            self._failures -= 1
            raise RuntimeError("retryable close failure")


class GatedCloseProbe(CloseProbe):
    """在测试允许前阻塞关闭，并可模拟永久失败。"""

    def __init__(self, *, fail: bool = False) -> None:
        """初始化关闭门闩和失败开关。"""
        super().__init__()
        self.started = asyncio.Event()
        self.proceed = asyncio.Event()
        self.completed = asyncio.Event()
        self._fail = fail

    async def aclose(self) -> None:
        """等待测试放行后记录完成或抛出稳定清理异常。"""
        await super().aclose()
        self.started.set()
        await self.proceed.wait()
        if self._fail:
            raise RuntimeError("gated close failure")
        self.completed.set()


def build_snapshot(**overrides: object) -> RuntimeConfigSnapshot:
    """构造用于生产 bundle 装配测试的完整运行快照。"""
    values: dict[str, object] = {
        "deepseek_api_key": "deepseek-secret",
        "deepseek_base_url": "https://deepseek.example",
        "deepseek_model": "deepseek-model",
        "hostex_access_token": "hostex-secret",
        "hostex_webhook_secret_token": "webhook-secret",
        "hostex_reconcile_interval_seconds": 600.0,
        "wecom_corp_id": "corp-id",
        "wecom_kf_secret": "kf-secret",
        "wecom_callback_token": "callback-token",
        "wecom_encoding_aes_key": "A" * 43,
        "wecom_agent_id": 100001,
        "wecom_agent_secret": "agent-secret",
        "wecom_contact_secret": "contact-secret",
        "wecom_duty_userids": "owner, staff,owner",
        "wecom_poll_interval_seconds": 15.0,
    }
    values.update(overrides)
    return RuntimeConfigSnapshot(**values)  # type: ignore[arg-type]


def build_bundle(revision: int, probe: CloseProbe) -> RuntimeClientBundle:
    """构造只关注生命周期字段的最小不可变 bundle。"""
    return RuntimeClientBundle(
        revision=revision,
        hostex=object(),
        wecom=object(),
        contact_client=None,
        assistant=object(),
        delivery_rewriter=object(),
        faq_drafter=object(),
        tourism_searcher=object(),
        reminder_weather=object(),
        complaint_analyzer=object(),
        context_summarizer=object(),
        wecom_callback_service=object(),
        hostex_webhook_service=object(),
        agent_id=100001,
        duty_userids=("owner",),
        wecom_poll_interval_seconds=10.0,
        hostex_reconcile_interval_seconds=60.0,
        closeables=(probe,),
    )


def test_bundle_is_immutable() -> None:
    """运行快照发布后不得被业务代码原地修改。"""
    bundle = build_bundle(1, CloseProbe())

    with pytest.raises(FrozenInstanceError):
        bundle.revision = 2  # type: ignore[misc]


@pytest.mark.asyncio
async def test_bundle_closes_unique_resources_in_reverse_ownership_order() -> None:
    """重复登记的资源只关闭一次，并按构造的逆序释放依赖。"""
    order: list[str] = []
    first = OrderedCloseProbe("first", order)
    second = OrderedCloseProbe("second", order)
    bundle = build_bundle(1, first)
    object.__setattr__(bundle, "closeables", (first, second, first))

    await bundle.aclose()

    assert order == ["second", "first"]
    assert first.calls == 1


@pytest.mark.asyncio
async def test_bundle_retries_only_failed_resources() -> None:
    """部分关闭失败后重试失败资源，已成功资源不得二次关闭。"""
    failed_then_ok = RetryCloseProbe(failures=1)
    already_closed = CloseProbe()
    bundle = build_bundle(1, failed_then_ok)
    object.__setattr__(bundle, "closeables", (failed_then_ok, already_closed))

    with pytest.raises(RuntimeError, match="retryable close failure"):
        await bundle.aclose()
    assert failed_then_ok.calls == 1
    assert already_closed.calls == 1

    await bundle.aclose()
    assert failed_then_ok.calls == 2
    assert already_closed.calls == 1


@pytest.mark.asyncio
async def test_swap_does_not_interrupt_old_lease_and_delays_close() -> None:
    """热切换立即发布新版本，但在途旧业务完成前保留旧客户端。"""
    old_probe = CloseProbe()
    new_probe = CloseProbe()
    registry = RuntimeClientRegistry(build_bundle(1, old_probe))

    async with registry.acquire() as old_bundle:
        await asyncio.wait_for(
            registry.swap(build_bundle(2, new_probe)),
            timeout=0.1,
        )
        async with registry.acquire() as new_bundle:
            assert old_bundle.revision == 1
            assert new_bundle.revision == 2
        assert old_probe.calls == 0

    assert old_probe.calls == 1
    assert new_probe.calls == 0


@pytest.mark.asyncio
async def test_double_cancellation_cannot_interrupt_lease_release() -> None:
    """业务task连续取消两次也必须完成lease释放，使shutdown不会永久等待。"""
    probe = CloseProbe()
    registry = RuntimeClientRegistry(build_bundle(1, probe))
    entered = asyncio.Event()

    async def use_bundle() -> None:
        """持有租约直到测试取消业务。"""
        async with registry.acquire():
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(use_bundle())
    await entered.wait()
    await registry._lock.acquire()  # noqa: SLF001
    try:
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
    finally:
        registry._lock.release()  # noqa: SLF001

    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(registry.close(), timeout=0.1)
    assert probe.calls == 1


@pytest.mark.asyncio
async def test_swap_returns_after_publish_and_close_waits_for_retirement() -> None:
    """swap发布后立即返回，shutdown则必须等待已跟踪的退役清理。"""
    old_probe = GatedCloseProbe()
    second_probe = GatedCloseProbe()
    registry = RuntimeClientRegistry(build_bundle(1, old_probe))
    swap_task = asyncio.create_task(registry.swap(build_bundle(2, second_probe)))
    await old_probe.started.wait()

    returned_while_retirement_blocked = swap_task.done()
    old_probe.proceed.set()
    await swap_task
    assert returned_while_retirement_blocked is True

    # 再次制造退役清理，证明shutdown会等待后台task而不是提前返回。
    await registry.swap(build_bundle(3, CloseProbe()))
    await second_probe.started.wait()
    close_task = asyncio.create_task(registry.close())
    await asyncio.sleep(0)
    assert close_task.done() is False

    second_probe.proceed.set()
    await asyncio.wait_for(close_task, timeout=0.1)
    assert old_probe.completed.is_set()


@pytest.mark.asyncio
async def test_status_reads_current_bundle_metadata_without_exposing_secrets() -> None:
    """状态快照随swap更新，只暴露健康判断所需的非敏感字段。"""
    registry = RuntimeClientRegistry(
        build_bundle(1, CloseProbe()),
        configuration_healthy=False,
    )
    candidate = build_bundle(2, CloseProbe())
    object.__setattr__(candidate, "contact_client", object())
    object.__setattr__(candidate, "duty_userids", ())
    object.__setattr__(candidate, "wecom_poll_interval_seconds", 7.0)
    object.__setattr__(candidate, "hostex_reconcile_interval_seconds", 21.0)

    initial = await registry.status()
    await registry.swap(candidate)
    current = await registry.status()

    assert initial.revision == 1
    assert initial.has_duty is True
    assert initial.contact_configured is False
    assert initial.configuration_healthy is False
    assert current.revision == 2
    assert current.has_duty is False
    assert current.contact_configured is True
    assert current.wecom_poll_interval_seconds == 7.0
    assert current.hostex_reconcile_interval_seconds == 21.0
    assert current.resources_healthy is True
    assert current.configuration_healthy is True
    assert not hasattr(current, "hostex")
    await registry.close()


@pytest.mark.asyncio
async def test_concurrent_swaps_close_every_retired_bundle_once() -> None:
    """并发切换按锁串行发布，所有退出活动集的 bundle 仅关闭一次。"""
    probes = [CloseProbe() for _ in range(3)]
    registry = RuntimeClientRegistry(build_bundle(1, probes[0]))

    await asyncio.gather(
        registry.swap(build_bundle(2, probes[1])),
        registry.swap(build_bundle(3, probes[2])),
    )

    async with registry.acquire() as current:
        assert current.revision in {2, 3}
    assert sorted(probe.calls for probe in probes) == [0, 1, 1]

    await registry.close()
    assert [probe.calls for probe in probes].count(1) == 3


@pytest.mark.asyncio
async def test_retired_bundle_cannot_be_published_again() -> None:
    """registry永久记住已接管对象，退役并关闭后也拒绝重新发布同一bundle。"""
    retired = build_bundle(1, CloseProbe())
    registry = RuntimeClientRegistry(retired)
    await registry.swap(build_bundle(2, CloseProbe()))

    with pytest.raises(ValueError, match="已发布"):
        await registry.swap(retired)
    await registry.close()


@pytest.mark.asyncio
async def test_failed_retired_bundle_cannot_be_published_again() -> None:
    """仍在失败清理集合中的bundle不得作为candidate重新进入活动集。"""
    retired = build_bundle(1, FailingCloseProbe())
    registry = RuntimeClientRegistry(retired)
    await registry.swap(build_bundle(2, CloseProbe()))

    with pytest.raises(ValueError, match="已发布"):
        await registry.swap(retired)
    with pytest.raises(RuntimeError, match="运行客户端资源关闭失败"):
        await registry.close()


@pytest.mark.asyncio
async def test_bundle_cannot_be_claimed_by_two_registries() -> None:
    """同bundle的claim属于对象自身，不得被另一registry重复接管。"""
    bundle = build_bundle(1, CloseProbe())
    registry = RuntimeClientRegistry(bundle)

    with pytest.raises(ValueError, match="已发布"):
        RuntimeClientRegistry(bundle)

    await registry.close()


@pytest.mark.asyncio
async def test_successfully_retired_bundles_are_garbage_collectable() -> None:
    """成功退役后registry不得为身份去重长期保留bundle及其秘密。"""
    initial = build_bundle(0, CloseProbe())
    registry = RuntimeClientRegistry(initial)
    retired_refs = [weakref.ref(initial)]
    del initial

    for revision in range(1, 101):
        candidate = build_bundle(revision, CloseProbe())
        await registry.swap(candidate)
        if revision < 100:
            retired_refs.append(weakref.ref(candidate))
        del candidate
    for _ in range(3):
        await asyncio.sleep(0)
    gc.collect()

    assert all(reference() is None for reference in retired_refs)
    await registry.close()


@pytest.mark.asyncio
async def test_in_progress_retirement_holds_bundle_only_until_cleanup_finishes() -> None:
    """进行中的退役task强持有bundle，成功结束后应立即允许回收。"""
    old_probe = GatedCloseProbe()
    retired = build_bundle(1, old_probe)
    retired_ref = weakref.ref(retired)
    registry = RuntimeClientRegistry(retired)
    del retired

    await registry.swap(build_bundle(2, CloseProbe()))
    await old_probe.started.wait()
    gc.collect()
    assert retired_ref() is not None

    old_probe.proceed.set()
    for _ in range(3):
        await asyncio.sleep(0)
    gc.collect()
    assert retired_ref() is None
    await registry.close()


@pytest.mark.asyncio
async def test_retired_close_failure_does_not_turn_published_swap_into_failure() -> None:
    """发布点之后的旧资源清理异常不得诱发DB补偿并关闭当前candidate。"""
    old_probe = FailingCloseProbe()
    new_probe = CloseProbe()
    registry = RuntimeClientRegistry(build_bundle(1, old_probe))

    await registry.swap(build_bundle(2, new_probe))

    async with registry.acquire() as current:
        assert current.revision == 2
    assert old_probe.calls == 1
    assert new_probe.calls == 0
    with pytest.raises(RuntimeError, match="运行客户端资源关闭失败"):
        await registry.close()
    assert old_probe.calls == 2
    assert new_probe.calls == 1

    # 每次 shutdown 调用只重试一次，不在永久失败资源上循环等待。
    with pytest.raises(RuntimeError, match="运行客户端资源关闭失败"):
        await registry.close()
    assert old_probe.calls == 3
    assert new_probe.calls == 1


@pytest.mark.asyncio
async def test_resource_health_recovers_only_after_failed_bundle_is_retried() -> None:
    """无关bundle关闭成功不能掩盖旧失败，只有旧资源确认关闭才恢复健康。"""
    health: list[bool] = []
    failed_a = RetryCloseProbe(failures=1)
    closed_b = CloseProbe()
    current_c = CloseProbe()
    registry = RuntimeClientRegistry(
        build_bundle(1, failed_a),
        resource_health_setter=health.append,
    )

    await registry.swap(build_bundle(2, closed_b))
    await failed_a.called.wait()
    assert health[-1] is False

    await registry.swap(build_bundle(3, current_c))
    await closed_b.called.wait()
    assert closed_b.calls == 1
    assert health[-1] is False

    await registry.retry_failed_closes()
    assert failed_a.calls == 2
    assert health[-1] is True
    await registry.close()


@pytest.mark.asyncio
async def test_shutdown_retries_failed_bundle_without_reclosing_successes() -> None:
    """shutdown重试遗留失败资源，同时不重复关闭bundle中已成功的资源。"""
    failed_then_ok = RetryCloseProbe(failures=1)
    already_closed = CloseProbe()
    bundle = build_bundle(1, failed_then_ok)
    object.__setattr__(bundle, "closeables", (failed_then_ok, already_closed))
    registry = RuntimeClientRegistry(bundle)

    await registry.swap(build_bundle(2, CloseProbe()))
    await failed_then_ok.called.wait()
    assert (failed_then_ok.calls, already_closed.calls) == (1, 1)

    await registry.close()
    assert (failed_then_ok.calls, already_closed.calls) == (2, 1)


@pytest.mark.asyncio
async def test_close_rejects_new_leases_and_waits_for_existing_lease() -> None:
    """应用退出禁止新业务进入，并等待最后租约释放后关闭全部资源。"""
    probe = CloseProbe()
    registry = RuntimeClientRegistry(build_bundle(1, probe))
    lease = registry.acquire()
    await lease.__aenter__()

    close_task = asyncio.create_task(registry.close())
    await asyncio.sleep(0)
    assert close_task.done() is False
    with pytest.raises(RuntimeError, match="已关闭"):
        async with registry.acquire():
            pass

    await lease.__aexit__(None, None, None)
    await asyncio.wait_for(close_task, timeout=0.1)
    assert probe.calls == 1


@pytest.mark.asyncio
async def test_concurrent_close_callers_share_in_progress_attempt() -> None:
    """并发close共享当前attempt，后来的调用不能在资源实际关闭前返回。"""
    probe = GatedCloseProbe()
    registry = RuntimeClientRegistry(build_bundle(1, probe))

    first = asyncio.create_task(registry.close())
    await probe.started.wait()
    second = asyncio.create_task(registry.close())
    await asyncio.sleep(0)
    assert second.done() is False

    probe.proceed.set()
    await asyncio.gather(first, second)
    assert probe.calls == 1


@pytest.mark.asyncio
async def test_concurrent_close_callers_share_error_and_later_call_retries() -> None:
    """同期close获得同一稳定错误，后续新调用仅发起一次有限重试。"""
    probe = GatedCloseProbe(fail=True)
    registry = RuntimeClientRegistry(build_bundle(1, probe))

    first = asyncio.create_task(registry.close())
    await probe.started.wait()
    second = asyncio.create_task(registry.close())
    probe.proceed.set()
    results = await asyncio.gather(first, second, return_exceptions=True)

    assert all(
        isinstance(result, RuntimeError)
        and str(result) == "运行客户端资源关闭失败"
        for result in results
    )
    assert probe.calls == 2
    with pytest.raises(RuntimeError, match="运行客户端资源关闭失败"):
        await registry.close()
    assert probe.calls == 3


@pytest.mark.asyncio
async def test_failed_candidate_is_closed_without_replacing_current() -> None:
    """调用方可安全丢弃失败候选，registry 当前版本保持不变。"""
    old_probe = CloseProbe()
    candidate_probe = CloseProbe()
    registry = RuntimeClientRegistry(build_bundle(1, old_probe))
    candidate = build_bundle(2, candidate_probe)

    await candidate.aclose()

    async with registry.acquire() as current:
        assert current.revision == 1
    assert old_probe.calls == 0
    assert candidate_probe.calls == 1
    await registry.close()


@pytest.mark.asyncio
async def test_production_bundle_uses_controlled_http_clients_for_both_sdks(
    monkeypatch,
) -> None:
    """生产 OpenAI 与 Anthropic SDK 必须分别复用受控 HTTPS 客户端。"""
    created_http_clients = [CloseProbe(), CloseProbe()]
    pending_http_clients = list(created_http_clients)
    sdk_clients: list[CloseProbe] = []
    sdk_kwargs: list[dict[str, object]] = []
    http_client_kwargs: list[dict[str, object]] = []

    def build_http_client(policy, **kwargs):
        """依次返回两个可识别客户端，并记录生产超时配置。"""
        http_client_kwargs.append(kwargs)
        return pending_http_clients.pop(0)

    def build_sdk(**kwargs):
        """记录 SDK 构造参数并返回可关闭客户端。"""
        sdk_kwargs.append(kwargs)
        client = CloseProbe()
        sdk_clients.append(client)
        return client

    monkeypatch.setattr(runtime_clients, "build_public_https_client", build_http_client)
    monkeypatch.setattr(runtime_clients, "AsyncOpenAI", build_sdk)
    monkeypatch.setattr(runtime_clients, "AsyncAnthropic", build_sdk)

    bundle = await runtime_clients.build_runtime_client_bundle(
        build_snapshot(),
        revision=7,
        callback_queue=object(),
        hostex_event_recorder=object(),
        knowledge=object(),
        faq_candidate_context=object(),
        safety_hmac_key=b"safety-key",
        web_search_status_setter=lambda value: None,
    )

    assert bundle.revision == 7
    assert bundle.agent_id == 100001
    assert bundle.duty_userids == ("owner", "staff")
    assert bundle.wecom_poll_interval_seconds == 15.0
    assert bundle.hostex_reconcile_interval_seconds == 600.0
    assert len(sdk_kwargs) == 2
    assert all("http_client" in kwargs for kwargs in sdk_kwargs)
    assert sdk_kwargs[0]["http_client"] is not sdk_kwargs[1]["http_client"]
    assert all(kwargs["max_retries"] == 0 for kwargs in sdk_kwargs)
    assert http_client_kwargs == [
        {"timeout_seconds": 45.0},
        {"timeout_seconds": 45.0},
    ]

    await bundle.aclose()
    assert all(client.calls == 1 for client in sdk_clients)


@pytest.mark.asyncio
async def test_bundle_constructor_failure_closes_partial_resources(monkeypatch) -> None:
    """候选构造中途失败时释放已创建资源，且不产生可发布 bundle。"""
    created_http_clients = [CloseProbe(), CloseProbe()]
    pending_http_clients = list(created_http_clients)

    class SdkProbe(CloseProbe):
        """模拟 SDK 负责关闭注入的 HTTP 客户端。"""

        def __init__(self, http_client: CloseProbe) -> None:
            """保存 SDK 拥有的受控客户端。"""
            super().__init__()
            self._http_client = http_client

        async def aclose(self) -> None:
            """关闭 SDK 并级联关闭其受控 HTTP 客户端。"""
            await super().aclose()
            await self._http_client.aclose()

    monkeypatch.setattr(
        runtime_clients,
        "build_public_https_client",
        lambda policy, **kwargs: pending_http_clients.pop(0),
    )
    sdk_clients: list[SdkProbe] = []

    def build_openai(**kwargs):
        """让 fake SDK 采用与生产SDK相同的HTTP所有权语义。"""
        client = SdkProbe(kwargs["http_client"])
        sdk_clients.append(client)
        return client

    monkeypatch.setattr(runtime_clients, "AsyncOpenAI", build_openai)

    def reject_anthropic(**kwargs):
        """模拟第二个 SDK 构造失败。"""
        raise RuntimeError("anthropic construction failed")

    monkeypatch.setattr(runtime_clients, "AsyncAnthropic", reject_anthropic)

    with pytest.raises(RuntimeError, match="anthropic construction failed"):
        await runtime_clients.build_runtime_client_bundle(
            build_snapshot(),
            revision=8,
            callback_queue=object(),
            hostex_event_recorder=object(),
            knowledge=object(),
            faq_candidate_context=object(),
            safety_hmac_key=b"safety-key",
            web_search_status_setter=lambda value: None,
        )

    assert sdk_clients[0].calls == 1
    # OpenAI HTTP 由已构造SDK级联关闭，Anthropic HTTP因SDK未构造而由builder直接关闭。
    assert all(client.calls == 1 for client in created_http_clients)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dependency", "service_field"),
    [
        (get_callback_service, "wecom_callback_service"),
        (get_hostex_webhook_service, "hostex_webhook_service"),
    ],
)
async def test_request_dependency_holds_bundle_lease_until_request_finishes(
    dependency,
    service_field: str,
) -> None:
    """回调与Webhook依赖应让验签、读取和入库全程使用同一revision。"""
    old_probe = CloseProbe()
    new_probe = CloseProbe()
    old_bundle = build_bundle(1, old_probe)
    new_bundle = build_bundle(2, new_probe)
    object.__setattr__(
        old_bundle,
        service_field,
        (
            WeComCallbackService.__new__(WeComCallbackService)
            if service_field == "wecom_callback_service"
            else HostexWebhookService.__new__(HostexWebhookService)
        ),
    )
    registry = RuntimeClientRegistry(old_bundle)
    app = type("App", (), {"state": type("State", (), {})()})()
    app.state.runtime_client_registry = registry
    request = Request({"type": "http", "app": app})

    lease_dependency = dependency(request)
    service = await anext(lease_dependency)
    assert service is getattr(old_bundle, service_field)

    await registry.swap(new_bundle)
    assert old_probe.calls == 0
    await lease_dependency.aclose()
    assert old_probe.calls == 1
    await registry.close()
