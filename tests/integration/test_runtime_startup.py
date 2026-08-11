import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from argon2 import PasswordHasher, Type
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from homestay_bot import application
from homestay_bot.application import application_lifespan
from homestay_bot.domain.enums import RuntimeConfigVersionStatus
from homestay_bot.domain.models import (
    AdminCredential,
    AuditLog,
    Base,
    RuntimeConfigState,
    RuntimeConfigVersion,
)
from homestay_bot.domain.runtime_config import RuntimeConfigSnapshot
from homestay_bot.main import app
from homestay_bot.routes.hostex_webhook import (
    HostexWebhookService,
    get_hostex_webhook_service,
)
from homestay_bot.routes.wecom_callback import (
    WeComCallbackService,
    get_callback_service,
)
from homestay_bot.services.runtime_clients import RuntimeClientBundle
from homestay_bot.services.runtime_config_cipher import RuntimeConfigCipher
from homestay_bot.services.runtime_config_service import (
    ActivationResult,
    RuntimeConfigTestError,
    RuntimeConfigTestResult,
    RuntimeConfigUnavailableError,
    UpdateRuntimeConfig,
)

RUNTIME_ENVIRONMENT_KEYS = (
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
    "HOSTEX_ACCESS_TOKEN",
    "HOSTEX_WEBHOOK_SECRET_TOKEN",
    "HOSTEX_RECONCILE_INTERVAL_SECONDS",
    "WECOM_CORP_ID",
    "WECOM_KF_SECRET",
    "WECOM_CALLBACK_TOKEN",
    "WECOM_ENCODING_AES_KEY",
    "WECOM_AGENT_ID",
    "WECOM_AGENT_SECRET",
    "WECOM_CONTACT_SECRET",
    "WECOM_DUTY_USERIDS",
    "WECOM_POLL_INTERVAL_SECONDS",
)


def _runtime_snapshot(**overrides: object) -> RuntimeConfigSnapshot:
    """构造不访问真实外部服务的完整运行快照。"""
    values: dict[str, object] = {
        "deepseek_api_key": "startup-deepseek-secret",
        "deepseek_base_url": "https://api.deepseek.test",
        "deepseek_model": "deepseek-v4-flash",
        "hostex_access_token": "startup-hostex-secret",
        "hostex_webhook_secret_token": "startup-webhook-secret",
        "hostex_reconcile_interval_seconds": 900.0,
        "wecom_corp_id": "startup-corp",
        "wecom_kf_secret": "startup-kf-secret",
        "wecom_callback_token": "startup-callback-token",
        "wecom_encoding_aes_key": "A" * 43,
        "wecom_agent_id": 100001,
        "wecom_agent_secret": "startup-agent-secret",
        "wecom_contact_secret": None,
        "wecom_duty_userids": "owner",
        "wecom_poll_interval_seconds": 60.0,
    }
    values.update(overrides)
    return RuntimeConfigSnapshot(**values)  # type: ignore[arg-type]


def _runtime_bundle(
    snapshot: RuntimeConfigSnapshot,
    revision: int,
    closeable: object,
    *,
    callback_service: WeComCallbackService | None = None,
    webhook_service: HostexWebhookService | None = None,
) -> RuntimeClientBundle:
    """构造生命周期协调测试使用的不联网完整bundle。"""
    return RuntimeClientBundle(
        revision=revision,
        hostex=object(),
        wecom=object(),
        contact_client=(object() if snapshot.wecom_contact_secret is not None else None),
        assistant=object(),
        faq_drafter=object(),
        tourism_searcher=object(),
        reminder_weather=object(),
        complaint_analyzer=object(),
        context_summarizer=object(),
        wecom_callback_service=(
            callback_service
            or WeComCallbackService.from_credentials(
                snapshot.wecom_callback_token,
                snapshot.wecom_encoding_aes_key,
                snapshot.wecom_corp_id,
                object(),  # type: ignore[arg-type]
            )
        ),
        hostex_webhook_service=(
            webhook_service
            or HostexWebhookService(
                snapshot.hostex_webhook_secret_token,
                object(),  # type: ignore[arg-type]
            )
        ),
        agent_id=snapshot.wecom_agent_id,
        duty_userids=("owner",),
        wecom_poll_interval_seconds=snapshot.wecom_poll_interval_seconds,
        hostex_reconcile_interval_seconds=(snapshot.hostex_reconcile_interval_seconds),
        closeables=(closeable,),
    )


async def _create_runtime_database(
    database_url: str,
    *,
    active_payload: bytes | None = None,
) -> None:
    """创建生命周期所需表，并可预置一个数据库激活版本。"""
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    if active_payload is not None:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            version = RuntimeConfigVersion(
                encrypted_payload=active_payload,
                masked_summary={"source": "database"},
                status=RuntimeConfigVersionStatus.TEST_PASSED,
                test_results={"succeeded": True},
                based_on_revision=0,
            )
            session.add(version)
            await session.flush()
            session.add(
                RuntimeConfigState(
                    id=1,
                    active_version_id=version.id,
                    revision=1,
                )
            )
            await session.commit()
    await engine.dispose()


