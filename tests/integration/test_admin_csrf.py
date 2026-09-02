import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.models import AdminCsrfNonce, AdminCsrfQuota, Base
from homestay_bot.repositories.admin_csrf import SQLAlchemyAdminCsrfRepository
from homestay_bot.services.admin_csrf import AdminCsrfCapacityError, AdminCsrfService


@pytest.mark.asyncio
async def test_csrf_stores_only_hash_and_consumes_once() -> None:
    """CSRF 明文只返回浏览器，数据库仅存 SHA-256 且只能消费一次。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 11, 12, tzinfo=UTC)

    async with factory() as session:
        service = AdminCsrfService(
            SQLAlchemyAdminCsrfRepository(session),
            clock=lambda: now,
        )
        token = await service.issue("login", admin_id=None)
        await session.commit()
        stored = await session.scalar(select(AdminCsrfNonce))
        assert stored is not None
        assert stored.token_hash != token
        assert token not in stored.token_hash

        assert await service.consume(token, "login", admin_id=None) is True
        assert await service.consume(token, "login", admin_id=None) is False
        await session.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_csrf_purpose_admin_and_expiry_must_match() -> None:
    """用途、管理员或有效期不匹配时不得消费 nonce。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 11, 12, tzinfo=UTC)
    async with factory() as session:
        service = AdminCsrfService(
            SQLAlchemyAdminCsrfRepository(session),
            clock=lambda: now,
            ttl=timedelta(minutes=5),
        )
        token = await service.issue("password", admin_id=1)
        await session.commit()
        assert await service.consume(token, "logout", admin_id=1) is False
        assert await service.consume(token, "password", admin_id=2) is False
        service = AdminCsrfService(
            SQLAlchemyAdminCsrfRepository(session),
            clock=lambda: now + timedelta(minutes=6),
        )
        assert await service.consume(token, "password", admin_id=1) is False
        assert await session.scalar(select(func.count(AdminCsrfNonce.id))) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_two_transactions_can_only_consume_nonce_once(tmp_path) -> None:
    """两个携带相同旧 Cookie 和 token 的事务并发消费时只能一个成功。"""
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'csrf.db'}?timeout=30"
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 11, 12, tzinfo=UTC)
    async with factory() as session:
        token = await AdminCsrfService(
            SQLAlchemyAdminCsrfRepository(session), clock=lambda: now
        ).issue("login", admin_id=None)
        await session.commit()

    async def consume_once() -> bool:
        """在独立事务原子消费同一 nonce。"""
        async with factory() as session:
            consumed = await AdminCsrfService(
                SQLAlchemyAdminCsrfRepository(session), clock=lambda: now
            ).consume(token, "login", admin_id=None)
            await session.commit()
            return consumed

    assert sorted(await asyncio.gather(consume_once(), consume_once())) == [False, True]
    await engine.dispose()


@pytest.mark.asyncio
async def test_issue_purges_expired_nonces_before_enforcing_capacity() -> None:
    """签发前应有界清理过期 nonce，并只按仍活动记录执行硬上限。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 11, 12, tzinfo=UTC)
    async with factory() as session:
        session.add_all(
            [
                AdminCsrfNonce(
                    token_hash=f"{index:064x}",
                    purpose="login",
                    expires_at=now - timedelta(seconds=index + 1),
                )
                for index in range(2)
            ]
        )
        await session.commit()
        service = AdminCsrfService(
            SQLAlchemyAdminCsrfRepository(session),
            clock=lambda: now,
            max_active=1,
            purge_limit=2,
        )

        await service.issue("login", admin_id=None)
        await session.commit()

        assert await session.scalar(select(func.count(AdminCsrfNonce.id))) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_issue_rejects_when_active_nonce_capacity_is_full() -> None:
    """活动 nonce 达到硬上限时必须稳定拒绝继续扩张表。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 11, 12, tzinfo=UTC)
    async with factory() as session:
        service = AdminCsrfService(
            SQLAlchemyAdminCsrfRepository(session),
            clock=lambda: now,
            max_active=1,
        )
        await service.issue("login", admin_id=None)
        with pytest.raises(AdminCsrfCapacityError):
            await service.issue("login", admin_id=None)
    await engine.dispose()


