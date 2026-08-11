import asyncio
from typing import cast

import pytest
from cryptography.fernet import Fernet
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
from homestay_bot.domain.runtime_config import RuntimeConfigSnapshot
from homestay_bot.repositories.runtime_config import (
    RuntimeConfigConflictError,
    SQLAlchemyRuntimeConfigRepository,
)
from homestay_bot.services.runtime_config_cipher import RuntimeConfigCipher
from homestay_bot.services.runtime_config_service import (
    RuntimeConfigService,
    RuntimeConfigTestResult,
    UpdateRuntimeConfig,
)


def runtime_snapshot(**overrides: object) -> RuntimeConfigSnapshot:
    """构造真实SQLite补偿测试使用的完整快照。"""
    values: dict[str, object] = {
        "deepseek_api_key": "deepseek-secret",
        "deepseek_base_url": "https://deepseek.example",
        "deepseek_model": "model",
        "hostex_access_token": "hostex-secret",
        "hostex_webhook_secret_token": "webhook-secret",
        "hostex_reconcile_interval_seconds": 600.0,
        "wecom_corp_id": "corp",
        "wecom_kf_secret": "kf-secret",
        "wecom_callback_token": "callback-token",
        "wecom_encoding_aes_key": "A" * 43,
        "wecom_agent_id": 100001,
        "wecom_agent_secret": "agent-secret",
        "wecom_contact_secret": None,
        "wecom_duty_userids": "owner",
        "wecom_poll_interval_seconds": 10.0,
    }
    values.update(overrides)
    return RuntimeConfigSnapshot(**values)  # type: ignore[arg-type]


class AllowAuth:
    """允许真实仓储补偿测试通过管理员复核。"""

    async def reverify_at_version(self, *args: object) -> None:
        """模拟成功复核。"""


class PassingTester:
    """让候选进入激活和运行时发布阶段。"""

    async def test(self, snapshot: RuntimeConfigSnapshot) -> RuntimeConfigTestResult:
        """验证候选完整并返回成功。"""
        snapshot.validate()
        return RuntimeConfigTestResult(succeeded=True)


class GatedCompensationRepository(SQLAlchemyRuntimeConfigRepository):
    """在真实restore SQL前暴露门闩，稳定注入第二次取消。"""

    def __init__(self, session: AsyncSession) -> None:
        """保存会话并初始化补偿门闩。"""
        super().__init__(session)
        self.restore_started = asyncio.Event()
        self.restore_proceed = asyncio.Event()

    async def restore_failed_activation(self, *args: object, **kwargs: object) -> bool:
        """等待测试第二次取消后再执行真实CAS补偿。"""
        self.restore_started.set()
        await self.restore_proceed.wait()
        return await super().restore_failed_activation(*args, **kwargs)  # type: ignore[arg-type]


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
async def test_failed_activation_compensation_restores_pointer_and_only_marks_candidate() -> None:
    """真实仓储补偿应恢复完整指针，且rollback目标不被误标为失败候选。"""
    factory = await build_factory("sqlite+aiosqlite:///:memory:")
    async with factory() as session:
        repository = SQLAlchemyRuntimeConfigRepository(session)
        first = await repository.create_candidate(b"one", {}, 1)
        await repository.mark_test_passed(first.id, {"succeeded": True})
        await repository.activate(first.id, expected_revision=0)
        second = await repository.create_candidate(
            b"two",
            {},
            1,
            based_on_version_id=first.id,
            based_on_revision=1,
        )
        await repository.mark_test_passed(second.id, {"succeeded": True})
        await repository.activate(second.id, expected_revision=1)

        restored = await repository.restore_failed_activation(
            second.id,
            failed_candidate_version_id=second.id,
            expected_revision=2,
            restore_revision=1,
            restore_active_version_id=first.id,
            restore_previous_version_id=None,
            failure_code="activation_failed",
        )
        await session.commit()

        state = await repository.get_state()
        stored_first = await repository.get_version(first.id)
        stored_second = await repository.get_version(second.id)
        assert restored is True
        assert (state.revision, state.active_version_id, state.previous_version_id) == (
            1,
            first.id,
            None,
        )
        assert stored_first is not None
        assert stored_first.status is RuntimeConfigVersionStatus.TEST_PASSED
        assert stored_second is not None
        assert stored_second.status is RuntimeConfigVersionStatus.ACTIVATION_FAILED
        assert stored_second.failure_code == "activation_failed"
    await dispose_factory(factory)