def _set_bootstrap_environment(
    monkeypatch,
    *,
    database_url: str,
    config_encryption_key: str | None,
) -> str:
    """只设置后台修复模式必需配置，并返回管理员明文测试密码。"""
    password = "startup-admin-password"
    values = {
        "DATABASE_URL": database_url,
        "PUBLIC_BASE_URL": "https://local.example",
        "SESSION_SECRET": "s" * 32,
        "DATA_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "ADMIN_BOOTSTRAP_USERNAME": "owner",
        "ADMIN_BOOTSTRAP_PASSWORD_HASH": PasswordHasher(type=Type.ID).hash(password),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    if config_encryption_key is None:
        monkeypatch.delenv("CONFIG_ENCRYPTION_KEY", raising=False)
    else:
        monkeypatch.setenv("CONFIG_ENCRYPTION_KEY", config_encryption_key)
    return password


def _clear_runtime_environment(monkeypatch) -> None:
    """清除全部可网页编辑字段，证明启动不依赖空凭据占位。"""
    for key in RUNTIME_ENVIRONMENT_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.mark.asyncio
async def test_bootstrap_only_starts_secure_repair_console_without_external_clients(
    tmp_path,
    monkeypatch,
) -> None:
    """外部配置全缺失时仍装配唯一管理员后台，且绝不构造空凭据客户端。"""
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'repair-only.db'}"
    await _create_runtime_database(database_url)
    _set_bootstrap_environment(
        monkeypatch,
        database_url=database_url,
        config_encryption_key=Fernet.generate_key().decode(),
    )
    _clear_runtime_environment(monkeypatch)

    def reject_external_client(*args, **kwargs):
        """任何外部客户端构造都表示修复模式越界。"""
        raise AssertionError("repair-only 启动不得构造外部客户端")

    for name in ("HostexClient", "WeComApiClient"):
        monkeypatch.setattr(application, name, reject_external_client)
    for name in ("AsyncOpenAI", "AsyncAnthropic"):
        monkeypatch.setattr(
            "homestay_bot.services.runtime_clients." + name,
            reject_external_client,
        )

    test_app = FastAPI()
    async with application_lifespan(test_app):
        assert test_app.state.admin_auth_available is True
        assert test_app.state.admin_auth_service is not None
        assert test_app.state.admin_csrf_service is not None
        assert test_app.state.admin_dashboard_service is not None
        assert test_app.state.runtime_config_service is not None
        assert (await test_app.state.runtime_config_service.page_data()).source == "unconfigured"
        health = await test_app.state.health_service.check()
        assert health["database"] == "ok"
        assert health["configuration"] == "incomplete"
        assert health["status"] == "degraded"
        assert not hasattr(test_app.state, "wecom_callback_service")
        assert not hasattr(test_app.state, "hostex_webhook_service")


@pytest.mark.asyncio
async def test_repair_only_first_activation_starts_runtime_without_restart(
    tmp_path,
    monkeypatch,
) -> None:
    """首次网页激活应在当前进程装配registry、路由依赖和全部后台循环。"""
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'first-activation.db'}"
    await _create_runtime_database(database_url)
    password = _set_bootstrap_environment(
        monkeypatch,
        database_url=database_url,
        config_encryption_key=Fernet.generate_key().decode(),
    )
    _clear_runtime_environment(monkeypatch)
    started_tasks = 0
    closed = 0

    class PassingTester:
        """避免首次激活测试访问真实供应商。"""

        async def test(self, candidate: RuntimeConfigSnapshot) -> RuntimeConfigTestResult:
            """验证候选完整后返回安全成功结果。"""
            candidate.validate()
            return RuntimeConfigTestResult(succeeded=True)

    class CloseProbe:
        """记录首次运行bundle在shutdown被释放。"""

        async def aclose(self) -> None:
            """记录一次资源关闭。"""
            nonlocal closed
            closed += 1

    callback_service = WeComCallbackService.from_credentials(
        "callback-token",
        "A" * 43,
        "corp-id",
        object(),  # type: ignore[arg-type]
    )
    webhook_service = HostexWebhookService(
        "webhook-token",
        object(),  # type: ignore[arg-type]
    )

    async def build_bundle(
        snapshot: RuntimeConfigSnapshot,
        *,
        revision: int,
        **kwargs,
    ) -> RuntimeClientBundle:
        """构造不联网但字段完整的首次运行bundle。"""
        snapshot.validate()
        return _runtime_bundle(
            snapshot,
            revision,
            CloseProbe(),
            callback_service=callback_service,
            webhook_service=webhook_service,
        )

    async def blocked_loop(*args, **kwargs) -> None:
        """记录后台循环启动，并等待生命周期取消。"""
        nonlocal started_tasks
        started_tasks += 1
        await asyncio.Event().wait()

    monkeypatch.setattr(application, "RuntimeConfigTester", PassingTester)
    monkeypatch.setattr(application, "build_runtime_client_bundle", build_bundle)
    for loop_name in (
        "_run_worker_loop",
        "_run_wecom_poll_loop",
        "_run_faq_maintenance_loop",
        "_run_retention_loop",
        "_run_context_maintenance_loop",
        "_run_hostex_reconcile_loop",
    ):
        monkeypatch.setattr(application, loop_name, blocked_loop)

    test_app = FastAPI()
    async with application_lifespan(test_app):
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            credential = await session.scalar(select(AdminCredential))
            assert credential is not None
            actor_id = int(credential.employee_id)
            admin_id = int(credential.id)
            session_version = int(credential.session_version)
        await engine.dispose()

        snapshot = _runtime_snapshot(wecom_contact_secret="contact-secret")
        outcomes = await asyncio.gather(
            *(
                test_app.state.runtime_config_service.create_and_test(
                    UpdateRuntimeConfig.from_snapshot(snapshot),
                    actor_id=actor_id,
                    admin_id=admin_id,
                    password=password,
                    expected_session_version=session_version,
                    expected_revision=0,
                )
                for _ in range(2)
            ),
            return_exceptions=True,
        )
        successful = [item for item in outcomes if isinstance(item, ActivationResult)]
        failures = [item for item in outcomes if isinstance(item, BaseException)]
        assert len(successful) == 1
        assert len(failures) == 1
        result = successful[0]
        for _ in range(10):
            if started_tasks == 7:
                break
            await asyncio.sleep(0)

        registry = test_app.state.runtime_client_registry
        assert (await registry.status()).revision == result.revision
        assert started_tasks == 7
        health = await test_app.state.health_service.check()
        assert health["configuration"] == "ok"
        assert health["wecom_contact_sync"] == "ok"

        request = Request({"type": "http", "app": test_app})
        callback_dependency = get_callback_service(request)
        webhook_dependency = get_hostex_webhook_service(request)
        assert await anext(callback_dependency) is callback_service
        assert await anext(webhook_dependency) is webhook_service
        await callback_dependency.aclose()
        await webhook_dependency.aclose()

    assert closed == 1
    assert not hasattr(test_app.state, "runtime_client_registry")