@pytest.mark.asyncio
async def test_csrf_limits_each_admin_purpose_to_eight_parallel_tabs() -> None:
    """同一管理员同一动作最多保留八个 nonce，其他动作仍有独立容量。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 11, 12, tzinfo=UTC)
    async with factory() as session:
        service = AdminCsrfService(
            SQLAlchemyAdminCsrfRepository(session),
            clock=lambda: now,
            max_active=100,
            max_active_per_scope=8,
        )
        for _ in range(8):
            await service.issue("runtime-config-activate", admin_id=1)
        with pytest.raises(AdminCsrfCapacityError):
            await service.issue("runtime-config-activate", admin_id=1)

        other_purpose = await service.issue("runtime-config-rollback", admin_id=1)
        other_admin = await service.issue("runtime-config-activate", admin_id=2)
        assert other_purpose
        assert other_admin
    await engine.dispose()


@pytest.mark.asyncio
async def test_two_services_atomically_share_database_nonce_capacity(tmp_path) -> None:
    """两个独立服务在最后一个配额槽位并发签发时只能一个成功。"""
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'quota.db'}?timeout=30"
    first_engine = create_async_engine(database_url)
    second_engine = create_async_engine(database_url)
    async with first_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    first_factory = async_sessionmaker(first_engine, expire_on_commit=False)
    second_factory = async_sessionmaker(second_engine, expire_on_commit=False)
    now = datetime(2026, 8, 11, 12, tzinfo=UTC)

    async def issue_once(factory) -> bool:
        """在独立事务尝试预占唯一槽位并提交。"""
        async with factory() as session:
            try:
                await AdminCsrfService(
                    SQLAlchemyAdminCsrfRepository(session),
                    clock=lambda: now,
                    max_active=1,
                ).issue("login", admin_id=None)
            except AdminCsrfCapacityError:
                await session.rollback()
                return False
            await session.commit()
            return True

    results = await asyncio.gather(
        issue_once(first_factory),
        issue_once(second_factory),
    )

    assert sorted(results) == [False, True]
    async with first_factory() as session:
        quota = await session.get(AdminCsrfQuota, 1)
        assert quota is not None and quota.active_count == 1
        assert await session.scalar(select(func.count(AdminCsrfNonce.id))) == 1
    await first_engine.dispose()
    await second_engine.dispose()


@pytest.mark.asyncio
async def test_consume_and_expiry_purge_keep_quota_equal_to_nonce_count(tmp_path) -> None:
    """成功消费和过期清理都必须在同一事务同步递减数据库配额。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'quota-sync.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 11, 12, tzinfo=UTC)
    async with factory() as session:
        service = AdminCsrfService(
            SQLAlchemyAdminCsrfRepository(session),
            clock=lambda: now,
            ttl=timedelta(seconds=1),
            max_active=3,
        )
        first = await service.issue("login", admin_id=None)
        await service.issue("login", admin_id=None)
        assert await service.consume(first, "login", admin_id=None)
        await session.commit()

    async with factory() as session:
        later = AdminCsrfService(
            SQLAlchemyAdminCsrfRepository(session),
            clock=lambda: now + timedelta(seconds=2),
            max_active=3,
        )
        await later.issue("login", admin_id=None)
        await session.commit()
        quota = await session.get(AdminCsrfQuota, 1)
        nonce_count = await session.scalar(select(func.count(AdminCsrfNonce.id)))
        assert quota is not None
        assert quota.active_count == nonce_count == 1
    await engine.dispose()


