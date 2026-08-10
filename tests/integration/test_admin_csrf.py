import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.models import AdminCsrfNonce, Base
from homestay_bot.repositories.admin_csrf import SQLAlchemyAdminCsrfRepository
from homestay_bot.services.admin_csrf import AdminCsrfService


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