@pytest.mark.asyncio
async def test_first_runtime_start_failure_compensates_database_and_cleans_partial_tasks(
    tmp_path,
    monkeypatch,
) -> None:
    """首次装配中途失败必须恢复DB指针、关闭bundle且不发布半成品state。"""
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'first-start-failure.db'}"
    await _create_runtime_database(database_url)
    password = _set_bootstrap_environment(
        monkeypatch,
        database_url=database_url,
        config_encryption_key=Fernet.generate_key().decode(),
    )
    _clear_runtime_environment(monkeypatch)
    close_calls = 0
    create_calls = 0

    class PassingTester:
        """让候选进入激活后的运行装配阶段。"""

        async def test(self, candidate: RuntimeConfigSnapshot) -> RuntimeConfigTestResult:
            """验证候选并返回成功。"""
            candidate.validate()
            return RuntimeConfigTestResult(succeeded=True)

    class CloseProbe:
        """记录补偿路径释放首次bundle。"""

        async def aclose(self) -> None:
            """记录一次关闭。"""
            nonlocal close_calls
            close_calls += 1

    async def build_bundle(
        snapshot: RuntimeConfigSnapshot,
        *,
        revision: int,
        **kwargs,
    ) -> RuntimeClientBundle:
        """构造待首次装配的候选bundle。"""
        return _runtime_bundle(snapshot, revision, CloseProbe())

    async def blocked_loop(*args, **kwargs) -> None:
        """若已创建则等待协调器取消。"""
        await asyncio.Event().wait()

    real_create_task = asyncio.create_task

    def fail_third_create_task(coro):
        """在已有两个task后模拟启动器中途失败。"""
        nonlocal create_calls
        create_calls += 1
        if create_calls == 3:
            coro.close()
            raise RuntimeError("task start failed")
        return real_create_task(coro)

    monkeypatch.setattr(application, "RuntimeConfigTester", PassingTester)
    monkeypatch.setattr(application, "build_runtime_client_bundle", build_bundle)
    for loop_name in (
        "_run_worker_loop",
        "_run_wecom_poll_loop",
        "_run_faq_maintenance_loop",
        "_run_retention_loop",
        "_run_context_maintenance_loop",
        "_run_hostex_reconcile_loop",
    ):
        monkeypatch.setattr(application, loop_name, blocked_loop)
    monkeypatch.setattr(application, "_create_runtime_task", fail_third_create_task)

    test_app = FastAPI()
    async with application_lifespan(test_app):
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            credential = await session.scalar(select(AdminCredential))
            assert credential is not None
            actor_id = int(credential.employee_id)
            admin_id = int(credential.id)
            session_version = int(credential.session_version)
        repair_health_service = test_app.state.health_service
        repair_customer_service = test_app.state.customer_admin_service

        with pytest.raises(RuntimeConfigTestError) as captured:
            await test_app.state.runtime_config_service.create_and_test(
                UpdateRuntimeConfig.from_snapshot(_runtime_snapshot()),
                actor_id=actor_id,
                admin_id=admin_id,
                password=password,
                expected_session_version=session_version,
                expected_revision=0,
            )
        assert captured.value.error_code == "activation_failed"

        async with factory() as session:
            state = await session.get(RuntimeConfigState, 1)
            candidate = await session.scalar(select(RuntimeConfigVersion))
        await engine.dispose()
        assert state is not None
        assert state.revision == 0
        assert state.active_version_id is None
        assert candidate is not None
        assert candidate.status is RuntimeConfigVersionStatus.ACTIVATION_FAILED
        assert close_calls == 1
        assert not hasattr(test_app.state, "runtime_client_registry")
        assert not hasattr(test_app.state, "approval_page_service")
        assert test_app.state.health_service is repair_health_service
        assert test_app.state.customer_admin_service is repair_customer_service
        assert (await test_app.state.health_service.check())["configuration"] == (
            "incomplete"
        )


