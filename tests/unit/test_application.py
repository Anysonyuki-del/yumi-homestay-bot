import asyncio
import contextlib
import logging
import sqlite3
from datetime import UTC, date, datetime
from io import BytesIO
from types import SimpleNamespace
from typing import Any, cast

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.exc import IntegrityError, OperationalError

from homestay_bot import application
from homestay_bot.domain.enums import BusinessTaskStatus, EmployeeRole
from homestay_bot.services.private_file_storage import PrivateFileStorage


class StopWorkerRetry(RuntimeError):
    """表示测试已观察到 worker 在数据库锁冲突后继续重试。"""


class StopFaqMaintenance(RuntimeError):
    """表示测试已观察到一次 FAQ 周期维护。"""


class StopContextMaintenance(RuntimeError):
    """表示测试已观察到一次客户上下文周期维护。"""


class StopHostexReconcile(RuntimeError):
    """表示测试已观察到一次百居易对账。"""


class StopTaskLifecycle(RuntimeError):
    """表示测试已观察到一次任务生命周期巡检。"""


class StopRetentionLoop(RuntimeError):
    """表示测试已观察到一次历史记录清理。"""


def test_deferred_payload_restores_fast_ack_delivery_metadata() -> None:
    """worker 载荷必须完整恢复安抚摘要和 outbox 编号。"""
    message = application._deferred_message_from_payload(
        {
            "msgid": "msg-1",
            "open_kfid": "wk-1",
            "external_userid": "wm-1",
            "origin": "guest",
            "msgtype": "text",
            "content": "灯坏了修一下",
            "sent_at": "2026-08-14T02:23:51+08:00",
            "fast_ack_sha256": "a" * 64,
            "fast_ack_outbox_id": "outbox:fast-ack",
            "merged_guest_count": 3,
        }
    )

    assert message.metadata == {
        "fast_ack_sha256": "a" * 64,
        "fast_ack_outbox_id": "outbox:fast-ack",
        "merged_guest_count": "3",
    }


@pytest.mark.asyncio
async def test_deferred_dispatch_separates_debounce_and_final_phases() -> None:
    """同一 worker 必须把静默任务和最终回复任务分发到不同服务入口。"""
    calls: list[tuple[str, str]] = []

    class ConversationServiceStub:
        """记录延迟任务选择的会话服务入口。"""

        async def process_debounced_message(self, message) -> None:
            """记录静默阶段调用。"""
            calls.append(("debounce", message.msgid))

        async def process_recorded_message(self, message) -> None:
            """记录最终阶段调用。"""
            calls.append(("final", message.msgid))

    message = application._deferred_message_from_payload(
        {
            "msgid": "msg-1",
            "open_kfid": "wk-1",
            "external_userid": "wm-1",
            "origin": "guest",
            "msgtype": "text",
            "content": "灯坏了",
            "sent_at": "2026-08-14T02:23:51+08:00",
        }
    )
    service = ConversationServiceStub()

    await application._dispatch_deferred_message(
        service,
        message,
        phase="debounce",
    )
    await application._dispatch_deferred_message(
        service,
        message,
        phase="final",
    )

    assert calls == [("debounce", "msg-1"), ("final", "msg-1")]


def test_deferred_ack_and_final_use_distinct_outbox_delivery_phases() -> None:
    """静默阶段安抚与最终回复必须生成不同的事务 outbox 幂等键。"""
    assert application._guest_delivery_phase(
        deferred=True,
        deferred_phase="debounce",
    ) == "ack"
    assert application._guest_delivery_phase(
        deferred=True,
        deferred_phase="final",
    ) == "final"
    assert application._guest_delivery_phase(
        deferred=False,
        deferred_phase="final",
    ) is None


