import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.application import LOCAL_ADMIN_WECOM_USERID, _bootstrap_admin_auth
from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.domain.models import AdminCredential, Base, Employee
from homestay_bot.services.admin_passwords import ADMIN_PASSWORD_HASHER


@pytest.mark.asyncio
async def test_existing_credential_skips_bootstrap_requirements(tmp_path) -> None:
    """既有凭证优先于引导变量和员工活动状态，重复启动不得改绑身份。"""
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
                password_hash="existing-hash",
            )
        )
        await session.commit()

    assert await _bootstrap_admin_auth(factory, username=None, password_hash=None)
    assert await _bootstrap_admin_auth(
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
