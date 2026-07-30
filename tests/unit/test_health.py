from datetime import UTC, datetime, timedelta

import httpx
import pytest

from homestay_bot.main import app
from homestay_bot.routes.health import OperationalHealthService


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
        "wecom_polling": "not_configured",
        "configuration": "incomplete",
        "web_search": "not_configured",
        "wecom_contact_sync": "not_configured",
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


@pytest.mark.asyncio
async def test_main_app_registers_customer_crm_route() -> None:
    """主应用必须暴露管理员客户管理入口。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/employee/customers")

    assert response.status_code != 404


@pytest.mark.asyncio
async def test_operational_health_degrades_when_wecom_poll_is_stale() -> None:
    """超过一分钟没有成功补拉时健康状态应明确降级。"""

    async def database_probe() -> bool:
        """模拟可用数据库。"""
        return True

    now = datetime.now(UTC)
    service = OperationalHealthService(
        database_probe=database_probe,
        heartbeat_getter=lambda: now,
        poll_heartbeat_getter=lambda: now - timedelta(seconds=61),
        configuration_ok=True,
        web_search_status_getter=lambda: "unknown",
    )

    result = await service.check()

    assert result["status"] == "degraded"
    assert result["wecom_polling"] == "stale"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("web_search_status", "overall_status"),
    [
        ("unknown", "ok"),
        ("ok", "ok"),
        ("unsupported", "degraded"),
        ("degraded", "degraded"),
    ],
)
async def test_web_search_status_controls_overall_health(
    web_search_status: str,
    overall_status: str,
) -> None:
    """未验证不影响启动，明确不支持或异常时总体健康降级。"""

    async def database_probe() -> bool:
        """模拟可用数据库。"""
        return True

    now = datetime.now(UTC)
    service = OperationalHealthService(
        database_probe=database_probe,
        heartbeat_getter=lambda: now,
        poll_heartbeat_getter=lambda: now,
        configuration_ok=True,
        web_search_status_getter=lambda: web_search_status,
    )

    result = await service.check()

    assert result["web_search"] == web_search_status
    assert result["status"] == overall_status


@pytest.mark.asyncio
async def test_optional_wecom_contact_sync_is_reported_without_degrading() -> None:
    """未配置可选客户联系 Secret 时应明确展示，但不影响核心健康。"""

    async def database_probe() -> bool:
        """模拟可用数据库。"""
        return True

    now = datetime.now(UTC)
    service = OperationalHealthService(
        database_probe=database_probe,
        heartbeat_getter=lambda: now,
        poll_heartbeat_getter=lambda: now,
        configuration_ok=True,
        web_search_status_getter=lambda: "ok",
        contact_sync_configured=False,
    )

    result = await service.check()

    assert result["status"] == "ok"
    assert result["wecom_contact_sync"] == "not_configured"
