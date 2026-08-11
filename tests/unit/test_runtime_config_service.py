from dataclasses import dataclass
from datetime import datetime

import pytest
from cryptography.fernet import Fernet

from homestay_bot.domain.enums import RuntimeConfigVersionStatus
from homestay_bot.domain.runtime_config import RuntimeConfigSnapshot
from homestay_bot.repositories.runtime_config import RuntimeConfigConflictError
from homestay_bot.services.runtime_config_cipher import RuntimeConfigCipher
from homestay_bot.services.runtime_config_service import (
    RuntimeConfigCheckTestResult,
    RuntimeConfigProviderTestResult,
    RuntimeConfigService,
    RuntimeConfigTestError,
    RuntimeConfigTestResult,
    UpdateRuntimeConfig,
)


def test_provider_test_results_only_serialize_safe_status_fields() -> None:
    """候选测试元数据只允许固定供应商、布尔状态、稳定码和本地回调标记。"""
    result = RuntimeConfigTestResult(
        succeeded=False,
        error_code="hostex_auth_failed",
        providers=(
            RuntimeConfigProviderTestResult("deepseek", True),
            RuntimeConfigProviderTestResult("hostex", False, "hostex_auth_failed"),
            RuntimeConfigProviderTestResult(
                "wecom",
                True,
                callback_verification="local_only",
            ),
        ),
    )

    assert result.to_safe_dict() == {
        "succeeded": False,
        "error_code": "hostex_auth_failed",
        "providers": {
            "deepseek": {"succeeded": True},
            "hostex": {"succeeded": False, "error_code": "hostex_auth_failed"},
            "wecom": {"succeeded": True, "callback_verification": "local_only"},
        },
    }


def build_snapshot(**overrides: object) -> RuntimeConfigSnapshot:
    """构造完整外部集成快照。"""
    values: dict[str, object] = {
        "deepseek_api_key": "deepseek-secret-A1B2",
        "deepseek_base_url": "https://api.deepseek.example",
        "deepseek_model": "deepseek-v4-flash",
        "hostex_access_token": "hostex-secret-C3D4",
        "hostex_webhook_secret_token": "hostex-webhook-E5F6",
        "hostex_reconcile_interval_seconds": 900.0,
        "wecom_corp_id": "corp-G7H8",
        "wecom_kf_secret": "kf-secret-I9J0",
        "wecom_callback_token": "callback-K1L2",
        "wecom_encoding_aes_key": "A" * 43,
        "wecom_agent_id": 1000002,
        "wecom_agent_secret": "agent-M3N4",
        "wecom_contact_secret": "contact-O5P6",
        "wecom_duty_userids": "owner",
        "wecom_poll_interval_seconds": 10.0,
    }
    values.update(overrides)
    return RuntimeConfigSnapshot(**values)  # type: ignore[arg-type]


@dataclass
class VersionStub:
    """模拟带生命周期元数据的不可变版本。"""

    id: int
    encrypted_payload: bytes
    masked_summary: dict[str, object]
    based_on_version_id: int | None
    based_on_revision: int
    status: RuntimeConfigVersionStatus = RuntimeConfigVersionStatus.CANDIDATE
    test_results: dict[str, object] | None = None
    failure_code: str | None = None
    created_at: datetime | None = None
    created_by: int | None = None


@dataclass
class StateStub:
    """模拟激活状态。"""

    revision: int = 0
    active_version_id: int | None = None
    previous_version_id: int | None = None


