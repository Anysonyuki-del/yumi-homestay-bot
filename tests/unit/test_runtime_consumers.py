import asyncio
from datetime import date
from types import SimpleNamespace
from typing import Any, cast

import pytest

from homestay_bot import application
from homestay_bot.application import RuntimeWorkerBindings
from homestay_bot.services.runtime_clients import RuntimeClientBundle, RuntimeClientRegistry


class CloseProbe:
    """记录旧客户端连接池何时关闭。"""

    def __init__(self) -> None:
        """初始化关闭计数。"""
        self.calls = 0

    async def aclose(self) -> None:
        """记录一次关闭。"""
        self.calls += 1


def build_bundle(revision: int, probe: CloseProbe) -> RuntimeClientBundle:
    """构造consumer测试所需的最小bundle。"""
    return RuntimeClientBundle(
        revision=revision,
        hostex=f"hostex-{revision}",
        wecom=f"wecom-{revision}",
        contact_client=None,
        assistant=f"assistant-{revision}",
        delivery_rewriter=f"delivery-rewriter-{revision}",
        faq_drafter=f"faq-{revision}",
        tourism_searcher=f"tourism-{revision}",
        reminder_weather=f"weather-{revision}",
        complaint_analyzer=f"complaint-{revision}",
        context_summarizer=f"summary-{revision}",
        wecom_callback_service=object(),
        hostex_webhook_service=object(),
        agent_id=revision,
        duty_userids=(f"duty-{revision}",),
        wecom_poll_interval_seconds=float(revision + 5),
        hostex_reconcile_interval_seconds=float(revision + 60),
        closeables=(probe,),
    )


@pytest.mark.asyncio
async def test_worker_long_job_does_not_block_swap_but_delays_old_close(monkeypatch) -> None:
    """worker租约覆盖领取到最终提交，长job期间swap立即完成且旧bundle延迟关闭。"""
    old_probe = CloseProbe()
    new_probe = CloseProbe()
    registry = RuntimeClientRegistry(build_bundle(1, old_probe))
    job_started = asyncio.Event()
    finish_job = asyncio.Event()
    seen_revisions: list[int] = []
    run_calls = 0

    class SessionContext:
        """提供worker所需最小会话。"""

        async def __aenter__(self):
            """返回带提交方法的测试会话。"""
            return SimpleNamespace(commit=self.commit)

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            """退出测试会话。"""

        async def commit(self) -> None:
            """模拟成功提交。"""

    class RepositoryStub:
        """接受worker仓储构造参数。"""

        def __init__(self, session, **kwargs) -> None:
            """忽略测试无关参数。"""

        async def recover_stale(self, *, before) -> None:
            """模拟无遗留任务。"""

    class WorkerStub:
        """把一次run_once阻塞为在途长job。"""

        def __init__(self, **kwargs) -> None:
            """接受生产装配参数。"""

        async def run_once(self) -> bool:
            """等待测试允许最终提交。"""
            nonlocal run_calls
            run_calls += 1
            if run_calls > 1:
                await asyncio.Event().wait()
            job_started.set()
            await finish_job.wait()
            return True

    async def build_bindings(session, bundle) -> RuntimeWorkerBindings:
        """记录本次job固定使用的revision。"""
        seen_revisions.append(bundle.revision)
        return RuntimeWorkerBindings(
            sync_handler=cast(Any, object()),
            wecom=cast(Any, object()),
            handlers={},
        )

    monkeypatch.setattr(application, "SQLAlchemyJobRepository", RepositoryStub)
    monkeypatch.setattr(application, "Worker", WorkerStub)
    monkeypatch.setattr(
        application,
        "SQLAlchemyApprovalRepository",
        lambda session: SimpleNamespace(recover_stale_creating=lambda **kwargs: None),
    )
    task = asyncio.create_task(
        application._run_worker_loop(
            SimpleNamespace(state=SimpleNamespace()),
            factory=cast(Any, lambda: SessionContext()),
            registry=registry,
            runtime_handler_factory=build_bindings,
            recover_stale=False,
        )
    )
    await asyncio.wait_for(job_started.wait(), timeout=1)

    await asyncio.wait_for(registry.swap(build_bundle(2, new_probe)), timeout=0.1)
    assert old_probe.calls == 0
    assert seen_revisions == [1]

    finish_job.set()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert old_probe.calls == 1
    await registry.close()


