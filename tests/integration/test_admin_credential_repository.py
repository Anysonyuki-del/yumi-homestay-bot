import asyncio
from datetime import UTC, datetime

import pytest
from argon2 import PasswordHasher, Type
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.domain.models import AdminCredential, Base, Employee
from homestay_bot.repositories.admin_credentials import (
    SQLAlchemyAdminCredentialRepository,
)
from homestay_bot.services.admin_auth_service import (
    AdminAuthService,
    AuthenticationError,
)


@pytest.mark.asyncio
async def test_bootstrap_imports_only_precomputed_hash_once() -> None:
    """首次引导只导入预生成哈希，重复执行不能覆盖现有管理员密码。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    initial_hash = PasswordHasher(type=Type.ID).hash("bootstrap-password")
    replacement_hash = PasswordHasher(type=Type.ID).hash("other-password")

    async with factory() as session:
        employee = Employee(
            wecom_userid="console-admin",
            name="后台管理员",
            role=EmployeeRole.ADMIN,
        )
        session.add(employee)
        await session.flush()
        repository = SQLAlchemyAdminCredentialRepository(session)

        created = await repository.bootstrap(
            employee_id=employee.id,
            username="admin",
            password_hash=initial_hash,
        )
        repeated = await repository.bootstrap(
            employee_id=employee.id,
            username="other-admin",
            password_hash=replacement_hash,
        )
        await session.commit()

        assert created.id == 1
        assert repeated.id == created.id
        assert repeated.username == "admin"
        assert repeated.password_hash == initial_hash
        assert "bootstrap-password" not in repeated.password_hash
        assert await session.scalar(select(func.count(AdminCredential.id))) == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_repository_atomic_methods_persist_lock_and_session_state() -> None:
    """仓储原子方法应持久化认证失败计数和会话版本。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    password_hash = PasswordHasher(type=Type.ID).hash("bootstrap-password")

    async with factory() as session:
        employee = Employee(
            wecom_userid="persisted-admin",
            name="后台管理员",
            role=EmployeeRole.ADMIN,
        )
        session.add(employee)
        await session.flush()
        repository = SQLAlchemyAdminCredentialRepository(session)
        credential = await repository.bootstrap(
            employee_id=employee.id,
            username="admin",
            password_hash=password_hash,
        )
        now = datetime(2026, 8, 11, 8, tzinfo=UTC)
        for _ in range(4):
            await repository.record_failed_attempt(
                credential.id,
                now=now,
                lock_until=now.replace(minute=15),
            )
        await repository.increment_session_version(credential.id)
        await repository.increment_session_version(credential.id)
        await session.commit()

    async with factory() as session:
        repository = SQLAlchemyAdminCredentialRepository(session)
        loaded = await repository.get_by_username("admin")

        assert loaded is not None
        assert loaded.failed_attempts == 4
        assert loaded.session_version == 3
        assert await repository.get_by_id(1) is loaded

    await engine.dispose()


@pytest.mark.asyncio
async def test_bootstrap_rejects_forged_argon2id_prefix() -> None:
    """仓储必须解析 Argon2 编码，不能只信任可伪造的算法前缀。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        employee = Employee(
            wecom_userid="forged-hash-admin",
            name="后台管理员",
            role=EmployeeRole.ADMIN,
        )
        session.add(employee)
        await session.flush()

        with pytest.raises(ValueError, match="Argon2id"):
            await SQLAlchemyAdminCredentialRepository(session).bootstrap(
                employee_id=employee.id,
                username="admin",
                password_hash="$argon2id$plaintext",
            )

    await engine.dispose()


async def _create_concurrent_admin_database(database_url: str) -> None:
    """为文件型 SQLite 并发测试创建唯一管理员。"""
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        employee = Employee(
            wecom_userid="concurrent-admin",
            name="后台管理员",
            role=EmployeeRole.ADMIN,
        )
        session.add(employee)
        await session.flush()
        await SQLAlchemyAdminCredentialRepository(session).bootstrap(
            employee_id=employee.id,
            username="admin",
            password_hash=PasswordHasher(type=Type.ID).hash("initial-password"),
        )
        await session.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_failed_logins_increment_without_loss(tmp_path) -> None:
    """两个独立 SQLite 会话并发登录失败后计数必须精确为二。"""
    database_url = (
        f"sqlite+aiosqlite:///{tmp_path / 'failed-login.db'}?timeout=30"
    )
    await _create_concurrent_admin_database(database_url)
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 11, 10, tzinfo=UTC)

    async def fail_once() -> None:
        """在独立事务执行一次失败登录并提交计数。"""
        async with factory() as session:
            service = AdminAuthService(SQLAlchemyAdminCredentialRepository(session))
            with pytest.raises(AuthenticationError):
                await service.authenticate("admin", "wrong-password", now)
            await session.commit()

    await asyncio.gather(fail_once(), fail_once())
    async with factory() as session:
        credential = await SQLAlchemyAdminCredentialRepository(session).get_by_id(1)
        assert credential is not None
        assert credential.failed_attempts == 2

    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_session_revocation_never_loses_version(tmp_path) -> None:
    """两个独立 SQLite 会话并发撤销时版本必须分别返回二、三。"""
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'revoke.db'}?timeout=30"
    await _create_concurrent_admin_database(database_url)
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def revoke_once() -> int:
        """在独立事务原子递增并提交一次会话版本。"""
        async with factory() as session:
            version = await AdminAuthService(
                SQLAlchemyAdminCredentialRepository(session)
            ).revoke_other_sessions(1)
            await session.commit()
            return version

    versions = await asyncio.gather(revoke_once(), revoke_once())
    assert sorted(versions) == [2, 3]
    async with factory() as session:
        credential = await SQLAlchemyAdminCredentialRepository(session).get_by_id(1)
        assert credential is not None
        assert credential.session_version == 3

    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_password_change_uses_hash_compare_and_swap(tmp_path) -> None:
    """同一旧密码并发改密只能成功一次，且版本与最终哈希保持一致。"""
    database_url = (
        f"sqlite+aiosqlite:///{tmp_path / 'change-password.db'}?timeout=30"
    )
    await _create_concurrent_admin_database(database_url)
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def change_once(new_password: str) -> tuple[str, str]:
        """在独立事务尝试用同一旧密码完成一次 CAS 改密。"""
        async with factory() as session:
            service = AdminAuthService(SQLAlchemyAdminCredentialRepository(session))
            try:
                await service.change_password(
                    1,
                    "initial-password",
                    new_password,
                )
            except AuthenticationError:
                await session.commit()
                return ("rejected", new_password)
            await session.commit()
            return ("changed", new_password)

    results = await asyncio.gather(
        change_once("first-secure-password"),
        change_once("second-secure-password"),
    )
    assert sorted(result[0] for result in results) == ["changed", "rejected"]
    successful_password = next(
        password for status, password in results if status == "changed"
    )
    async with factory() as session:
        repository = SQLAlchemyAdminCredentialRepository(session)
        credential = await repository.get_by_id(1)
        assert credential is not None
        assert credential.session_version == 2
        await AdminAuthService(repository).reverify(1, successful_password)

    await engine.dispose()