@pytest.mark.asyncio
async def test_failed_compensation_cas_does_not_mutate_reactivated_candidate(tmp_path) -> None:
    """补偿CAS失配时不得把已被其他操作重新激活的候选标记失败。"""
    factory = await build_factory(f"sqlite+aiosqlite:///{tmp_path / 'compensation-race.db'}")
    async with factory() as session:
        repository = SQLAlchemyRuntimeConfigRepository(session)
        first = await repository.create_candidate(b"one", {}, 1)
        await repository.mark_test_passed(first.id, {"succeeded": True})
        await repository.activate(first.id, expected_revision=0)
        second = await repository.create_candidate(
            b"two",
            {},
            1,
            based_on_version_id=first.id,
            based_on_revision=1,
        )
        await repository.mark_test_passed(second.id, {"succeeded": True})
        await repository.activate(second.id, expected_revision=1)
        await session.commit()

    # 另一实例连续回滚两次，使v2再次成为active，但revision已不再属于A操作。
    async with factory() as session:
        repository = SQLAlchemyRuntimeConfigRepository(session)
        await repository.rollback(
            expected_revision=2,
            expected_previous_version_id=first.id,
        )
        await repository.rollback(
            expected_revision=3,
            expected_previous_version_id=second.id,
        )
        await session.commit()

    async with factory() as session:
        repository = SQLAlchemyRuntimeConfigRepository(session)
        restored = await repository.restore_failed_activation(
            second.id,
            failed_candidate_version_id=second.id,
            expected_revision=2,
            restore_revision=1,
            restore_active_version_id=first.id,
            restore_previous_version_id=None,
            failure_code="activation_failed",
        )
        await session.commit()

    async with factory() as session:
        repository = SQLAlchemyRuntimeConfigRepository(session)
        state = await repository.get_state()
        stored_second = await repository.get_version(second.id)
        assert restored is False
        assert (state.revision, state.active_version_id, state.previous_version_id) == (
            4,
            second.id,
            first.id,
        )
        assert stored_second is not None
        assert stored_second.status is RuntimeConfigVersionStatus.TEST_PASSED
        assert stored_second.failure_code is None
    await dispose_factory(factory)


@pytest.mark.asyncio
async def test_create_compensation_survives_double_cancellation(tmp_path) -> None:
    """create运行发布取消后，第二次cancel也必须等真实SQLite补偿提交再传播。"""
    factory = await build_factory(f"sqlite+aiosqlite:///{tmp_path / 'create-cancel.db'}")
    cipher = RuntimeConfigCipher(Fernet.generate_key().decode())
    activation_started = asyncio.Event()
    repository_holder: list[GatedCompensationRepository] = []

    async def run_operation() -> None:
        """用独立真实会话执行首次候选激活。"""
        async with factory() as session:
            repository = GatedCompensationRepository(session)
            repository_holder.append(repository)

            async def activate_runtime(*args: object) -> None:
                """提交DB激活后等待首次取消。"""
                await session.commit()
                activation_started.set()
                await asyncio.Event().wait()

            service = RuntimeConfigService(
                repository=repository,
                cipher=cipher,
                auth=AllowAuth(),
                tester=PassingTester(),
                environment_snapshot=None,
                before_test=session.commit,
                activate_runtime=activate_runtime,
                after_compensation=session.commit,
            )
            await service.create_and_test(
                UpdateRuntimeConfig.from_snapshot(runtime_snapshot()),
                actor_id=1,
                admin_id=1,
                password="password",
                expected_session_version=1,
                expected_revision=0,
            )

    operation = asyncio.create_task(run_operation())
    await asyncio.wait_for(activation_started.wait(), timeout=1.0)
    operation.cancel()
    repository = repository_holder[0]
    await repository.restore_started.wait()
    operation.cancel()
    repository.restore_proceed.set()

    with pytest.raises(asyncio.CancelledError):
        await operation
    async with factory() as session:
        state = await session.get(RuntimeConfigState, 1)
        candidate = await session.scalar(select(RuntimeConfigVersion))
    assert state is not None
    assert (state.revision, state.active_version_id, state.previous_version_id) == (
        0,
        None,
        None,
    )
    assert candidate is not None
    assert candidate.status is RuntimeConfigVersionStatus.ACTIVATION_FAILED
    await dispose_factory(factory)