@pytest.mark.asyncio
async def test_shutdown_waits_for_first_activation_and_prevents_late_publish(
    tmp_path,
    monkeypatch,
) -> None:
    """shutdown与首次构造竞态时禁止晚发布，并等待候选清理和DB补偿。"""
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'shutdown-activation.db'}"
    await _create_runtime_database(database_url)
    password = _set_bootstrap_environment(
        monkeypatch,
        database_url=database_url,
        config_encryption_key=Fernet.generate_key().decode(),
    )
    _clear_runtime_environment(monkeypatch)
    build_started = asyncio.Event()
    build_proceed = asyncio.Event()
    close_calls = 0
    started_tasks = 0

    class PassingTester:
        """让候选进入首次运行构造。"""

        async def test(self, candidate: RuntimeConfigSnapshot) -> RuntimeConfigTestResult:
            """验证候选后返回成功。"""
            candidate.validate()
            return RuntimeConfigTestResult(succeeded=True)

    class CloseProbe:
        """记录被shutdown拒绝的候选是否关闭。"""

        async def aclose(self) -> None:
            """记录候选资源关闭。"""
            nonlocal close_calls
            close_calls += 1

    async def build_bundle(
        snapshot: RuntimeConfigSnapshot,
        *,
        revision: int,
        **kwargs,
    ) -> RuntimeClientBundle:
        """阻塞首次候选构造，稳定制造shutdown竞态。"""
        build_started.set()
        await build_proceed.wait()
        return _runtime_bundle(snapshot, revision, CloseProbe())

    async def finished_loop(*args, **kwargs) -> None:
        """若错误晚发布则记录后台task数量后立即返回，避免RED泄漏。"""
        nonlocal started_tasks
        started_tasks += 1

    monkeypatch.setattr(application, "RuntimeConfigTester", PassingTester)
    monkeypatch.setattr(application, "build_runtime_client_bundle", build_bundle)
    for loop_name in (
        "_run_worker_loop",
        "_run_wecom_poll_loop",
        "_run_faq_maintenance_loop",
        "_run_retention_loop",
        "_run_context_maintenance_loop",
        "_run_hostex_reconcile_loop",
    ):
        monkeypatch.setattr(application, loop_name, finished_loop)

    test_app = FastAPI()
    lifespan = application_lifespan(test_app)
    await lifespan.__aenter__()
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        credential = await session.scalar(select(AdminCredential))
        assert credential is not None
        actor_id = int(credential.employee_id)
        admin_id = int(credential.id)
        session_version = int(credential.session_version)

    activation = asyncio.create_task(
        test_app.state.runtime_config_service.create_and_test(
            UpdateRuntimeConfig.from_snapshot(_runtime_snapshot()),
            actor_id=actor_id,
            admin_id=admin_id,
            password=password,
            expected_session_version=session_version,
            expected_revision=0,
        )
    )
    await build_started.wait()
    shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
    await asyncio.sleep(0)
    shutdown_finished_before_release = shutdown.done()
    build_proceed.set()
    activation_result = await asyncio.gather(activation, return_exceptions=True)
    await shutdown

    async with factory() as session:
        state = await session.get(RuntimeConfigState, 1)
        candidate = await session.scalar(select(RuntimeConfigVersion))
    await engine.dispose()
    assert shutdown_finished_before_release is False
    assert len(activation_result) == 1
    assert isinstance(activation_result[0], RuntimeConfigTestError)
    assert state is not None
    assert (state.revision, state.active_version_id) == (0, None)
    assert candidate is not None
    assert candidate.status is RuntimeConfigVersionStatus.ACTIVATION_FAILED
    assert close_calls == 1
    assert started_tasks == 0
    assert not hasattr(test_app.state, "runtime_client_registry")
    assert not hasattr(test_app.state, "approval_page_service")