class RepositoryStub:
    """以内存实现状态、候选生命周期和审计端口。"""

    def __init__(self) -> None:
        """初始化空状态和调用记录。"""
        self.state = StateStub()
        self.versions: dict[int, VersionStub] = {}
        self.audits: list[dict[str, object]] = []
        self.prune_calls: list[int] = []

    async def get_activation_context(self) -> tuple[StateStub, VersionStub | None]:
        """一次返回一致的状态和当前版本。"""
        active = (
            self.versions.get(self.state.active_version_id)
            if self.state.active_version_id is not None
            else None
        )
        return self.state, active

    async def get_state(self) -> StateStub:
        """返回当前状态。"""
        return self.state

    async def get_active_version(self) -> VersionStub | None:
        """返回激活版本。"""
        return (await self.get_activation_context())[1]

    async def get_version(self, version_id: int) -> VersionStub | None:
        """按编号读取版本。"""
        return self.versions.get(version_id)

    async def list_versions(self, *, limit: int = 20) -> list[VersionStub]:
        """返回有界历史。"""
        return list(reversed(self.versions.values()))[:limit]

    async def create_candidate(
        self,
        encrypted_payload: bytes,
        masked_summary: dict[str, object],
        actor_id: int,
        *,
        based_on_version_id: int | None,
        based_on_revision: int,
    ) -> VersionStub:
        """保存绑定基线的候选。"""
        version = VersionStub(
            len(self.versions) + 1,
            encrypted_payload,
            masked_summary,
            based_on_version_id,
            based_on_revision,
            created_by=actor_id,
        )
        self.versions[version.id] = version
        return version

    async def mark_test_passed(
        self,
        version_id: int,
        test_results: dict[str, object],
    ) -> None:
        """记录候选测试通过。"""
        version = self.versions[version_id]
        version.status = RuntimeConfigVersionStatus.TEST_PASSED
        version.test_results = test_results

    async def mark_test_failed(
        self,
        version_id: int,
        test_results: dict[str, object],
        *,
        failure_code: str,
    ) -> None:
        """记录失败候选。"""
        version = self.versions[version_id]
        version.status = RuntimeConfigVersionStatus.TEST_FAILED
        version.test_results = test_results
        version.failure_code = failure_code

    async def mark_activation_conflict(self, version_id: int) -> None:
        """记录候选激活冲突。"""
        version = self.versions[version_id]
        version.status = RuntimeConfigVersionStatus.ACTIVATION_CONFLICT
        version.failure_code = "revision_conflict"

    async def activate(self, version_id: int, expected_revision: int) -> StateStub:
        """仅在原始 revision 仍匹配时激活。"""
        if expected_revision != self.state.revision:
            raise RuntimeConfigConflictError("conflict")
        self.state = StateStub(
            revision=self.state.revision + 1,
            active_version_id=version_id,
            previous_version_id=self.state.active_version_id,
        )
        return self.state

    async def rollback(
        self,
        *,
        expected_revision: int,
        expected_previous_version_id: int,
    ) -> StateStub:
        """同时绑定 revision 和上一版本执行交换。"""
        if (
            expected_revision != self.state.revision
            or expected_previous_version_id != self.state.previous_version_id
        ):
            raise RuntimeConfigConflictError("conflict")
        self.state = StateStub(
            revision=self.state.revision + 1,
            active_version_id=self.state.previous_version_id,
            previous_version_id=self.state.active_version_id,
        )
        return self.state

    async def restore_failed_activation(
        self,
        expected_active_version_id: int,
        *,
        failed_candidate_version_id: int | None,
        expected_revision: int,
        restore_revision: int,
        restore_active_version_id: int | None,
        restore_previous_version_id: int | None,
        failure_code: str,
    ) -> bool:
        """仅在失败版本仍为active时恢复操作前完整指针。"""
        if (
            self.state.revision != expected_revision
            or self.state.active_version_id != expected_active_version_id
        ):
            return False
        self.state = StateStub(
            revision=restore_revision,
            active_version_id=restore_active_version_id,
            previous_version_id=restore_previous_version_id,
        )
        if failed_candidate_version_id is not None:
            version = self.versions[failed_candidate_version_id]
            version.status = RuntimeConfigVersionStatus.ACTIVATION_FAILED
            version.failure_code = failure_code
        return True

    async def prune(self, *, keep_latest: int) -> int:
        """记录保留上限。"""
        self.prune_calls.append(keep_latest)
        return 0

    async def add_audit(self, **fields: object) -> None:
        """记录最小审计字段。"""
        self.audits.append(fields)


