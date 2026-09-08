"""外部调用结果的只读汇总；诊断页承诺能在这里看到，就必须真的有。"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from homestay_bot.domain.models import Base, ExternalRequest
from homestay_bot.repositories.admin_diagnostics import (
    SQLAlchemyAdminDiagnosticsRepository,
)


async def _session() -> AsyncSession:
    """按既有约定各自建内存库，不依赖共享 fixture。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)()


async def _add(session: AsyncSession, **kwargs) -> None:
    """写入一条外部调用记录。"""
    session.add(ExternalRequest(**kwargs))
    await session.flush()


@pytest.mark.asyncio
async def test_external_calls_roll_up_by_endpoint() -> None:
    """按端点汇总而不是逐条罗列。

    生产上同一个端点已有上百条完全相同的记录（对账轮询每 15 分钟一次），逐条
    列出既看不出问题也翻不完。诊断页要回答的是「这个接口现在还通不通」。
    """
    session = await _session()
    base = datetime(2026, 9, 8, 10, 0, tzinfo=UTC)
    for i in range(3):
        await _add(
            session,
            provider="hostex",
            method="GET",
            path="/reservations",
            business_code=200,
            succeeded=True,
            created_at=base + timedelta(minutes=i),
        )
    await _add(
        session,
        provider="hostex",
        method="GET",
        path="/reservations",
        business_code=500,
        succeeded=False,
        created_at=base + timedelta(minutes=10),
    )
    await _add(
        session,
        provider="wecom",
        method="POST",
        path="/message/send",
        business_code=0,
        succeeded=True,
        created_at=base,
    )

    repo = SQLAlchemyAdminDiagnosticsRepository(session)
    rows = await repo.list_external_calls(limit=10)

    by_path = {row.path: row for row in rows}
    assert by_path["/reservations"].total == 4
    assert by_path["/reservations"].failed == 1
    # 最近一次是失败的，这正是要一眼看到的信息。
    assert by_path["/reservations"].last_succeeded is False
    # SQLite 不保留 tzinfo（生产是 PostgreSQL 的 timestamptz），比较时统一去掉。
    assert by_path["/reservations"].last_at.replace(tzinfo=None) == (
        base + timedelta(minutes=10)
    ).replace(tzinfo=None)
    assert by_path["/message/send"].failed == 0


@pytest.mark.asyncio
async def test_external_call_fields_are_sanitised() -> None:
    """路径与提供方仍按稳定机器码校验，不让正文或查询串混进诊断页。"""
    session = await _session()
    await _add(
        session,
        provider="hostex",
        method="GET",
        path="/reservations?token=abc123&name=张三",
        business_code=200,
        succeeded=True,
        created_at=datetime(2026, 9, 8, tzinfo=UTC),
    )

    repo = SQLAlchemyAdminDiagnosticsRepository(session)
    rows = await repo.list_external_calls(limit=10)

    assert rows[0].path == "unknown_path"
    assert "token=abc123" not in rows[0].path
    assert "张三" not in rows[0].path