@pytest.mark.asyncio
async def test_retention_loop_purges_and_commits_daily(monkeypatch) -> None:
    """历史记录维护应每天清理一次，并在独立事务提交。"""
    calls: list[object] = []
    session = SimpleNamespace()

    async def commit() -> None:
        """记录清理事务提交。"""
        calls.append("commit")

    session.commit = commit

    class SessionContext:
        """返回固定清理会话。"""

        async def __aenter__(self):
            """进入测试会话。"""
            return session

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            """退出测试会话。"""

    class RetentionRepositoryStub:
        """记录清理仓储调用。"""

        def __init__(self, selected_session) -> None:
            """验证仓储绑定当前短会话。"""
            assert selected_session is session

        async def purge(self):
            """返回固定删除计数。"""
            calls.append("purge")
            return {"jobs": 2}

    async def stop_after_cycle(delay: float) -> None:
        """观察到一天调度间隔后终止无限循环。"""
        assert delay == 86_400
        raise StopRetentionLoop

    monkeypatch.setattr(
        application,
        "SQLAlchemyRetentionRepository",
        RetentionRepositoryStub,
        raising=False,
    )
    monkeypatch.setattr(application.asyncio, "sleep", stop_after_cycle)

    with pytest.raises(StopRetentionLoop):
        await application._run_retention_loop(
            cast(Any, lambda: SessionContext())
        )

    assert calls == ["purge", "commit"]


@pytest.mark.asyncio
async def test_complaint_delivery_callback_ignores_non_complaint_sources(monkeypatch) -> None:
    """普通客人回复不得误更新客诉投递状态。"""
    calls: list[object] = []

    class RepositoryStub:
        """记录客诉状态仓储是否被调用。"""

        def __init__(self, session) -> None:
            """保留注入会话。"""
            calls.append(session)

    monkeypatch.setattr(application, "SQLAlchemyComplaintRepository", RepositoryStub)

    await application._record_complaint_delivery(
        object(),
        "guest-message-1",
        delivered=True,
    )

    assert calls == []


@pytest.mark.asyncio
async def test_complaint_delivery_callback_updates_real_result(monkeypatch) -> None:
    """客诉出站任务成功和失败都应通过来源编号回写状态。"""
    calls: list[tuple[str, int, str | None]] = []

    class RepositoryStub:
        """捕获客诉实际投递状态更新。"""

        def __init__(self, session) -> None:
            """接受当前 worker 事务。"""

        async def mark_delivery_sent(
            self, review_id: int, *, sent_at, external_message_id: str
        ) -> None:
            """记录成功投递。"""
            calls.append(("sent", review_id, external_message_id))

        async def mark_delivery_failed(self, review_id: int, *, error_code: str) -> None:
            """记录失败投递。"""
            calls.append(("failed", review_id, error_code))

    monkeypatch.setattr(application, "SQLAlchemyComplaintRepository", RepositoryStub)

    await application._record_complaint_delivery(
        object(),
        "complaint:17",
        delivered=False,
        error_code="WeComApiError",
    )
    await application._record_complaint_delivery(
        object(),
        "complaint:17",
        delivered=True,
        external_message_id="wecom-17",
    )
    await application._record_complaint_delivery(
        object(),
        "complaint:17:retry-2",
        delivered=False,
        error_code="RetryableDeliveryError",
    )

    assert calls == [
        ("failed", 17, "WeComApiError"),
        ("sent", 17, "wecom-17"),
        ("failed", 17, "RetryableDeliveryError"),
    ]


def test_committed_hostex_job_updates_sync_heartbeats() -> None:
    """只有已提交的百居易事件任务才刷新同步与提醒心跳。"""
    app = SimpleNamespace(state=SimpleNamespace())
    completed_at = datetime(2026, 7, 31, 8, tzinfo=UTC)

    application._record_committed_job_heartbeat(
        app,
        SimpleNamespace(job_type="hostex_event"),
        now_provider=lambda: completed_at,
    )

    assert app.state.hostex_sync_last_success == completed_at
    assert app.state.lifecycle_scheduler_last_success == completed_at


