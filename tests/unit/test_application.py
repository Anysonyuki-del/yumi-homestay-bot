import sqlite3
from datetime import UTC, date, datetime
from io import BytesIO
from types import SimpleNamespace
from typing import Any, cast

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.exc import OperationalError

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

        def __init__(self, repository, summarizer) -> None:
            """验证依赖已经装配。"""
            assert isinstance(repository, RepositoryStub)
            assert summarizer == "summarizer"

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

        def __init__(self, hostex, operations, *, lifecycle=None) -> None:
            """验证运行时装配了生命周期调度器。"""
            assert hostex == "hostex"
            assert lifecycle == "lifecycle"

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

        async def list_candidates(self):
            """返回固定候选。"""
            calls.append(("list", (), {}))
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

    assert await service.list_candidates() == ["candidate"]
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
        ("list", (), {}),
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
