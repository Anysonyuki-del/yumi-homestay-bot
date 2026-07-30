import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy.exc import OperationalError

from homestay_bot import application


class StopWorkerRetry(RuntimeError):
    """表示测试已观察到 worker 在数据库锁冲突后继续重试。"""


class StopFaqMaintenance(RuntimeError):
    """表示测试已观察到一次 FAQ 周期维护。"""


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