@pytest.mark.asyncio
async def test_missing_config_key_keeps_settings_readable_but_disables_writes(
    tmp_path,
    monkeypatch,
) -> None:
    """配置主密钥缺失时后台仍可读环境掩码，但敏感写入必须显式拒绝。"""
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'read-only.db'}"
    await _create_runtime_database(database_url)
    _set_bootstrap_environment(
        monkeypatch,
        database_url=database_url,
        config_encryption_key=None,
    )
    snapshot = _runtime_snapshot()
    for name, value in snapshot.to_dict().items():
        if name == "schema_version" or value is None:
            continue
        monkeypatch.setenv(name.upper(), str(value))

    test_app = FastAPI()
    async with application_lifespan(test_app):
        page = await test_app.state.runtime_config_service.page_data()
        assert page.source == "environment"
        assert test_app.state.runtime_config_writes_available is False
        with pytest.raises(RuntimeConfigUnavailableError):
            await test_app.state.runtime_config_service.create_and_test(
                UpdateRuntimeConfig(deepseek_model="changed"),
                actor_id=1,
                admin_id=1,
                password="startup-admin-password",
                expected_session_version=1,
                expected_revision=0,
            )
        assert (await test_app.state.health_service.check())["status"] == "degraded"


@pytest.mark.asyncio
async def test_valid_database_active_snapshot_is_runtime_source_without_external_env(
    tmp_path,
    monkeypatch,
) -> None:
    """环境外部字段全缺失时，数据库有效 active 密文仍应成为完整运行来源。"""
    config_key = Fernet.generate_key().decode()
    snapshot = _runtime_snapshot(deepseek_model="database-model")
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'database-source.db'}"
    await _create_runtime_database(
        database_url,
        active_payload=RuntimeConfigCipher(config_key).encrypt(snapshot),
    )
    _set_bootstrap_environment(
        monkeypatch,
        database_url=database_url,
        config_encryption_key=config_key,
    )
    _clear_runtime_environment(monkeypatch)
    bootstrap = application.BootstrapSettings(_env_file=None)  # type: ignore[call-arg]
    engine = application.create_engine(bootstrap.database_url)
    factory = application.create_session_factory(engine)

    resolved, source, degraded = await application._resolve_runtime_snapshot(
        factory,
        cipher=RuntimeConfigCipher(config_key),
        environment_snapshot=None,
    )

    assert resolved == snapshot
    assert source == "database"
    assert degraded is False
    await engine.dispose()


@pytest.mark.asyncio
async def test_corrupt_active_uses_environment_repair_and_commits_activation_with_audit(
    tmp_path,
    monkeypatch,
) -> None:
    """损坏 active 应可用环境基线修复，且测试释放事务、激活与审计原子落库。"""
    config_key = Fernet.generate_key().decode()
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'corrupt-active.db'}"
    await _create_runtime_database(database_url, active_payload=b"corrupt-ciphertext")
    password = _set_bootstrap_environment(
        monkeypatch,
        database_url=database_url,
        config_encryption_key=config_key,
    )
    snapshot = _runtime_snapshot()
    engine = application.create_engine(database_url)
    factory = application.create_session_factory(engine)
    assert await application._bootstrap_admin_auth(
        factory,
        username="owner",
        password_hash=PasswordHasher(type=Type.ID).hash(password),
    )

    class TransactionProbeTester:
        """用第二会话写事务证明候选测试期间首会话没有持锁。"""

        async def test(self, candidate: RuntimeConfigSnapshot) -> RuntimeConfigTestResult:
            """执行无业务副作用的状态行自更新，并提交独立探测事务。"""
            candidate.validate()
            async with factory() as probe_session:
                await probe_session.execute(
                    text("UPDATE runtime_config_state SET revision = revision WHERE id = 1")
                )
                await probe_session.commit()
            return RuntimeConfigTestResult(succeeded=True)

    tester = TransactionProbeTester()
    executor = ThreadPoolExecutor(max_workers=2)
    semaphore = asyncio.Semaphore(2)
    try:
        dummy_hash = await asyncio.get_running_loop().run_in_executor(
            executor,
            application.ADMIN_PASSWORD_HASHER.hash,
            "runtime-config-dummy",
        )
        service = application.SessionRuntimeConfigService(
            factory,
            cipher=RuntimeConfigCipher(config_key),
            environment_snapshot=snapshot,
            password_hasher=application.ADMIN_PASSWORD_HASHER,
            dummy_hash=dummy_hash,
            argon2_semaphore=semaphore,
            argon2_executor=executor,
            writable=True,
            tester=tester,
        )
        page = await service.page_data()
        assert page.source == "environment"
        async with factory() as session:
            credential = await session.scalar(select(AdminCredential))
            assert credential is not None
            admin_id = credential.id
            actor_id = credential.employee_id
            session_version = credential.session_version

        result = await service.create_and_test(
            UpdateRuntimeConfig(deepseek_model="repaired-model"),
            actor_id=actor_id,
            admin_id=admin_id,
            password=password,
            expected_session_version=session_version,
            expected_revision=1,
        )

        assert result.revision == 2
        assert (await service.page_data()).source == "database"
        async with factory() as session:
            state = await session.get(RuntimeConfigState, 1)
            audits = list(
                await session.scalars(
                    select(AuditLog).where(AuditLog.action == "runtime_config.activate")
                )
            )
        assert state is not None
        assert state.active_version_id == result.version_id
        assert state.previous_version_id is None
        assert len(audits) == 1
    finally:
        await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)
        await engine.dispose()


