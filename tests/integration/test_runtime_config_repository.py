import asyncio
from typing import cast

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from homestay_bot.domain.enums import EmployeeRole, RuntimeConfigVersionStatus
from homestay_bot.domain.models import (
    AuditLog,
    Base,
    Employee,
    RuntimeConfigState,
    RuntimeConfigVersion,
)
from homestay_bot.repositories.runtime_config import (
    RuntimeConfigConflictError,
    SQLAlchemyRuntimeConfigRepository,
)


async def build_factory(database_url: str) -> async_sessionmaker[AsyncSession]:
    """创建包含真实外键和唯一管理员员工的测试数据库。"""
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            Employee(
                id=1,
                wecom_userid="runtime-admin",
                name="YuMi",
                role=EmployeeRole.ADMIN,
                is_active=True,
            )
        )
        await session.commit()
    return factory


async def dispose_factory(factory: async_sessionmaker[AsyncSession]) -> None:
    """显式关闭测试引擎，避免 aiosqlite 线程跨事件循环泄漏。"""
    await cast(AsyncEngine, factory.kw["bind"]).dispose()


@pytest.mark.asyncio
async def test_activate_and_rollback_keep_correct_version_pointers() -> None:
    """激活和回滚必须原子推进 active、previous 与 revision。"""
    factory = await build_factory("sqlite+aiosqlite:///:memory:")
    async with factory() as session:
        repository = SQLAlchemyRuntimeConfigRepository(session)
        first = await repository.create_candidate(b"cipher-one", {"configured": True}, 1)
        await repository.mark_test_passed(first.id, {"succeeded": True})
        initial = await repository.get_state()
        assert initial.revision == 0

        first_state = await repository.activate(first.id, expected_revision=0)
        second = await repository.create_candidate(
            b"cipher-two",
            {"configured": True},
            1,
            based_on_version_id=first.id,
            based_on_revision=1,
        )
        await repository.mark_test_passed(second.id, {"succeeded": True})
        second_state = await repository.activate(second.id, expected_revision=1)
        rolled_back = await repository.rollback(
            expected_revision=2,
            expected_previous_version_id=first.id,
        )
        await session.commit()

        assert (first_state.active_version_id, first_state.previous_version_id) == (first.id, None)
        assert (second_state.active_version_id, second_state.previous_version_id) == (
            second.id,
            first.id,
        )
        assert (
            rolled_back.active_version_id,
            rolled_back.previous_version_id,
            rolled_back.revision,
        ) == (first.id, second.id, 3)
    await dispose_factory(factory)