async def _memory_csrf_factory() -> async_sessionmaker:
    """创建仅供本文件新增用例使用的内存库会话工厂。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_full_scope_evicts_oldest_nonce_when_requested() -> None:
    """开启淘汰后，作用域满仍可签发，且最旧 nonce 立即失效。"""
    factory = await _memory_csrf_factory()
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    async with factory() as session:
        service = AdminCsrfService(
            SQLAlchemyAdminCsrfRepository(session),
            clock=lambda: now,
            max_active=100,
            max_active_per_scope=8,
        )
        tokens = [
            await service.issue("task-write:1", admin_id=1, evict_oldest_in_scope=True)
            for _ in range(8)
        ]
        # 第九个必须成功签发，否则顺序浏览会在 GET 阶段就 429。
        ninth = await service.issue(
            "task-write:1", admin_id=1, evict_oldest_in_scope=True
        )
        await session.commit()

        assert ninth
        assert await service.consume(tokens[0], "task-write:1", admin_id=1) is False
        assert await service.consume(tokens[1], "task-write:1", admin_id=1) is True
        await session.commit()


@pytest.mark.asyncio
async def test_full_scope_still_rejects_without_eviction() -> None:
    """不开启淘汰时必须保持既有拒绝语义，登录路径依赖它抵抗滥用。"""
    factory = await _memory_csrf_factory()
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    async with factory() as session:
        service = AdminCsrfService(
            SQLAlchemyAdminCsrfRepository(session),
            clock=lambda: now,
            max_active=100,
            max_active_per_scope=8,
        )
        for _ in range(8):
            await service.issue("task-write:1", admin_id=1)
        with pytest.raises(AdminCsrfCapacityError):
            await service.issue("task-write:1", admin_id=1)


@pytest.mark.asyncio
async def test_eviction_keeps_quota_equal_to_nonce_count() -> None:
    """淘汰路径必须按实删数回冲配额，否则活动计数会持续漂高。"""
    factory = await _memory_csrf_factory()
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    async with factory() as session:
        service = AdminCsrfService(
            SQLAlchemyAdminCsrfRepository(session),
            clock=lambda: now,
            max_active=100,
            max_active_per_scope=8,
        )
        for _ in range(12):
            await service.issue(
                "task-write:1", admin_id=1, evict_oldest_in_scope=True
            )
        await session.commit()

        nonce_count = await session.scalar(select(func.count(AdminCsrfNonce.id)))
        quota = await session.scalar(
            select(AdminCsrfQuota.active_count).where(AdminCsrfQuota.id == 1)
        )
        assert nonce_count == 8
        assert quota == nonce_count


@pytest.mark.asyncio
async def test_issue_accepts_ttl_override() -> None:
    """运营表单需要比默认十五分钟更长的有效期，逐次覆盖不改默认值。"""
    factory = await _memory_csrf_factory()
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    async with factory() as session:
        service = AdminCsrfService(
            SQLAlchemyAdminCsrfRepository(session),
            clock=lambda: now,
        )
        await service.issue("task-write:1", admin_id=1, ttl=timedelta(hours=8))
        await service.issue("approval-confirm:1", admin_id=1)
        await session.commit()

        long_lived = await session.scalar(
            select(AdminCsrfNonce.expires_at).where(
                AdminCsrfNonce.purpose == "task-write:1"
            )
        )
        default_lived = await session.scalar(
            select(AdminCsrfNonce.expires_at).where(
                AdminCsrfNonce.purpose == "approval-confirm:1"
            )
        )
        assert _as_utc(long_lived) == now + timedelta(hours=8)
        assert _as_utc(default_lived) == now + timedelta(minutes=15)


@pytest.mark.asyncio
async def test_admin_nonces_reserve_capacity_for_anonymous_login() -> None:
    """管理员表单占满自身天花板后，未登录访客仍须能取得登录令牌。"""
    factory = await _memory_csrf_factory()
    now = datetime(2026, 9, 3, 12, tzinfo=UTC)
    async with factory() as session:
        service = AdminCsrfService(
            SQLAlchemyAdminCsrfRepository(session),
            clock=lambda: now,
            max_active=100,
            max_active_per_scope=8,
            max_active_anonymous=20,
        )
        issued = 0
        for index in range(200):
            try:
                await service.issue(f"task-write:{index}", admin_id=1)
            except AdminCsrfCapacityError:
                break
            issued += 1
        await session.commit()

        # 管理员用尽自身额度后不得挤占匿名子池，否则登录页会直接 429。
        assert issued < 100
        assert await service.issue("login", admin_id=None)
        await session.commit()


def _as_utc(value: datetime) -> datetime:
    """把 SQLite 读回的无时区时间统一为 UTC 以便比较。"""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