class AuthStub:
    """模拟绑定会话版本的二次密码认证。"""

    def __init__(self) -> None:
        """初始化调用记录。"""
        self.calls: list[tuple[int, str, int]] = []

    async def reverify_at_version(
        self,
        admin_id: int,
        password: str,
        expected_session_version: int,
    ) -> None:
        """只接受固定密码和当前测试会话版本。"""
        self.calls.append((admin_id, password, expected_session_version))
        if password != "correct-password" or expected_session_version != 4:
            raise PermissionError("invalid")


class CandidateTesterStub:
    """返回可控安全结果，并可模拟测试期间并发激活。"""

    def __init__(
        self,
        *,
        success: bool = True,
        after_test: object | None = None,
        detailed: bool = False,
    ) -> None:
        """保存结果、候选和可选测试完成钩子。"""
        self.success = success
        self.snapshots: list[RuntimeConfigSnapshot] = []
        self.after_test = after_test
        self.detailed = detailed

    async def test(self, snapshot: RuntimeConfigSnapshot) -> RuntimeConfigTestResult:
        """记录候选并返回不含远端正文的结果。"""
        self.snapshots.append(snapshot)
        if callable(self.after_test):
            self.after_test()
        error_code = None if self.success else "deepseek_auth_failed"
        providers: tuple[RuntimeConfigProviderTestResult, ...] = ()
        if self.detailed:
            providers = (
                RuntimeConfigProviderTestResult(
                    "deepseek",
                    self.success,
                    error_code,
                    checks=(
                        RuntimeConfigCheckTestResult(
                            "openai",
                            self.success,
                            error_code,
                        ),
                        RuntimeConfigCheckTestResult("anthropic", True),
                    ),
                ),
                RuntimeConfigProviderTestResult(
                    "hostex",
                    True,
                    checks=(RuntimeConfigCheckTestResult("properties", True),),
                ),
                RuntimeConfigProviderTestResult(
                    "wecom",
                    True,
                    callback_verification="local_only",
                    checks=(
                        RuntimeConfigCheckTestResult("kf", True),
                        RuntimeConfigCheckTestResult("agent", True),
                        RuntimeConfigCheckTestResult(
                            "callback",
                            True,
                            verification="local_only",
                        ),
                    ),
                ),
            )
        return RuntimeConfigTestResult(
            succeeded=self.success,
            error_code=error_code,
            providers=providers,
        )


def build_service(
    repository: RepositoryStub,
    auth: AuthStub,
    tester: CandidateTesterStub,
    environment: RuntimeConfigSnapshot | None,
    *,
    activate_runtime=None,
) -> RuntimeConfigService:
    """装配使用真实加密器的配置服务。"""
    return RuntimeConfigService(
        repository=repository,
        cipher=RuntimeConfigCipher(Fernet.generate_key().decode()),
        auth=auth,
        tester=tester,
        environment_snapshot=environment,
        retention=5,
        activate_runtime=activate_runtime,
    )


async def activate(
    service: RuntimeConfigService,
    command: UpdateRuntimeConfig,
    *,
    expected_revision: int,
) -> object:
    """使用固定测试管理员身份调用敏感激活操作。"""
    return await service.create_and_test(
        command,
        actor_id=7,
        admin_id=1,
        password="correct-password",
        expected_session_version=4,
        expected_revision=expected_revision,
    )


@pytest.mark.asyncio
async def test_environment_baseline_blank_secret_and_explicit_optional_clear() -> None:
    """空白保留既有秘密，而明确清除动作只清空可选 Contact Secret。"""
    environment = build_snapshot()
    repository = RepositoryStub()
    auth = AuthStub()
    tester = CandidateTesterStub()
    service = build_service(repository, auth, tester, environment)

    result = await activate(
        service,
        UpdateRuntimeConfig(
            deepseek_api_key="   ",
            deepseek_model="deepseek-new-model",
            clear_wecom_contact_secret=True,
        ),
        expected_revision=0,
    )
    after = await service.load_active_or_environment()

    assert tester.snapshots[0].deepseek_api_key == environment.deepseek_api_key
    assert tester.snapshots[0].wecom_contact_secret is None
    assert after.deepseek_model == "deepseek-new-model"
    assert result.revision == 1
    assert auth.calls == [(1, "correct-password", 4)]
    assert UpdateRuntimeConfig(clear_wecom_contact_secret=True).changed_fields() == (
        "wecom_contact_secret",
    )