@pytest.mark.asyncio
async def test_wecom_poll_uses_one_revision_and_reads_new_interval_next_round(
    monkeypatch,
) -> None:
    """每轮poll只用一个bundle，切换后下一轮等待读取新间隔。"""
    registry = RuntimeClientRegistry(build_bundle(1, CloseProbe()))
    delays: list[float] = []
    polled_revisions: list[int] = []

    class StopPoll(RuntimeError):
        """表示已观察到新revision的下一轮间隔。"""

    async def controlled_sleep(delay: float) -> None:
        """记录两轮间隔，并在新间隔出现后结束循环。"""
        delays.append(delay)
        if len(delays) == 2:
            raise StopPoll

    class PollerStub:
        """记录poll使用的revision并在首轮切换配置。"""

        def __init__(self, bundle: RuntimeClientBundle) -> None:
            """保存本轮固定bundle。"""
            self._bundle = bundle

        async def run_once(self) -> None:
            """首轮运行中发布新bundle。"""
            polled_revisions.append(self._bundle.revision)
            if self._bundle.revision == 1:
                await registry.swap(build_bundle(2, CloseProbe()))

    monkeypatch.setattr(application.asyncio, "sleep", controlled_sleep)

    with pytest.raises(StopPoll):
        await application._run_wecom_poll_loop(
            SimpleNamespace(state=SimpleNamespace()),
            registry=registry,
            runtime_poller_factory=PollerStub,
        )

    assert polled_revisions == [1]
    assert delays == [6.0, 7.0]
    await registry.close()


@pytest.mark.asyncio
async def test_hostex_reconcile_holds_one_revision_and_uses_new_interval(
    monkeypatch,
) -> None:
    """每轮Hostex对账与生命周期服务同revision，下一轮读取新间隔。"""
    old_probe = CloseProbe()
    registry = RuntimeClientRegistry(build_bundle(1, old_probe))
    calls: list[tuple[object, object]] = []

    class StopReconcile(RuntimeError):
        """表示已观察到下一轮新间隔。"""

    class SessionContext:
        """提供对账短事务。"""

        async def __aenter__(self):
            """返回可提交会话。"""
            return SimpleNamespace(commit=self.commit)

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            """退出会话。"""

        async def commit(self) -> None:
            """模拟提交。"""

    class SyncServiceStub:
        """记录同轮Hostex与生命周期依赖。"""

        def __init__(self, hostex, operations, *, lifecycle=None) -> None:
            """捕获运行依赖。"""
            calls.append((hostex, lifecycle))

        async def reconcile(self, start_date, end_date) -> int:
            """在对账进行中发布新revision。"""
            await registry.swap(build_bundle(2, CloseProbe()))
            assert old_probe.calls == 0
            return 1

    async def stop_at_new_interval(delay: float) -> None:
        """验证本轮结束后立即读取新bundle间隔。"""
        assert delay == 62.0
        raise StopReconcile

    monkeypatch.setattr(application, "HostexSyncService", SyncServiceStub)
    monkeypatch.setattr(application.asyncio, "sleep", stop_at_new_interval)

    with pytest.raises(StopReconcile):
        await application._run_hostex_reconcile_loop(
            factory=cast(Any, lambda: SessionContext()),
            registry=registry,
            runtime_lifecycle_factory=lambda session, bundle: (
                f"lifecycle-{bundle.revision}"
            ),
            today_provider=lambda: date(2026, 8, 11),
        )

    assert calls == [("hostex-1", "lifecycle-1")]
    assert old_probe.calls == 1
    await registry.close()


@pytest.mark.asyncio
async def test_context_maintenance_holds_one_revision_for_whole_round(monkeypatch) -> None:
    """一轮客户摘要维护固定同一summarizer，轮末才释放旧bundle。"""
    old_probe = CloseProbe()
    registry = RuntimeClientRegistry(build_bundle(1, old_probe))
    summarizers: list[object] = []

    class StopContext(RuntimeError):
        """表示一轮摘要已完成。"""

    class SessionContext:
        """提供发现和客户维护会话。"""

        async def __aenter__(self):
            """返回可提交会话。"""
            return SimpleNamespace(commit=self.commit, begin_nested=lambda: None)

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            """退出会话。"""

        async def commit(self) -> None:
            """模拟提交。"""

    class ContextRepositoryStub:
        """返回一个需要维护的客户。"""

        def __init__(self, session) -> None:
            """接受当前会话。"""

        async def list_customer_ids_with_messages(self) -> list[int]:
            """返回单个客户编号。"""
            return [7]

    class ContextServiceStub:
        """记录本轮summarizer并在外部调用中切换revision。"""

        def __init__(self, repository, summarizer, *, before_external) -> None:
            """捕获本轮模型依赖。"""
            summarizers.append(summarizer)

        async def maintain_customer(self, customer_id: int, now) -> None:
            """模拟长模型调用期间热切换。"""
            await registry.swap(build_bundle(2, CloseProbe()))
            assert old_probe.calls == 0

    async def stop_after_round(delay: float) -> None:
        """轮末旧租约应已释放。"""
        assert delay == 3600
        assert old_probe.calls == 1
        raise StopContext

    monkeypatch.setattr(application, "SQLAlchemyContextRepository", ContextRepositoryStub)
    monkeypatch.setattr(application, "ContextRetentionService", ContextServiceStub)
    monkeypatch.setattr(application.asyncio, "sleep", stop_after_round)

    with pytest.raises(StopContext):
        await application._run_context_maintenance_loop(
            factory=cast(Any, lambda: SessionContext()),
            registry=registry,
        )

    assert summarizers == ["summary-1"]
    await registry.close()