@pytest.mark.asyncio
async def test_corrupt_startup_health_recovers_after_current_process_swap(
    tmp_path,
    monkeypatch,
) -> None:
    """损坏active回退环境后保持降级，网页修复swap且revision一致才恢复健康。"""
    config_key = Fernet.generate_key().decode()
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'corrupt-health.db'}"
    await _create_runtime_database(database_url, active_payload=b"corrupt-ciphertext")
    password = _set_bootstrap_environment(
        monkeypatch,
        database_url=database_url,
        config_encryption_key=config_key,
    )
    environment = _runtime_snapshot()
    for name, value in environment.to_dict().items():
        if name != "schema_version" and value is not None:
            monkeypatch.setenv(name.upper(), str(value))

    class PassingTester:
        """让网页修复进入DB激活和当前进程swap阶段。"""

        async def test(self, candidate: RuntimeConfigSnapshot) -> RuntimeConfigTestResult:
            """验证修复候选完整后返回成功。"""
            candidate.validate()
            return RuntimeConfigTestResult(succeeded=True)

    class CloseProbe:
        """提供可正常关闭的不联网资源。"""

        async def aclose(self) -> None:
            """模拟成功关闭。"""

    async def build_bundle(
        snapshot: RuntimeConfigSnapshot,
        *,
        revision: int,
        **kwargs,
    ) -> RuntimeClientBundle:
        """按候选动态字段构造健康元数据bundle。"""
        return _runtime_bundle(snapshot, revision, CloseProbe())

    async def blocked_loop(*args, **kwargs) -> None:
        """保持后台循环存活直到生命周期取消。"""
        await asyncio.Event().wait()

    monkeypatch.setattr(application, "RuntimeConfigTester", PassingTester)
    monkeypatch.setattr(application, "build_runtime_client_bundle", build_bundle)
    for loop_name in (
        "_run_worker_loop",
        "_run_wecom_poll_loop",
        "_run_faq_maintenance_loop",
        "_run_retention_loop",
        "_run_context_maintenance_loop",
        "_run_hostex_reconcile_loop",
    ):
        monkeypatch.setattr(application, loop_name, blocked_loop)

    test_app = FastAPI()
    async with application_lifespan(test_app):
        initial_status = await test_app.state.runtime_client_registry.status()
        initial_health = await test_app.state.health_service.check()
        assert initial_status.configuration_healthy is False
        assert initial_health["configuration"] == "incomplete"

        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            credential = await session.scalar(select(AdminCredential))
            assert credential is not None
            actor_id = int(credential.employee_id)
            admin_id = int(credential.id)
            session_version = int(credential.session_version)
        await engine.dispose()

        result = await test_app.state.runtime_config_service.create_and_test(
            UpdateRuntimeConfig(
                deepseek_model="repaired-model",
                wecom_contact_secret="contact-secret",
                wecom_poll_interval_seconds=5.0,
                hostex_reconcile_interval_seconds=60.0,
            ),
            actor_id=actor_id,
            admin_id=admin_id,
            password=password,
            expected_session_version=session_version,
            expected_revision=1,
        )

        current = await test_app.state.runtime_client_registry.status()
        health = await test_app.state.health_service.check()
        assert current.revision == result.revision
        assert current.configuration_healthy is True
        assert current.contact_configured is True
        assert current.wecom_poll_interval_seconds == 5.0
        assert current.hostex_reconcile_interval_seconds == 60.0
        assert health["configuration"] == "ok"
        assert health["wecom_contact_sync"] == "ok"