@pytest.mark.asyncio
async def test_rollback_compensation_survives_double_cancellation(tmp_path) -> None:
    """rollback运行发布取消后，第二次cancel也必须恢复原指针并提交。"""
    factory = await build_factory(f"sqlite+aiosqlite:///{tmp_path / 'rollback-cancel.db'}")
    cipher = RuntimeConfigCipher(Fernet.generate_key().decode())
    first_snapshot = runtime_snapshot(deepseek_model="first")
    second_snapshot = runtime_snapshot(deepseek_model="second")
    async with factory() as session:
        repository = SQLAlchemyRuntimeConfigRepository(session)
        first = await repository.create_candidate(
            cipher.encrypt(first_snapshot),
            first_snapshot.masked_view().to_dict(),
            1,
        )
        await repository.mark_test_passed(first.id, {"succeeded": True})
        await repository.activate(first.id, expected_revision=0)
        second = await repository.create_candidate(
            cipher.encrypt(second_snapshot),
            second_snapshot.masked_view().to_dict(),
            1,
            based_on_version_id=first.id,
            based_on_revision=1,
        )
        await repository.mark_test_passed(second.id, {"succeeded": True})
        await repository.activate(second.id, expected_revision=1)
        await session.commit()

    activation_started = asyncio.Event()
    repository_holder: list[GatedCompensationRepository] = []

    async def run_operation() -> None:
        """用独立真实会话执行回滚和取消补偿。"""
        async with factory() as session:
            repository = GatedCompensationRepository(session)
            repository_holder.append(repository)

            async def activate_runtime(*args: object) -> None:
                """提交回滚指针后等待首次取消。"""
                await session.commit()
                activation_started.set()
                await asyncio.Event().wait()

            service = RuntimeConfigService(
                repository=repository,
                cipher=cipher,
                auth=AllowAuth(),
                tester=PassingTester(),
                environment_snapshot=None,
                activate_runtime=activate_runtime,
                after_compensation=session.commit,
            )
            await service.rollback(
                actor_id=1,
                admin_id=1,
                password="password",
                expected_session_version=1,
                expected_revision=2,
                expected_previous_version_id=first.id,
            )

    operation = asyncio.create_task(run_operation())
    await asyncio.wait_for(activation_started.wait(), timeout=1.0)
    operation.cancel()
    repository = repository_holder[0]
    await repository.restore_started.wait()
    operation.cancel()
    repository.restore_proceed.set()

    with pytest.raises(asyncio.CancelledError):
        await operation
    async with factory() as session:
        state = await session.get(RuntimeConfigState, 1)
        stored_first = await session.get(RuntimeConfigVersion, first.id)
    assert state is not None
    assert (state.revision, state.active_version_id, state.previous_version_id) == (
        2,
        second.id,
        first.id,
    )
    assert stored_first is not None
    assert stored_first.status is RuntimeConfigVersionStatus.TEST_PASSED
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
            {
                "succeeded": False,
                "error_code": "deepseek_auth_failed",
                "providers": {
                    "deepseek": {
                        "succeeded": False,
                        "error_code": "deepseek_auth_failed",
                        "checks": {
                            "openai": {
                                "succeeded": False,
                                "error_code": "deepseek_auth_failed",
                            },
                            "anthropic": {"succeeded": True},
                        },
                    },
                    "hostex": {
                        "succeeded": True,
                        "checks": {"properties": {"succeeded": True}},
                    },
                    "wecom": {
                        "succeeded": True,
                        "callback_verification": "local_only",
                        "checks": {
                            "kf": {"succeeded": True},
                            "agent": {"succeeded": True},
                            "callback": {
                                "succeeded": True,
                                "verification": "local_only",
                            },
                        },
                    },
                },
            },
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
            "providers": {
                "deepseek": {
                    "succeeded": False,
                    "error_code": "deepseek_auth_failed",
                    "checks": {
                        "openai": {
                            "succeeded": False,
                            "error_code": "deepseek_auth_failed",
                        },
                        "anthropic": {"succeeded": True},
                    },
                },
                "hostex": {
                    "succeeded": True,
                    "checks": {"properties": {"succeeded": True}},
                },
                "wecom": {
                    "succeeded": True,
                    "callback_verification": "local_only",
                    "checks": {
                        "kf": {"succeeded": True},
                        "agent": {"succeeded": True},
                        "callback": {
                            "succeeded": True,
                            "verification": "local_only",
                        },
                    },
                },
            },
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
