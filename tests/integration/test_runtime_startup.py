import asyncio

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from homestay_bot.domain.models import Base
from homestay_bot.main import app


def test_configured_application_starts_worker_and_reports_healthy(
    tmp_path, monkeypatch
) -> None:
    """完整本地配置应装配数据库与 worker，并通过分层健康检查。"""
    chat_configuration: dict[str, str | bool] = {}
    tourism_configuration: dict[str, str | bool] = {}

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
    monkeypatch.setattr("homestay_bot.application.AsyncOpenAI", FakeOpenAI)
    monkeypatch.setattr("homestay_bot.application.AsyncAnthropic", FakeAnthropic)

    with TestClient(app) as client:
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "database": "ok",
            "worker_heartbeat": "ok",
            "wecom_polling": "ok",
            "configuration": "ok",
            "web_search": "unknown",
        }
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