@pytest.mark.asyncio
async def test_context_maintenance_processes_customers_hourly(monkeypatch) -> None:
    """应用应每小时维护有消息的客户摘要并提交成功结果。"""
    session = SimpleNamespace(committed=False)
    maintained: list[tuple[int, datetime]] = []

    async def commit() -> None:
        """记录维护事务已提交。"""
        session.committed = True

    session.commit = commit

    class SessionContext:
        """返回固定维护会话。"""

        async def __aenter__(self):
            """进入维护会话。"""
            return session

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            """退出维护会话。"""

    class RepositoryStub:
        """返回一个需要维护的正式客户。"""

        def __init__(self, selected_session) -> None:
            """验证仓储绑定维护会话。"""
            assert selected_session is session

        async def list_customer_ids_with_messages(self):
            """返回固定客户主键。"""
            return [7]

    class ServiceStub:
        """记录上下文维护调用。"""

        def __init__(self, repository, summarizer, *, before_external=None) -> None:
            """验证依赖已经装配。"""
            assert isinstance(repository, RepositoryStub)
            assert summarizer == "summarizer"
            assert before_external == session.commit

        async def maintain_customer(self, customer_id: int, now: datetime) -> None:
            """记录客户与统一维护时间。"""
            maintained.append((customer_id, now))

    async def stop_after_cycle(delay: float) -> None:
        """验证一小时间隔并结束无限循环。"""
        assert delay == 3600
        raise StopContextMaintenance

    now = datetime(2026, 7, 31, 8, tzinfo=UTC)
    completed_at = datetime(2026, 7, 31, 8, 5, tzinfo=UTC)
    heartbeats: list[datetime] = []
    monkeypatch.setattr(application, "SQLAlchemyContextRepository", RepositoryStub)
    monkeypatch.setattr(application, "ContextRetentionService", ServiceStub)
    monkeypatch.setattr(application.asyncio, "sleep", stop_after_cycle)

    with pytest.raises(StopContextMaintenance):
        await application._run_context_maintenance_loop(
            factory=cast(Any, lambda: SessionContext()),
            summarizer="summarizer",
            now_provider=lambda: now,
            heartbeat_now=lambda: completed_at,
            heartbeat=heartbeats.append,
        )

    assert maintained == [(7, now)]
    assert session.committed is True
    assert heartbeats == [completed_at]


@pytest.mark.asyncio
async def test_context_maintenance_isolates_customer_failures(monkeypatch) -> None:
    """单个客户摘要失败不得阻断后续客户或成功心跳。"""
    sessions: list[SimpleNamespace] = []
    maintained: list[int] = []
    commits: list[int] = []

    class SessionContext:
        """为发现和每个客户维护返回独立会话。"""

        async def __aenter__(self):
            """创建可记录提交的独立会话。"""
            session = SimpleNamespace(index=len(sessions))

            async def commit() -> None:
                """记录当前客户会话提交。"""
                commits.append(session.index)

            session.commit = commit
            sessions.append(session)
            return session

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            """关闭本轮维护会话。"""

    class RepositoryStub:
        """首个会话返回两个需要维护的客户。"""

        def __init__(self, session) -> None:
            """绑定当前会话。"""
            self.session = session

        async def list_customer_ids_with_messages(self):
            """返回两个客户。"""
            return [1, 2]

    class ServiceStub:
        """让第一个客户失败，验证第二个客户仍继续。"""

        def __init__(self, repository, summarizer, *, before_external=None) -> None:
            """接受每客户独立仓储，并验证事务边界来自当前会话。"""
            assert before_external == repository.session.commit

        async def maintain_customer(self, customer_id: int, now: datetime) -> None:
            """记录客户并对第一个客户模拟模型失败。"""
            maintained.append(customer_id)
            if customer_id == 1:
                raise RuntimeError("summary failed")

    async def stop_after_cycle(delay: float) -> None:
        """验证周期仍按一小时运行。"""
        assert delay == 3600
        raise StopContextMaintenance

    monkeypatch.setattr(application, "SQLAlchemyContextRepository", RepositoryStub)
    monkeypatch.setattr(application, "ContextRetentionService", ServiceStub)
    monkeypatch.setattr(application.asyncio, "sleep", stop_after_cycle)
    heartbeats: list[datetime] = []

    with pytest.raises(StopContextMaintenance):
        await application._run_context_maintenance_loop(
            factory=cast(Any, lambda: SessionContext()),
            summarizer="summarizer",
            now_provider=lambda: datetime(2026, 7, 31, 8, tzinfo=UTC),
            heartbeat_now=lambda: datetime(2026, 7, 31, 8, 5, tzinfo=UTC),
            heartbeat=heartbeats.append,
        )

    assert maintained == [1, 2]
    assert commits == [2]
    assert heartbeats == [datetime(2026, 7, 31, 8, 5, tzinfo=UTC)]