@pytest.mark.asyncio
async def test_cancel_after_runtime_publish_keeps_database_and_registry_current(
    tmp_path,
    monkeypatch,
) -> None:
    """旧资源退役阶段取消请求不得补偿已发布的新revision或关闭当前bundle。"""
    config_key = Fernet.generate_key().decode()
    initial_snapshot = _runtime_snapshot(deepseek_model="initial-model")
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'published-cancel.db'}"
    await _create_runtime_database(
        database_url,
        active_payload=RuntimeConfigCipher(config_key).encrypt(initial_snapshot),
    )
    password = _set_bootstrap_environment(
        monkeypatch,
        database_url=database_url,
        config_encryption_key=config_key,
    )
    _clear_runtime_environment(monkeypatch)
    old_close_started = asyncio.Event()
    old_close_proceed = asyncio.Event()
    old_close_completed = asyncio.Event()
    successful_audit_started = asyncio.Event()
    candidate_close_calls = 0
    build_calls = 0

    class PassingTester:
        """让候选配置进入真实DB激活与runtime发布。"""

        async def test(self, candidate: RuntimeConfigSnapshot) -> RuntimeConfigTestResult:
            """校验候选完整并返回成功。"""
            candidate.validate()
            return RuntimeConfigTestResult(succeeded=True)

    class OldCloseProbe:
        """阻塞旧bundle关闭，暴露发布与退役的时序。"""

        async def aclose(self) -> None:
            """等待测试放行后完成旧资源关闭。"""
            old_close_started.set()
            await old_close_proceed.wait()
            old_close_completed.set()

    class CandidateCloseProbe:
        """记录新bundle是否被错误当作失败候选关闭。"""

        async def aclose(self) -> None:
            """记录当前bundle关闭次数。"""
            nonlocal candidate_close_calls
            candidate_close_calls += 1

    async def build_bundle(
        snapshot: RuntimeConfigSnapshot,
        *,
        revision: int,
        **kwargs,
    ) -> RuntimeClientBundle:
        """首次构造阻塞退役的旧bundle，后续构造可观测候选。"""
        nonlocal build_calls
        build_calls += 1
        closeable: object = OldCloseProbe() if build_calls == 1 else CandidateCloseProbe()
        return _runtime_bundle(snapshot, revision, closeable)

    async def blocked_loop(*args, **kwargs) -> None:
        """保持后台循环存活直到生命周期取消。"""
        await asyncio.Event().wait()

    original_add_audit = application.SQLAlchemyRuntimeConfigRepository.add_audit

    async def gated_add_audit(self, **kwargs) -> None:
        """发布成功后暂停外层请求，保证取消发生在可观测窗口。"""
        if kwargs.get("action") == "runtime_config.activate" and kwargs.get(
            "result"
        ) == "ok":
            successful_audit_started.set()
            await asyncio.Event().wait()
        await original_add_audit(self, **kwargs)

    monkeypatch.setattr(application, "RuntimeConfigTester", PassingTester)
    monkeypatch.setattr(application, "build_runtime_client_bundle", build_bundle)
    monkeypatch.setattr(
        application.SQLAlchemyRuntimeConfigRepository,
        "add_audit",
        gated_add_audit,
    )
    for loop_name in (
        "_run_worker_loop",
        "_run_wecom_poll_loop",
        "_run_faq_maintenance_loop",
        "_run_retention_loop",
        "_run_context_maintenance_loop",
        "_run_hostex_reconcile_loop",
    ):
        monkeypatch.setattr(application, loop_name, blocked_loop)

    test_app = FastAPI()
    async with application_lifespan(test_app):
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            credential = await session.scalar(select(AdminCredential))
            assert credential is not None
            actor_id = int(credential.employee_id)
            admin_id = int(credential.id)
            session_version = int(credential.session_version)

        activation = asyncio.create_task(
            test_app.state.runtime_config_service.create_and_test(
                UpdateRuntimeConfig(deepseek_model="published-model"),
                actor_id=actor_id,
                admin_id=admin_id,
                password=password,
                expected_session_version=session_version,
                expected_revision=1,
            )
        )
        await old_close_started.wait()
        # 新实现会到达成功审计门闩；旧实现仍阻塞在swap。
        await asyncio.sleep(0)
        activation.cancel()
        old_close_proceed.set()
        with pytest.raises(asyncio.CancelledError):
            await activation
        await asyncio.wait_for(old_close_completed.wait(), timeout=0.1)

        async with factory() as session:
            state = await session.get(RuntimeConfigState, 1)
            active = (
                await session.get(RuntimeConfigVersion, state.active_version_id)
                if state is not None and state.active_version_id is not None
                else None
            )
        assert state is not None
        assert state.revision == 2
        assert active is not None
        assert active.status is RuntimeConfigVersionStatus.TEST_PASSED
        assert (await test_app.state.runtime_client_registry.status()).revision == 2
        assert candidate_close_calls == 0
        assert successful_audit_started.is_set()
        await engine.dispose()


