import pytest
from argon2 import PasswordHasher, Type
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.domain.models import AdminCredential, Base, Employee
from homestay_bot.repositories.admin_credentials import (
    SQLAlchemyAdminCredentialRepository,
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
async def test_repository_persists_lock_and_session_state() -> None:
    """仓储应持久化认证锁定状态和会话版本。"""
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
        credential.failed_attempts = 4
        credential.session_version = 3
        await repository.save(credential)
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