@pytest.mark.asyncio
async def test_hostex_reconcile_updates_sync_and_lifecycle_heartbeats(
    monkeypatch,
) -> None:
    """成功对账后应同时证明订单同步和提醒调度仍在运行。"""
    now = datetime(2026, 7, 31, 8, tzinfo=UTC)
    session = SimpleNamespace(committed=False)
    sync_heartbeats: list[datetime] = []
    lifecycle_heartbeats: list[datetime] = []

    async def commit() -> None:
        """记录对账事务提交。"""
        session.committed = True

    session.commit = commit

    class SessionContext:
        """返回固定对账会话。"""

        async def __aenter__(self):
            """进入对账会话。"""
            return session

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            """退出对账会话。"""

    class SyncServiceStub:
        """记录对账窗口并验证生命周期服务已注入。"""

        def __init__(
            self,
            hostex,
            operations,
            *,
            lifecycle=None,
            task_lifecycle=None,
        ) -> None:
            """验证运行时装配了提醒调度与任务治理服务。"""
            assert hostex == "hostex"
            assert lifecycle == "lifecycle"
            assert isinstance(task_lifecycle, application.TaskLifecycleService)

        async def reconcile(self, start_date, end_date) -> int:
            """验证一期十五天补漏窗口。"""
            assert start_date == date(2026, 7, 30)
            assert end_date == date(2026, 8, 15)
            return 1

    async def stop_after_cycle(delay: float) -> None:
        """完成首轮对账后结束无限循环。"""
        assert delay == 900
        raise StopHostexReconcile

    monkeypatch.setattr(application, "HostexSyncService", SyncServiceStub)
    monkeypatch.setattr(application.asyncio, "sleep", stop_after_cycle)

    with pytest.raises(StopHostexReconcile):
        await application._run_hostex_reconcile_loop(
            factory=cast(Any, lambda: SessionContext()),
            hostex=cast(Any, "hostex"),
            interval_seconds=900,
            today_provider=lambda: date(2026, 7, 31),
            lifecycle_factory=lambda selected: (
                "lifecycle" if selected is session else "wrong"
            ),
            heartbeat_now=lambda: now,
            sync_heartbeat=sync_heartbeats.append,
            lifecycle_heartbeat=lifecycle_heartbeats.append,
        )

    assert session.committed is True
    assert sync_heartbeats == [now]
    assert lifecycle_heartbeats == [now]


@pytest.mark.asyncio
async def test_task_lifecycle_loop_commits_result_and_heartbeat(monkeypatch) -> None:
    """任务生命周期循环应按小时提交有限扫描并记录心跳。"""
    session = SimpleNamespace(committed=False)
    observed_at = datetime(2026, 8, 29, 8, tzinfo=UTC)
    heartbeats: list[datetime] = []
    results: list[object] = []

    async def commit() -> None:
        """记录生命周期事务已提交。"""
        session.committed = True

    session.commit = commit

    class SessionContext:
        """提供固定生命周期会话。"""

        async def __aenter__(self):
            """进入测试会话。"""
            return session

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            """退出测试会话。"""

    class RepositoryStub:
        """验证生命周期仓储绑定当前事务。"""

        def __init__(self, selected_session) -> None:
            """保存仓储会话。"""
            assert selected_session is session

    class ServiceStub:
        """返回固定有限扫描结果。"""

        def __init__(self, repository) -> None:
            """验证使用统一运营仓储。"""
            assert isinstance(repository, RepositoryStub)

        async def sweep(self, *, now: datetime, limit: int):
            """验证巡检时间和批次边界。"""
            assert now == observed_at
            assert limit == 100
            return application.TaskLifecycleSweepResult(3, 2, 1)

    async def stop_after_cycle(delay: float) -> None:
        """验证每小时兜底周期后结束循环。"""
        assert delay == 3600
        raise StopTaskLifecycle

    monkeypatch.setattr(application, "SQLAlchemyOperationsRepository", RepositoryStub)
    monkeypatch.setattr(application, "TaskLifecycleService", ServiceStub)
    monkeypatch.setattr(application.asyncio, "sleep", stop_after_cycle)

    with pytest.raises(StopTaskLifecycle):
        await application._run_task_lifecycle_loop(
            cast(Any, lambda: SessionContext()),
            now_provider=lambda: observed_at,
            heartbeat=heartbeats.append,
            result_recorder=results.append,
        )

    assert session.committed is True
    assert heartbeats == [observed_at]
    assert results == [application.TaskLifecycleSweepResult(3, 2, 1)]


