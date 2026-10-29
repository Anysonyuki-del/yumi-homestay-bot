"""真实 PostgreSQL 上的小程序登录并发建档与过期清理（Codex 审查 R4 补证）。

SQLite 写事务互斥，测不出两个连接同时首登时的唯一键等待与保存点回退；
`DELETE ... WHERE id IN (子查询 LIMIT n)` 的方言行为也只在 PostgreSQL 上有意义。
连接串只从 YUMI_TEST_POSTGRES_URL 读取，复用保留期测试的本机专用库校验与清表夹具；
缺少变量时跳过，跳过不能计为通过。微信换成本地替身，不访问真实接口。
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from homestay_bot.domain.enums import CustomerIdentityProvider
from homestay_bot.domain.models import Customer, CustomerIdentity, MiniProgramSession
from homestay_bot.repositories import miniprogram_sessions
from homestay_bot.repositories.customers import SQLAlchemyCustomerRepository
from homestay_bot.repositories.miniprogram_sessions import SQLAlchemyMiniProgramSessionRepository
from homestay_bot.services.miniprogram_auth import MiniProgramAuthService
from tests.integration.test_retention_postgresql import pg_engine  # noqa: F401

APP_ID = "wx0123456789abcdef"
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
# 等待另一会话进入锁等待的上限；远小于夹具设置的 5 秒 lock_timeout。
_LOCK_WAIT_TIMEOUT_SECONDS = 3.0


class _FixedExchanger:
    """所有 code 都换出同一个 OpenID，模拟同一客人在两台设备上同时首登。"""

    app_id = APP_ID

    async def exchange_code(self, code: str) -> str:
        """忽略 code，返回固定 OpenID。"""
        return "o-concurrent"


async def _wait_for_identity_insert_lock(engine: AsyncEngine) -> bool:
    """从独立连接轮询 pg_stat_activity，直到看到身份插入在等待另一事务结束。

    唯一索引遇到未提交的同键行时，插入方等待对方的 transactionid 锁；
    同时核对正在执行的语句，确认等待点就是 customer_identities 的插入。
    """
    deadline = asyncio.get_running_loop().time() + _LOCK_WAIT_TIMEOUT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        async with engine.connect() as connection:
            waiting = await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND pid <> pg_backend_pid() "
                    "AND wait_event_type = 'Lock' AND wait_event = 'transactionid' "
                    "AND query ILIKE 'INSERT INTO customer_identities%'"
                )
            )
        if waiting:
            return True
        await asyncio.sleep(0.05)
    return False


async def test_concurrent_first_login_creates_one_customer_and_both_tokens_resolve(
    pg_engine: AsyncEngine,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 建档后持事务不提交，B 的身份插入等待 A 的事务；A 提交后 B 回退保存点复用同一身份。"""
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    service = MiniProgramAuthService(factory, exchanger=_FixedExchanger(), clock=lambda: NOW)
    original_create = SQLAlchemyMiniProgramSessionRepository.create
    a_holding = asyncio.Event()
    release_a = asyncio.Event()

    async def create_holding_first(self, **kwargs) -> None:
        """第一次调用（A）写完会话后停在未提交事务里，第二次调用（B）照常执行。"""
        await original_create(self, **kwargs)
        if not a_holding.is_set():
            a_holding.set()
            await release_a.wait()

    monkeypatch.setattr(
        miniprogram_sessions.SQLAlchemyMiniProgramSessionRepository,
        "create",
        create_holding_first,
    )

    login_a = asyncio.create_task(service.login("code-a"))
    await asyncio.wait_for(a_holding.wait(), timeout=5)
    login_b = asyncio.create_task(service.login("code-b"))
    # 直接观测到 B 的身份插入在等 A 的事务结束，证明两个事务确实重叠、走到了唯一键冲突分支。
    assert await _wait_for_identity_insert_lock(pg_engine)
    assert not login_b.done()
    release_a.set()
    first, second = await asyncio.wait_for(asyncio.gather(login_a, login_b), timeout=10)

    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Customer)) == 1
        assert await session.scalar(select(func.count()).select_from(CustomerIdentity)) == 1
        assert await session.scalar(select(func.count()).select_from(MiniProgramSession)) == 2
    assert first.token != second.token
    assert await service.profile(first.token) is not None
    assert await service.profile(second.token) is not None


async def _seed_identity(factory: async_sessionmaker, external_id: str) -> int:
    """建一个小程序身份并返回身份编号。"""
    async with factory() as session, session.begin():
        await SQLAlchemyCustomerRepository(session).ensure_identity(
            provider=CustomerIdentityProvider.WECHAT_MINIPROGRAM,
            external_id=external_id,
            display_name="小程序客户",
        )
        identity_id = await SQLAlchemyMiniProgramSessionRepository(session).identity_id_for(
            external_id
        )
    assert identity_id is not None
    return identity_id


async def test_expired_cleanup_subquery_respects_limit_and_rolls_back(
    pg_engine: AsyncEngine,  # noqa: F811
) -> None:
    """全局过期清理按到期先后只删上限条数；登录事务失败时新会话与清理一并回滚。"""
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    stale_owner = await _seed_identity(factory, f"{APP_ID}:o-stale")
    new_owner = await _seed_identity(factory, f"{APP_ID}:o-new")
    async with factory() as session, session.begin():
        session.add_all(
            [
                MiniProgramSession(
                    token_hash=f"{index:064x}",
                    identity_id=stale_owner,
                    expires_at=NOW - timedelta(hours=10 - index),
                )
                for index in range(5)
            ]
        )

    def create_args(token_hash: str) -> dict[str, object]:
        """为 new_owner 签发一条会话，全局过期清理上限 2。"""
        return {
            "identity_id": new_owner,
            "token_hash": token_hash,
            "now": NOW,
            "expires_at": NOW + timedelta(hours=24),
            "max_per_identity": 5,
            "expired_cleanup_limit": 2,
        }

    with pytest.raises(RuntimeError, match="模拟提交前失败"):
        async with factory() as session, session.begin():
            await SQLAlchemyMiniProgramSessionRepository(session).create(**create_args("a" * 64))
            raise RuntimeError("模拟提交前失败")
    async with factory() as session:
        remaining = list(await session.scalars(select(MiniProgramSession.token_hash)))
    assert sorted(remaining) == [f"{index:064x}" for index in range(5)]

    async with factory() as session, session.begin():
        await SQLAlchemyMiniProgramSessionRepository(session).create(**create_args("b" * 64))
    async with factory() as session:
        remaining = list(await session.scalars(select(MiniProgramSession.token_hash)))
    # 到期最早的两条（index 0、1）被删，其余过期行留给后续登录，新会话已写入。
    assert sorted(remaining) == sorted([f"{index:064x}" for index in range(2, 5)] + ["b" * 64])