@pytest.mark.asyncio
async def test_concurrent_activation_allows_only_one_expected_revision(tmp_path) -> None:
    """两个实例使用同一修订号并发激活时只能有一个成功。"""
    factory = await build_factory(f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
    async with factory() as session:
        repository = SQLAlchemyRuntimeConfigRepository(session)
        one = await repository.create_candidate(b"one", {}, 1)
        two = await repository.create_candidate(b"two", {}, 1)
        await repository.mark_test_passed(one.id, {"succeeded": True})
        await repository.mark_test_passed(two.id, {"succeeded": True})
        await session.commit()

    async def activate(version_id: int) -> str:
        """在独立事务中竞争同一状态修订号。"""
        async with factory() as session:
            try:
                await SQLAlchemyRuntimeConfigRepository(session).activate(
                    version_id,
                    expected_revision=0,
                )
                await session.commit()
                return "activated"
            except RuntimeConfigConflictError:
                await session.rollback()
                return "conflict"

    results = await asyncio.gather(activate(one.id), activate(two.id))

    assert sorted(results) == ["activated", "conflict"]
    await dispose_factory(factory)


@pytest.mark.asyncio
async def test_retention_never_deletes_active_previous_or_latest_versions() -> None:
    """历史清理必须保留激活指针及指定数量的最新快照。"""
    factory = await build_factory("sqlite+aiosqlite:///:memory:")
    async with factory() as session:
        repository = SQLAlchemyRuntimeConfigRepository(session)
        versions = []
        for index in range(12):
            versions.append(
                await repository.create_candidate(
                    f"cipher-{index}".encode(),
                    {},
                    1,
                    based_on_version_id=versions[0].id if index else None,
                    based_on_revision=1 if index else 0,
                )
            )
        for version in versions:
            await repository.mark_test_passed(version.id, {"succeeded": True})
        await repository.activate(versions[0].id, expected_revision=0)
        await repository.activate(versions[1].id, expected_revision=1)
        deleted = await repository.prune(keep_latest=4)
        await session.commit()

        remaining = set(await session.scalars(select(RuntimeConfigVersion.id)))
        expected_latest = {version.id for version in versions[-4:]}
        assert {versions[0].id, versions[1].id} <= remaining
        assert expected_latest <= remaining
        assert deleted == 6
    await dispose_factory(factory)


@pytest.mark.asyncio
async def test_audit_details_only_store_fields_version_and_result() -> None:
    """配置审计不得复制掩码、密文、秘密值或测试响应。"""
    factory = await build_factory("sqlite+aiosqlite:///:memory:")
    async with factory() as session:
        repository = SQLAlchemyRuntimeConfigRepository(session)
        version = await repository.create_candidate(b"top-secret-cipher", {}, 1)
        await repository.add_audit(
            actor_id=1,
            action="runtime_config.activate",
            version_id=version.id,
            fields=("deepseek_api_key", "wecom_kf_secret"),
            result="ok",
            error_code=None,
        )
        await session.commit()

        audit = await session.scalar(select(AuditLog))
        assert audit is not None
        assert audit.details == {
            "version_id": version.id,
            "fields": ["deepseek_api_key", "wecom_kf_secret"],
            "result": "ok",
        }
        serialized = repr(audit.details)
        assert "top-secret-cipher" not in serialized
    await dispose_factory(factory)


@pytest.mark.asyncio
async def test_failed_candidate_is_traceable_but_cannot_be_activated() -> None:
    """失败候选保存脱敏结果与稳定错误码，但激活入口必须拒绝。"""
    factory = await build_factory("sqlite+aiosqlite:///:memory:")
    async with factory() as session:
        repository = SQLAlchemyRuntimeConfigRepository(session)
        candidate = await repository.create_candidate(
            b"failed-cipher",
            {"deepseek_api_key": "已配置 ····1234"},
            1,
            based_on_version_id=None,
            based_on_revision=0,
        )
        await repository.mark_test_failed(
            candidate.id,
            {"succeeded": False, "error_code": "deepseek_auth_failed"},
            failure_code="deepseek_auth_failed",
        )

        with pytest.raises(LookupError):
            await repository.activate(candidate.id, expected_revision=0)
        await session.commit()

        stored = await repository.get_version(candidate.id)
        state = await repository.get_state()
        assert stored is not None
        assert stored.status is RuntimeConfigVersionStatus.TEST_FAILED
        assert stored.failure_code == "deepseek_auth_failed"
        assert stored.test_results == {
            "succeeded": False,
            "error_code": "deepseek_auth_failed",
        }
        assert state.active_version_id is None
    await dispose_factory(factory)


@pytest.mark.asyncio
async def test_state_constraints_reject_negative_revision_and_equal_pointers() -> None:
    """数据库约束必须阻止负修订号和相同 active/previous 指针。"""
    factory = await build_factory("sqlite+aiosqlite:///:memory:")
    async with factory() as session:
        session.add(RuntimeConfigState(id=1, revision=-1))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        version = RuntimeConfigVersion(
            encrypted_payload=b"cipher",
            masked_summary={},
            created_by=1,
            status=RuntimeConfigVersionStatus.TEST_PASSED,
            test_results={"succeeded": True},
            based_on_revision=0,
        )
        session.add(version)
        await session.flush()
        session.add(
            RuntimeConfigState(
                id=1,
                revision=1,
                active_version_id=version.id,
                previous_version_id=version.id,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
    await dispose_factory(factory)