@pytest.mark.asyncio
async def test_faq_candidate_catalog_uses_short_session_and_commits_reopen(
    monkeypatch,
) -> None:
    """模型候选目录应使用独立短会话，并提交关闭期满的重开状态。"""
    session = SimpleNamespace(committed=False)

    async def commit() -> None:
        """记录短会话提交。"""
        session.committed = True

    session.commit = commit

    class SessionContext:
        """返回固定短会话。"""

        async def __aenter__(self):
            """进入测试会话。"""
            return session

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            """退出测试会话。"""

    class RepositoryStub:
        """返回一个最小候选目录。"""

        def __init__(self, selected_session) -> None:
            """验证仓储绑定独立会话。"""
            assert selected_session is session

        async def list_context(self, *, now: datetime):
            """返回固定候选。"""
            return [SimpleNamespace(id=7, canonical_question="能否寄存行李？")]

    monkeypatch.setattr(
        application,
        "SQLAlchemyFaqCandidateRepository",
        RepositoryStub,
    )
    adapter = application.SessionFaqCandidateRepository(
        cast(Any, lambda: SessionContext())
    )

    result = await adapter.list_context(
        now=datetime(2026, 7, 30, tzinfo=UTC)
    )

    assert result[0].id == 7
    assert session.committed is True


@pytest.mark.asyncio
async def test_session_knowledge_admin_service_delegates_candidate_actions(
    monkeypatch,
) -> None:
    """短会话管理服务应完整转发候选查看、转知识和关闭操作。"""
    session = object()
    calls: list[tuple[str, tuple, dict]] = []

    class SessionContext:
        """返回固定管理会话。"""

        async def __aenter__(self):
            """进入测试会话。"""
            return session

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            """退出测试会话。"""

    class AdminServiceStub:
        """记录候选管理调用。"""

        def __init__(self, selected_session) -> None:
            """验证服务绑定当前短会话。"""
            assert selected_session is session

        async def list_candidates(self, *, offset: int, limit: int):
            """返回固定候选。"""
            calls.append(("list", (), {"offset": offset, "limit": limit}))
            return ["candidate"]

        async def convert_candidate(
            self,
            candidate_id: int,
            employee_id: int,
            **fields,
        ):
            """记录转知识参数。"""
            calls.append(
                ("convert", (candidate_id, employee_id), fields)
            )
            return "knowledge"

        async def snooze_candidate(
            self,
            candidate_id: int,
            employee_id: int,
        ) -> None:
            """记录关闭参数。"""
            calls.append(("snooze", (candidate_id, employee_id), {}))

    monkeypatch.setattr(
        application,
        "KnowledgeAdminService",
        AdminServiceStub,
    )
    service = application.SessionKnowledgeAdminService(
        cast(Any, lambda: SessionContext())
    )

    assert await service.list_candidates(offset=0, limit=51) == ["candidate"]
    assert (
        await service.convert_candidate(
            7,
            11,
            category="停车",
        )
        == "knowledge"
    )
    await service.snooze_candidate(7, 11)

    assert calls == [
        ("list", (), {"offset": 0, "limit": 51}),
        ("convert", (7, 11), {"category": "停车"}),
        ("snooze", (7, 11), {}),
    ]


def test_worker_handlers_register_faq_draft_factory() -> None:
    """worker 装配应为当前事务注册 FAQ 草稿处理器。"""
    session = object()

    async def faq_handler(payload: dict[str, Any]) -> None:
        """提供固定测试处理器。"""

    def factory(selected_session):
        """验证处理器绑定当前 worker 会话。"""
        assert selected_session is session
        return faq_handler

    handlers: dict[str, Any] = {"wecom_sync": object()}

    application._register_faq_draft_handler(
        handlers,
        cast(Any, session),
        cast(Any, factory),
    )

    assert handlers["faq_draft_generate"] is faq_handler