@pytest.mark.asyncio
async def test_failed_candidate_is_saved_but_never_activated() -> None:
    """测试失败版本必须带失败状态留存，同时 active 指针保持不变。"""
    repository = RepositoryStub()
    service = build_service(
        repository,
        AuthStub(),
        CandidateTesterStub(success=False, detailed=True),
        build_snapshot(),
    )

    with pytest.raises(RuntimeConfigTestError) as captured:
        await activate(
            service,
            UpdateRuntimeConfig(deepseek_api_key="new-secret-value"),
            expected_revision=0,
        )

    candidate = repository.versions[1]
    assert captured.value.error_code == "deepseek_auth_failed"
    assert candidate.status is RuntimeConfigVersionStatus.TEST_FAILED
    assert candidate.failure_code == "deepseek_auth_failed"
    assert candidate.test_results["providers"]["deepseek"]["checks"]["openai"] == {
        "succeeded": False,
        "error_code": "deepseek_auth_failed",
    }
    assert repository.state.active_version_id is None
    assert "new-secret-value" not in repr(repository.audits)


@pytest.mark.asyncio
async def test_runtime_constructor_failure_restores_database_pointer() -> None:
    """DB激活后的bundle构造失败必须恢复原指针并标记稳定失败状态。"""
    repository = RepositoryStub()

    async def reject_runtime(snapshot, version_id: int, revision: int) -> None:
        """模拟生产bundle构造失败。"""
        raise RuntimeError("secret constructor details")

    service = build_service(
        repository,
        AuthStub(),
        CandidateTesterStub(),
        build_snapshot(),
        activate_runtime=reject_runtime,
    )

    with pytest.raises(RuntimeConfigTestError) as captured:
        await activate(
            service,
            UpdateRuntimeConfig(deepseek_model="candidate-model"),
            expected_revision=0,
        )

    assert captured.value.error_code == "activation_failed"
    assert repository.state == StateStub()
    assert repository.versions[1].status is RuntimeConfigVersionStatus.ACTIVATION_FAILED
    assert repository.versions[1].failure_code == "activation_failed"
    assert "secret constructor details" not in repr(repository.audits)


@pytest.mark.asyncio
async def test_runtime_swap_failure_restores_rollback_pointer() -> None:
    """回滚bundle发布失败也必须恢复回滚前active与previous。"""
    repository = RepositoryStub()

    class ActivationStub:
        """允许前两次激活并拒绝回滚发布。"""

        def __init__(self) -> None:
            """初始化调用次数。"""
            self.calls = 0

        async def __call__(self, snapshot, version_id: int, revision: int) -> None:
            """第三次调用模拟registry.swap失败。"""
            self.calls += 1
            if self.calls == 3:
                raise RuntimeError("swap failed")

    activation = ActivationStub()
    service = build_service(
        repository,
        AuthStub(),
        CandidateTesterStub(),
        build_snapshot(),
        activate_runtime=activation,
    )
    await activate(
        service,
        UpdateRuntimeConfig(deepseek_model="model-one"),
        expected_revision=0,
    )
    await activate(
        service,
        UpdateRuntimeConfig(deepseek_model="model-two"),
        expected_revision=1,
    )

    with pytest.raises(RuntimeConfigTestError) as captured:
        await service.rollback(
            actor_id=7,
            admin_id=1,
            password="correct-password",
            expected_session_version=4,
            expected_revision=2,
            expected_previous_version_id=1,
        )

    assert captured.value.error_code == "activation_failed"
    assert repository.state == StateStub(
        revision=2,
        active_version_id=2,
        previous_version_id=1,
    )
    assert repository.versions[1].status is RuntimeConfigVersionStatus.TEST_PASSED
    assert repository.versions[1].failure_code is None


