import sqlite3
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy.exc import OperationalError

from homestay_bot import application


class StopWorkerRetry(RuntimeError):
    """表示测试已观察到 worker 在数据库锁冲突后继续重试。"""


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