@pytest.mark.asyncio
async def test_complaint_page_session_adapter_forwards_message_cursor(
    monkeypatch,
) -> None:
    """客诉页面适配器必须把历史消息游标传到真实页面服务。"""
    session = object()
    calls: list[tuple[int, int | None]] = []

    class SessionContext:
        """提供不访问数据库的短会话。"""

        async def __aenter__(self):
            """返回固定会话标记。"""
            return session

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            """退出测试会话。"""

    class ComplaintServiceStub:
        """记录应用适配层传入的历史消息游标。"""

        def __init__(self, selected_session, outbox) -> None:
            """验证服务绑定固定短会话。"""
            assert selected_session is session

        async def get_detail(self, review_id: int, *, before_message_id=None):
            """记录详情编号和游标。"""
            calls.append((review_id, before_message_id))
            return {"review_id": review_id}

    monkeypatch.setattr(application, "ComplaintAdminService", ComplaintServiceStub)
    service = application.SessionComplaintAdminService(
        cast(Any, lambda: SessionContext())
    )

    result = await service.get_detail(7, before_message_id=301)

    assert result == {"review_id": 7}
    assert calls == [(7, 301)]


def test_worker_handlers_register_lifecycle_factory() -> None:
    """worker 装配应为当前事务注册生命周期发送处理器。"""
    session = object()

    async def lifecycle_handler(payload: dict[str, Any]) -> None:
        """提供固定测试处理器。"""

    def factory(selected_session):
        """验证处理器绑定当前 worker 会话。"""
        assert selected_session is session
        return lifecycle_handler

    handlers: dict[str, Any] = {"wecom_sync": object()}

    application._register_lifecycle_handler(
        handlers,
        cast(Any, session),
        cast(Any, factory),
    )

    assert handlers["lifecycle_send"] is lifecycle_handler


@pytest.mark.asyncio
async def test_faq_maintenance_runs_without_new_guest_question(monkeypatch) -> None:
    """应用后台应周期清理 FAQ 明细，不依赖客人再次触发统计服务。"""
    session = SimpleNamespace(committed=False)
    maintained_at: list[datetime] = []

    async def commit() -> None:
        """记录维护事务提交。"""
        session.committed = True

    session.commit = commit

    class SessionContext:
        """返回固定维护会话。"""

        async def __aenter__(self):
            """进入维护会话。"""
            return session

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            """退出维护会话。"""

    class RepositoryStub:
        """记录应用调用的周期维护时间。"""

        def __init__(self, selected_session) -> None:
            """验证仓储绑定维护会话。"""
            assert selected_session is session

        async def maintain(self, *, now: datetime) -> tuple[int, int]:
            """记录维护并返回清理数量。"""
            maintained_at.append(now)
            return 2, 1

    async def stop_after_first_cycle(delay: float) -> None:
        """验证维护间隔后结束无限循环。"""
        assert delay == 3600
        raise StopFaqMaintenance

    now = datetime(2026, 7, 30, 8, tzinfo=UTC)
    monkeypatch.setattr(
        application,
        "SQLAlchemyFaqCandidateRepository",
        RepositoryStub,
    )
    monkeypatch.setattr(application.asyncio, "sleep", stop_after_first_cycle)

    with pytest.raises(StopFaqMaintenance):
        await application._run_faq_maintenance_loop(
            factory=cast(Any, lambda: SessionContext()),
            now_provider=lambda: now,
        )

    assert maintained_at == [now]
    assert session.committed is True


@pytest.mark.asyncio
async def test_worker_loop_survives_transient_sqlite_lock(monkeypatch) -> None:
    """SQLite 短暂锁冲突不应永久终止消息发送 worker。"""

    class LockedSessionContext:
        """模拟进入数据库会话时遇到一次 SQLite 写锁。"""

        async def __aenter__(self):
            """抛出与真实运行日志一致的锁异常。"""
            raise OperationalError(
                "UPDATE jobs",
                {},
                sqlite3.OperationalError("database is locked"),
            )

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            """上下文进入失败时无需额外清理。"""

    def locked_factory():
        """每次创建会话都返回锁冲突上下文。"""
        return LockedSessionContext()

    async def stop_after_retry(delay: float) -> None:
        """观察到短暂等待即终止无限循环测试。"""
        assert delay == 1
        raise StopWorkerRetry

    monkeypatch.setattr(application.asyncio, "sleep", stop_after_retry)

    with pytest.raises(StopWorkerRetry):
        await application._run_worker_loop(
            SimpleNamespace(state=SimpleNamespace()),
            factory=cast(Any, locked_factory),
            handler=cast(Any, object()),
            wecom=cast(Any, object()),
        )


