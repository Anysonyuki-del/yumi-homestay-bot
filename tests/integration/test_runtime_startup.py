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
from homestay_bot.services.runtime_config_cipher import RuntimeConfigCipher
from homestay_bot.services.runtime_config_service import (
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
