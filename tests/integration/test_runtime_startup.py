import asyncio

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from homestay_bot.domain.models import Base
from homestay_bot.main import app


def test_configured_application_starts_worker_and_reports_healthy(
    tmp_path, monkeypatch
) -> None:
    """完整本地配置应装配数据库与 worker，并通过分层健康检查。"""
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
        "OPENAI_API_KEY": "test-openai-key",
        "OPENAI_MODEL": "gpt-5.6-terra",
        "HOSTEX_ACCESS_TOKEN": "test-hostex-token",
        "WECOM_CORP_ID": "corp-id",
        "WECOM_KF_SECRET": "kf-secret",
        "WECOM_CALLBACK_TOKEN": "callback-token",
        "WECOM_ENCODING_AES_KEY": "A" * 43,
        "WECOM_AGENT_ID": "100001",
        "WECOM_AGENT_SECRET": "agent-secret",
        "WECOM_DUTY_USERIDS": "staff-1",
        "SESSION_SECRET": "local-test-session-secret-at-least-32",
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)

    with TestClient(app) as client:
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "database": "ok",
            "worker_heartbeat": "ok",
            "configuration": "ok",
        }
