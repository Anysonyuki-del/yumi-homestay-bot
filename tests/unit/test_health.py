from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI, Request
from starlette.middleware.sessions import SessionMiddleware

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.main import app
from homestay_bot.routes.health import OperationalHealthService
from homestay_bot.routes.health import router as health_router


@pytest.mark.asyncio
async def test_health_endpoint_returns_ok() -> None:
    """未配置外部依赖时应明确返回降级状态，而不是伪报健康。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded"}


@pytest.mark.asyncio
async def test_health_details_require_admin_and_return_component_statuses() -> None:
    """内部组件状态只能向已登录管理员展示。"""

    class HealthServiceStub:
        """返回固定详细诊断。"""

        async def check(self) -> dict[str, str]:
            """模拟数据库异常的详细状态。"""
            return {
                "status": "degraded",
                "database": "error",
                "worker_heartbeat": "ok",
            }

    test_app = FastAPI()
    test_app.add_middleware(
        SessionMiddleware,
        secret_key="health-test-session-secret-at-least-32",
    )
    test_app.include_router(health_router)
    test_app.state.health_service = HealthServiceStub()

    @test_app.get("/test/session/{role}")
    async def seed_session(request: Request, role: EmployeeRole) -> dict[str, str]:
        """仅供测试写入签名员工会话。"""
        request.session["employee_id"] = 1
        request.session["employee_role"] = role.value
        return {"status": "seeded"}

    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        public_response = await client.get("/employee/health")
        await client.get("/test/session/staff")
        staff_response = await client.get("/employee/health")
        await client.get("/test/session/admin")
        admin_response = await client.get("/employee/health")

    assert public_response.status_code == 401
    assert staff_response.status_code == 403
    assert admin_response.status_code == 503
    assert admin_response.json() == {
        "status": "degraded",
        "database": "error",
        "worker_heartbeat": "ok",
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
        hostex_heartbeat_getter=lambda: now,
        context_heartbeat_getter=lambda: now,
        lifecycle_heartbeat_getter=lambda: now,
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
        hostex_heartbeat_getter=lambda: now,
        context_heartbeat_getter=lambda: now,
        lifecycle_heartbeat_getter=lambda: now,
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
        hostex_heartbeat_getter=lambda: now,
        context_heartbeat_getter=lambda: now,
        lifecycle_heartbeat_getter=lambda: now,
        configuration_ok=True,
        web_search_status_getter=lambda: "ok",
        contact_sync_configured=False,
    )

    result = await service.check()

    assert result["status"] == "ok"
    assert result["wecom_contact_sync"] == "not_configured"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stale_component", "field"),
    [
        ("hostex", "hostex_webhook_sync"),
        ("context", "context_maintenance"),
        ("lifecycle", "lifecycle_scheduler"),
    ],
)
async def test_operational_component_staleness_degrades_health(
    stale_component: str,
    field: str,
) -> None:
    """订单同步、上下文维护或提醒调度停滞都必须被健康页识别。"""

    async def database_probe() -> bool:
        """模拟可用数据库。"""
        return True

    now = datetime.now(UTC)
    stale = now - timedelta(minutes=31)
    heartbeats = {
        "hostex": now,
        "context": now,
        "lifecycle": now,
    }
    heartbeats[stale_component] = stale
    service = OperationalHealthService(
        database_probe=database_probe,
        heartbeat_getter=lambda: now,
        poll_heartbeat_getter=lambda: now,
        hostex_heartbeat_getter=lambda: heartbeats["hostex"],
        context_heartbeat_getter=lambda: heartbeats["context"],
        lifecycle_heartbeat_getter=lambda: heartbeats["lifecycle"],
        configuration_ok=True,
        web_search_status_getter=lambda: "ok",
        operational_max_age=timedelta(minutes=30),
    )

    result = await service.check()

    assert result["status"] == "degraded"
    assert result[field] == "stale"


@pytest.mark.asyncio
async def test_future_heartbeat_is_not_considered_healthy() -> None:
    """系统时间异常产生的未来心跳不得永久掩盖后台任务停滞。"""

    async def database_probe() -> bool:
        """模拟可用数据库。"""
        return True

    now = datetime.now(UTC)
    future = now + timedelta(minutes=5)
    service = OperationalHealthService(
        database_probe=database_probe,
        heartbeat_getter=lambda: now,
        poll_heartbeat_getter=lambda: future,
        hostex_heartbeat_getter=lambda: now,
        context_heartbeat_getter=lambda: now,
        lifecycle_heartbeat_getter=lambda: now,
        configuration_ok=True,
        web_search_status_getter=lambda: "ok",
    )

    result = await service.check()

    assert result["status"] == "degraded"
    assert result["wecom_polling"] == "stale"
