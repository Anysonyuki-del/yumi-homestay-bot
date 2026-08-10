import asyncio

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.application import LOCAL_ADMIN_WECOM_USERID, _bootstrap_admin_auth
from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.domain.models import AdminCredential, Base, Employee
from homestay_bot.services.admin_passwords import ADMIN_PASSWORD_HASHER


@pytest.mark.asyncio
async def test_wrong_employee_binding_degrades_existing_admin_auth(tmp_path) -> None:
    """即使哈希合法，绑定非保留或停用员工的既有凭证也不能视为健康。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'existing.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        employee = Employee(
            wecom_userid="historical-admin",
            name="历史管理员",
            role=EmployeeRole.ADMIN,
            is_active=False,
        )
        session.add(employee)
        await session.flush()
        session.add(
            AdminCredential(
                id=1,
                employee_id=employee.id,
                username="existing",
                password_hash=ADMIN_PASSWORD_HASHER.hash("existing-password"),
            )
        )
        await session.commit()

    assert not await _bootstrap_admin_auth(factory, username=None, password_hash=None)
    assert not await _bootstrap_admin_auth(
        factory,
        username="ignored",
        password_hash="ignored-hash",
    )
    async with factory() as session:
        credential = await session.scalar(select(AdminCredential))
        assert credential is not None
        assert credential.username == "existing"
        assert await session.scalar(select(func.count(Employee.id))) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_valid_existing_local_credential_needs_no_bootstrap_variables(tmp_path) -> None:
    """合法且启用的保留本地身份凭证可在无引导变量时直接复用。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'valid.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        employee = Employee(
            wecom_userid=LOCAL_ADMIN_WECOM_USERID,
            name="本地后台管理员",
            role=EmployeeRole.ADMIN,
            is_active=True,
        )
        session.add(employee)
        await session.flush()
        session.add(
            AdminCredential(
                id=1,
                employee_id=employee.id,
                username="existing",
                password_hash=ADMIN_PASSWORD_HASHER.hash("existing-password"),
            )
        )
        await session.commit()

    assert await _bootstrap_admin_auth(factory, username=None, password_hash=None)
    async with factory() as session:
        credential = await session.scalar(select(AdminCredential))
        assert credential is not None
        credential.password_hash = "invalid-hash"
        await session.commit()
    assert not await _bootstrap_admin_auth(factory, username=None, password_hash=None)
    await engine.dispose()


@pytest.mark.asyncio
async def test_first_bootstrap_uses_only_reserved_local_employee(tmp_path) -> None:
    """首次引导不得选择任意历史管理员，只创建明确保留的本地审计身份。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'first.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all(
            [
                Employee(
                    wecom_userid="admin-a",
                    name="管理员甲",
                    role=EmployeeRole.ADMIN,
                    is_active=True,
                ),
                Employee(
                    wecom_userid="admin-b",
                    name="管理员乙",
                    role=EmployeeRole.ADMIN,
                    is_active=True,
                ),
            ]
        )
        await session.commit()

    assert await _bootstrap_admin_auth(
        factory,
        username="local-admin",
        password_hash=ADMIN_PASSWORD_HASHER.hash("bootstrap-password"),
    )
    async with factory() as session:
        credential = await session.scalar(select(AdminCredential))
        local = await session.scalar(
            select(Employee).where(Employee.wecom_userid == LOCAL_ADMIN_WECOM_USERID)
        )
        assert credential is not None and local is not None
        assert credential.employee_id == local.id
        assert local.role is EmployeeRole.ADMIN
        assert local.is_active is True
    await engine.dispose()


@pytest.mark.asyncio
async def test_missing_bootstrap_reports_admin_unavailable_without_employee(tmp_path) -> None:
    """无既有凭证且缺少引导变量时应明确降级，不创建或猜测管理员身份。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'missing.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    assert not await _bootstrap_admin_auth(factory, username=None, password_hash=None)
    async with factory() as session:
        assert await session.scalar(select(func.count(Employee.id))) == 0
        assert await session.scalar(select(func.count(AdminCredential.id))) == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_two_instances_can_bootstrap_same_sqlite_database(tmp_path) -> None:
    """两个独立会话工厂并发首次引导后都应重读到同一合法凭证。"""
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'concurrent.db'}?timeout=30"
    first_engine = create_async_engine(database_url)
    second_engine = create_async_engine(database_url)
    async with first_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    first_factory = async_sessionmaker(first_engine, expire_on_commit=False)
    second_factory = async_sessionmaker(second_engine, expire_on_commit=False)
    password_hash = ADMIN_PASSWORD_HASHER.hash("bootstrap-password")

    results = await asyncio.gather(
        _bootstrap_admin_auth(
            first_factory,
            username="admin",
            password_hash=password_hash,
        ),
        _bootstrap_admin_auth(
            second_factory,
            username="admin",
            password_hash=password_hash,
        ),
    )

    assert results == [True, True]
    async with first_factory() as session:
        assert await session.scalar(select(func.count(Employee.id))) == 1
        assert await session.scalar(select(func.count(AdminCredential.id))) == 1
    await first_engine.dispose()
    await second_engine.dispose()