@pytest.mark.parametrize(
    ("admin_username", "admin_password_hash"),
    [(None, None), ("admin", "invalid-plaintext")],
)
def test_unavailable_admin_bootstrap_keeps_workers_running_and_reports_degraded(
    tmp_path,
    monkeypatch,
    admin_username: str | None,
    admin_password_hash: str | None,
) -> None:
    """后台引导缺失或失败时客服主链仍启动，但健康状态应明确降级。"""
    chat_configuration: dict[str, str | bool] = {}
    tourism_configuration: dict[str, str | bool] = {}
    started = {
        "worker": threading.Event(),
        "wecom_poll": threading.Event(),
        "context": threading.Event(),
        "hostex": threading.Event(),
    }
    worker_wiring: dict[str, bool] = {}
    worker_recovery_wiring: list[tuple[set[str] | None, set[str], bool]] = []

    class FakeOpenAI:
        """记录生命周期传给 OpenAI 客户端的连接配置。"""

        def __init__(
            self,
            *,
            api_key: str,
            base_url: str,
            http_client,
            max_retries: int,
        ) -> None:
            """保存密钥和兼容接口根地址，避免测试访问外网。"""
            chat_configuration["api_key"] = api_key
            chat_configuration["base_url"] = base_url
            assert http_client is not None
            assert max_retries == 0

        async def close(self) -> None:
            """模拟关闭异步客户端。"""
            chat_configuration["closed"] = True

    class FakeAnthropic:
        """记录 DeepSeek Anthropic 搜索客户端配置。"""

        def __init__(
            self,
            *,
            api_key: str,
            base_url: str,
            http_client,
            max_retries: int,
        ) -> None:
            """保存同一密钥与派生搜索地址。"""
            tourism_configuration["api_key"] = api_key
            tourism_configuration["base_url"] = base_url
            assert http_client is not None
            assert max_retries == 0

        async def close(self) -> None:
            """记录客户端已关闭。"""
            tourism_configuration["closed"] = True

    database_path = tmp_path / "runtime.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"

    async def create_schema() -> None:
        """为生命周期测试创建与迁移一致的本地表结构。"""
        engine = create_async_engine(database_url)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(create_schema())
    environment = {
        "DATABASE_URL": database_url,
        "PUBLIC_BASE_URL": "https://local.example",
        "DEEPSEEK_API_KEY": "test-deepseek-key",
        "DEEPSEEK_BASE_URL": "https://api.deepseek.test",
        "DEEPSEEK_MODEL": "deepseek-v4-flash",
        "HOSTEX_ACCESS_TOKEN": "test-hostex-token",
        "HOSTEX_WEBHOOK_SECRET_TOKEN": "test-webhook-secret",
        "WECOM_CORP_ID": "corp-id",
        "WECOM_KF_SECRET": "kf-secret",
        "WECOM_CALLBACK_TOKEN": "callback-token",
        "WECOM_ENCODING_AES_KEY": "A" * 43,
        "WECOM_AGENT_ID": "100001",
        "WECOM_AGENT_SECRET": "agent-secret",
        "WECOM_DUTY_USERIDS": "staff-1",
        "SESSION_SECRET": "local-test-session-secret-at-least-32",
        "DATA_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("ADMIN_BOOTSTRAP_USERNAME", raising=False)
    monkeypatch.delenv("ADMIN_BOOTSTRAP_PASSWORD_HASH", raising=False)
    if admin_username is not None and admin_password_hash is not None:
        monkeypatch.setenv("ADMIN_BOOTSTRAP_USERNAME", admin_username)
        monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD_HASH", admin_password_hash)
    monkeypatch.setattr("homestay_bot.services.runtime_clients.AsyncOpenAI", FakeOpenAI)
    monkeypatch.setattr("homestay_bot.services.runtime_clients.AsyncAnthropic", FakeAnthropic)

    async def worker_loop(app, **kwargs) -> None:
        """验证生产 worker 注册了一期全部持久化任务处理器。"""
        for name in ("runtime_handler_factory",):
            worker_wiring[name] = callable(kwargs.get(name))
        worker_recovery_wiring.append(
            (
                kwargs.get("included_job_types"),
                kwargs.get("excluded_job_types") or set(),
                bool(kwargs.get("recover_stale")),
            )
        )
        started["worker"].set()
        await asyncio.Event().wait()

    async def wecom_poll_loop(app, **kwargs) -> None:
        """模拟一次真实补拉成功并刷新生产健康状态。"""
        app.state.wecom_poll_last_success = datetime.now(UTC)
        started["wecom_poll"].set()
        await asyncio.Event().wait()

    async def context_loop(*, heartbeat, **kwargs) -> None:
        """通过应用注入的回调模拟一次上下文维护成功。"""
        heartbeat(datetime.now(UTC))
        started["context"].set()
        await asyncio.Event().wait()

    async def hostex_loop(
        *,
        sync_heartbeat,
        lifecycle_heartbeat,
        **kwargs,
    ) -> None:
        """通过应用注入的回调模拟一次对账和提醒调度成功。"""
        now = datetime.now(UTC)
        sync_heartbeat(now)
        lifecycle_heartbeat(now)
        started["hostex"].set()
        await asyncio.Event().wait()

    monkeypatch.setattr("homestay_bot.application._run_worker_loop", worker_loop)
    monkeypatch.setattr(
        "homestay_bot.application._run_wecom_poll_loop",
        wecom_poll_loop,
    )
    monkeypatch.setattr(
        "homestay_bot.application._run_context_maintenance_loop",
        context_loop,
    )
    monkeypatch.setattr(
        "homestay_bot.application._run_hostex_reconcile_loop",
        hostex_loop,
    )

    with TestClient(app) as client:
        assert all(event.wait(timeout=1) for event in started.values())
        response = client.get("/health")

        assert response.status_code == 503
        assert response.json() == {"status": "degraded"}
        assert worker_wiring == {"runtime_handler_factory": True}
        assert worker_recovery_wiring == [
            (None, {"wecom_process_message"}, True),
            ({"wecom_process_message"}, set(), True),
        ]
        assert app.state.private_file_service is app.state.task_page_service
        assert app.state.runtime_client_registry is not None
        assert app.state.admin_auth_available is False
        assert not hasattr(app.state, "admin_auth_service")
        assert not hasattr(app.state, "employee_access_verifier")
        assert chat_configuration == {
            "api_key": "test-deepseek-key",
            "base_url": "https://api.deepseek.test",
        }
        assert tourism_configuration == {
            "api_key": "test-deepseek-key",
            "base_url": "https://api.deepseek.test/anthropic",
        }
    assert chat_configuration["closed"] is True
    assert tourism_configuration["closed"] is True
    assert not hasattr(app.state, "admin_auth_service")
