import httpx
import pytest

from homestay_bot.main import app


@pytest.mark.asyncio
async def test_health_endpoint_returns_ok() -> None:
    """验证应用启动后能返回明确的健康状态。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
