import httpx
import pytest

from homestay_bot.main import app


@pytest.mark.asyncio
async def test_health_endpoint_returns_ok() -> None:
    """未配置外部依赖时应明确返回降级状态，而不是伪报健康。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "database": "not_configured",
        "worker_heartbeat": "not_configured",
        "configuration": "incomplete",
    }


@pytest.mark.asyncio
async def test_main_app_registers_wecom_callback_route() -> None:
    """主应用必须暴露企业微信回调入口。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        response = await client.get("/callbacks/wecom")

    assert response.status_code == 503
    assert response.json()["detail"] == "企业微信回调服务尚未配置"