@pytest.mark.asyncio
async def test_worker_loop_survives_non_lock_database_failure(monkeypatch) -> None:
    """数据库提交类故障不得永久终止后台 worker。"""

    class FailedSessionContext:
        """模拟数据库连接在 worker 周期开始时中断。"""

        async def __aenter__(self):
            """抛出非连接型数据库完整性错误。"""
            raise IntegrityError(
                "COMMIT",
                {},
                sqlite3.IntegrityError("constraint failed"),
            )

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            """上下文进入失败时无需额外清理。"""

    async def stop_after_retry(delay: float) -> None:
        """观察到有限退避即终止无限循环测试。"""
        assert delay == 1
        raise StopWorkerRetry

    monkeypatch.setattr(application.asyncio, "sleep", stop_after_retry)

    with pytest.raises(StopWorkerRetry):
        await application._run_worker_loop(
            SimpleNamespace(state=SimpleNamespace()),
            factory=cast(Any, lambda: FailedSessionContext()),
            handler=cast(Any, object()),
            wecom=cast(Any, object()),
        )


@pytest.mark.asyncio
async def test_failed_attachment_transaction_removes_private_file(
    monkeypatch,
    tmp_path,
) -> None:
    """附件数据库写入失败时不得遗留可被误用的孤儿文件。"""
    task = SimpleNamespace(
        id=7,
        assigned_employee_id=2,
        status=BusinessTaskStatus.IN_PROGRESS,
    )

    class SessionContext:
        """提供不实际访问数据库的短会话。"""

        async def __aenter__(self):
            """返回测试会话标记。"""
            return SimpleNamespace()

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            """退出测试会话。"""

    class RepositoryStub:
        """授权任务后模拟附件引用写入失败。"""

        def __init__(self, session) -> None:
            """接受应用注入的测试会话。"""

        async def get_task(self, task_id: int):
            """返回分派给当前员工的任务。"""
            assert task_id == task.id
            return task

        async def add_task_attachment(self, **fields):
            """模拟数据库约束或连接异常。"""
            raise RuntimeError("database write failed")

    monkeypatch.setattr(
        application,
        "SQLAlchemyOperationsRepository",
        RepositoryStub,
    )
    storage = PrivateFileStorage(tmp_path)
    service = application.SessionTaskPageService(
        cast(Any, lambda: SessionContext()),
        storage,
        1024,
    )
    employee = SimpleNamespace(
        id=2,
        role=EmployeeRole.STAFF,
        is_active=True,
    )
    png = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00"
        b"\x90wS\xde"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    with pytest.raises(RuntimeError, match="database write failed"):
        await service.upload_photo(
            task.id,
            cast(Any, employee),
            BytesIO(png),
            "image/png",
        )

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_failed_credential_transaction_removes_private_qr(
    monkeypatch,
    tmp_path,
) -> None:
    """凭证落库失败时必须删除刚保存的私有二维码。"""

    class SessionContext:
        """提供不实际访问数据库的凭证短会话。"""

        async def __aenter__(self):
            """返回测试会话标记。"""
            return SimpleNamespace()

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            """退出测试会话。"""

    class PropertyServiceStub:
        """通过权限校验后模拟凭证数据库写入失败。"""

        def __init__(self, session, cipher) -> None:
            """接受应用注入依赖。"""

        @staticmethod
        def require_admin(employee) -> None:
            """确认测试员工是管理员。"""
            assert employee.role is EmployeeRole.ADMIN

        async def replace_credentials(self, *args, **kwargs):
            """模拟凭证版本写入失败。"""
            raise RuntimeError("credential write failed")

    monkeypatch.setattr(
        application,
        "PropertyAdminService",
        PropertyServiceStub,
    )
    storage = PrivateFileStorage(tmp_path)
    cipher = application.SensitiveDataCipher(
        Fernet.generate_key().decode("ascii")
    )
    service = application.SessionPropertyAdminService(
        cast(Any, lambda: SessionContext()),
        cipher,
        storage,
        1024,
    )
    employee = SimpleNamespace(
        id=1,
        role=EmployeeRole.ADMIN,
        is_active=True,
    )
    png = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00"
        b"\x90wS\xde"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    with pytest.raises(RuntimeError, match="credential write failed"):
        await service.replace_credentials(
            101,
            cast(Any, employee),
            "839201",
            "入住指南",
            BytesIO(png),
            "image/png",
        )

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_runtime_task_failure_is_logged_with_traceback(caplog) -> None:
    """常驻后台task意外结束必须留下可定位的错误日志。

    task 由 background_tasks 强引用，异常永远不会被 retrieve，asyncio 的
    "Task exception was never retrieved" 也不会触发；没有主动记录时，运维只能
    看到 /health 转 degraded 而拿不到任何原因。
    """

    async def failing_loop() -> None:
        """模拟常驻循环遇到未预料异常后退出。"""
        raise RuntimeError("运行客户端注册表已关闭")

    with caplog.at_level(logging.ERROR, logger="homestay_bot.application"):
        task = application._create_runtime_task(failing_loop())
        with contextlib.suppress(RuntimeError):
            await task

    records = [item for item in caplog.records if item.levelno >= logging.ERROR]
    assert records, "后台task异常结束必须记录 ERROR 日志"
    # 脱敏过滤器会把 exc_info 渲染进 exc_text 再清空，两种形态都必须能定位。
    rendered = "\n".join(
        logging.Formatter("%(message)s").format(item) for item in records
    )
    assert "RuntimeError" in rendered
    assert "failing_loop" in rendered