@pytest.mark.asyncio
async def test_test_period_concurrency_uses_original_revision_and_marks_conflict() -> None:
    """测试期间他人激活后，不得用新 revision 激活旧基线候选。"""
    repository = RepositoryStub()

    def concurrent_activation() -> None:
        """模拟外部测试等待期间另一请求推进状态。"""
        repository.state = StateStub(revision=1, active_version_id=99)

    service = build_service(
        repository,
        AuthStub(),
        CandidateTesterStub(after_test=concurrent_activation),
        build_snapshot(),
    )

    with pytest.raises(RuntimeConfigConflictError):
        await activate(
            service,
            UpdateRuntimeConfig(deepseek_model="stale-model"),
            expected_revision=0,
        )

    candidate = repository.versions[1]
    assert candidate.based_on_revision == 0
    assert candidate.status is RuntimeConfigVersionStatus.ACTIVATION_CONFLICT
    assert repository.state.active_version_id == 99


@pytest.mark.asyncio
async def test_missing_environment_can_create_first_complete_snapshot() -> None:
    """外部 API 环境缺失时，只要页面提交完整字段就能创建首版配置。"""
    repository = RepositoryStub()
    service = build_service(repository, AuthStub(), CandidateTesterStub(), None)
    complete = build_snapshot()
    command = UpdateRuntimeConfig.from_snapshot(complete)

    result = await activate(service, command, expected_revision=0)

    assert result.version_id == 1
    assert await service.load_active_or_environment() == complete


@pytest.mark.asyncio
async def test_rollback_binds_session_revision_and_previous_version() -> None:
    """回滚必须同时绑定认证版本、页面 revision 和页面上一版本编号。"""
    repository = RepositoryStub()
    auth = AuthStub()
    tester = CandidateTesterStub()
    service = build_service(repository, auth, tester, build_snapshot())
    await activate(
        service,
        UpdateRuntimeConfig(deepseek_model="model-one"),
        expected_revision=0,
    )
    await activate(
        service,
        UpdateRuntimeConfig(deepseek_model="model-two"),
        expected_revision=1,
    )

    result = await service.rollback(
        actor_id=7,
        admin_id=1,
        password="correct-password",
        expected_session_version=4,
        expected_revision=2,
        expected_previous_version_id=1,
    )

    assert result.version_id == 1
    assert result.revision == 3
    assert (await service.load_active_or_environment()).deepseek_model == "model-one"
    assert auth.calls == [(1, "correct-password", 4)] * 3
    assert len(tester.snapshots) == 2


@pytest.mark.asyncio
async def test_version_views_only_expose_safe_lifecycle_and_actor_label() -> None:
    """版本页面只能看到安全状态、稳定错误码和管理员显示名。"""
    repository = RepositoryStub()
    service = build_service(repository, AuthStub(), CandidateTesterStub(), build_snapshot())
    await activate(
        service,
        UpdateRuntimeConfig(deepseek_model="model-one"),
        expected_revision=0,
    )
    repository.versions[1].failure_code = "safe_failure_code"

    views = await service.list_version_views()

    assert views[0].status == "test_passed"
    assert views[0].failure_code == "safe_failure_code"
    assert views[0].created_by_label == "YuMi 管理员"
    assert not hasattr(views[0], "created_by")


@pytest.mark.asyncio
async def test_stale_page_revision_is_rejected_before_candidate_or_test() -> None:
    """页面 revision 已过期时应在创建候选和外联测试前直接拒绝。"""
    repository = RepositoryStub()
    repository.state = StateStub(revision=2, active_version_id=None)
    tester = CandidateTesterStub()
    service = build_service(repository, AuthStub(), tester, build_snapshot())

    with pytest.raises(RuntimeConfigConflictError):
        await activate(
            service,
            UpdateRuntimeConfig(deepseek_model="stale"),
            expected_revision=1,
        )

    assert repository.versions == {}
    assert tester.snapshots == []
