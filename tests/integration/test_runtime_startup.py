import asyncio
import threading
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from homestay_bot.domain.models import Base
from homestay_bot.main import app


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

        def __init__(self, *, api_key: str, base_url: str) -> None:
            """保存密钥和兼容接口根地址，避免测试访问外网。"""
            chat_configuration["api_key"] = api_key
            chat_configuration["base_url"] = base_url

        async def close(self) -> None:
            """模拟关闭异步客户端。"""
            chat_configuration["closed"] = True

    class FakeAnthropic:
        """记录 DeepSeek Anthropic 搜索客户端配置。"""

        def __init__(self, *, api_key: str, base_url: str) -> None:
            """保存同一密钥与派生搜索地址。"""
            tourism_configuration["api_key"] = api_key
            tourism_configuration["base_url"] = base_url

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
    monkeypatch.setattr("homestay_bot.application.AsyncOpenAI", FakeOpenAI)
    monkeypatch.setattr("homestay_bot.application.AsyncAnthropic", FakeAnthropic)

    async def worker_loop(app, **kwargs) -> None:
        """验证生产 worker 注册了一期全部持久化任务处理器。"""
        for name in (
            "faq_draft_handler_factory",
            "hostex_event_handler_factory",
            "credential_part_handler_factory",
            "lifecycle_handler_factory",
        ):
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
        assert worker_wiring == {
            "faq_draft_handler_factory": True,
            "hostex_event_handler_factory": True,
            "credential_part_handler_factory": True,
            "lifecycle_handler_factory": True,
        }
        assert worker_recovery_wiring == [
            (None, {"wecom_process_message"}, True),
            ({"wecom_process_message"}, set(), True),
        ]
        assert app.state.private_file_service is app.state.task_page_service
        assert app.state.hostex_webhook_service is not None
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