@pytest.mark.asyncio
async def test_runtime_task_cancellation_is_not_reported_as_failure(caplog) -> None:
    """正常关停取消不得污染错误日志，否则告警会失去意义。"""

    async def cancellable_loop() -> None:
        """模拟关停期间被取消的常驻循环。"""
        await asyncio.sleep(3600)

    with caplog.at_level(logging.WARNING, logger="homestay_bot.application"):
        task = application._create_runtime_task(cancellable_loop())
        await asyncio.sleep(0)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert not [item for item in caplog.records if item.levelno >= logging.WARNING]


@pytest.mark.asyncio
async def test_runtime_task_normal_completion_is_not_reported(caplog) -> None:
    """正常结束的后台task不得记录异常。"""

    async def finishing_loop() -> None:
        """模拟一次性后台任务正常结束。"""
        return None

    with caplog.at_level(logging.WARNING, logger="homestay_bot.application"):
        await application._create_runtime_task(finishing_loop())

    assert not [item for item in caplog.records if item.levelno >= logging.WARNING]


def test_session_facades_implement_every_route_port_method() -> None:
    """路由从 app.state 取到的会话门面必须实现 Port 声明的全部方法。

    路由通过 app.state 获取服务，静态类型在该处是松的，因此「Port 与内层
    服务都加了方法、生产门面漏加」不会被 mypy 发现，只会在真实请求时抛
    AttributeError 并被错误处理压成状态码。v1.5.0 的批量归档就是这样上线的：
    TaskPageService 和 TaskPageServicePort 都有 archive_filtered，
    SessionTaskPageService 没有。这里逐个方法名比对，堵住整类问题而非三个方法。
    """
    from homestay_bot import application
    from homestay_bot.routes import approvals as approvals_routes
    from homestay_bot.routes import customers as customers_routes
    from homestay_bot.routes import properties as properties_routes
    from homestay_bot.routes import tasks as tasks_routes

    pairs = [
        (tasks_routes.TaskPageServicePort, application.SessionTaskPageService),
        (
            approvals_routes.ApprovalPageServicePort,
            application.SessionApprovalPageService,
        ),
        (
            properties_routes.PropertyAdminServicePort,
            application.SessionPropertyAdminService,
        ),
        (
            customers_routes.CustomerAdminServicePort,
            application.SessionCustomerAdminService,
        ),
    ]

    missing: dict[str, list[str]] = {}
    for port, facade in pairs:
        required = {
            name
            for name in vars(port)
            if not name.startswith("_") and callable(vars(port)[name])
        }
        absent = sorted(name for name in required if not hasattr(facade, name))
        if absent:
            missing[facade.__name__] = absent

    assert missing == {}, f"会话门面缺少 Port 声明的方法：{missing}"
