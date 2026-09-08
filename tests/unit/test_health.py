from datetime import UTC, datetime, timedelta

import httpx
import pytest
from admin_auth_helpers import configure_admin_auth
from fastapi import FastAPI, Request
from starlette.middleware.sessions import SessionMiddleware

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.main import app
from homestay_bot.routes.admin import _BENIGN_CHECK_STATES
from homestay_bot.routes.health import OperationalHealthService
from homestay_bot.routes.health import router as health_router
from homestay_bot.services.runtime_clients import RuntimeClientStatus


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
        request.session["employee_id"] = (
            1 if role is EmployeeRole.ADMIN else 2
        )
        request.session["employee_role"] = role.value
        request.session["admin_id"] = 1
        request.session["admin_session_version"] = 1
        request.session["last_activity_at"] = datetime.now(UTC).isoformat()
        return {"status": "seeded"}

    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        public_response = await client.get("/employee/health")
        configure_admin_auth(test_app, EmployeeRole.STAFF)
        await client.get("/test/session/staff")
        staff_response = await client.get("/employee/health")
        configure_admin_auth(test_app, EmployeeRole.ADMIN)
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
        ("hostex", "hostex_reconcile"),
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


@pytest.mark.asyncio
async def test_task_lifecycle_staleness_degrades_health() -> None:
    """超过两次小时巡检仍无成功心跳时必须进入降级状态。"""

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
        task_lifecycle_heartbeat_getter=lambda: now - timedelta(hours=3),
        configuration_ok=True,
        web_search_status_getter=lambda: "ok",
    )

    result = await service.check()

    assert result["status"] == "degraded"
    assert result["task_lifecycle"] == "stale"
@pytest.mark.asyncio
async def test_runtime_configuration_health_can_degrade_after_startup() -> None:
    """激活补偿冲突后动态配置标志应立即让健康状态降级。"""
    configuration = {"ok": True}
    now = datetime.now(UTC)

    async def database_probe() -> bool:
        """模拟健康数据库连接。"""
        return True

    service = OperationalHealthService(
        database_probe=database_probe,
        heartbeat_getter=lambda: now,
        poll_heartbeat_getter=lambda: now,
        hostex_heartbeat_getter=lambda: now,
        context_heartbeat_getter=lambda: now,
        lifecycle_heartbeat_getter=lambda: now,
        configuration_ok=lambda: configuration["ok"],
        web_search_status_getter=lambda: "ok",
    )

    assert (await service.check())["configuration"] == "ok"
    configuration["ok"] = False
    result = await service.check()
    assert result["configuration"] == "incomplete"
    assert result["status"] == "degraded"


@pytest.mark.asyncio
async def test_runtime_health_reads_current_revision_contact_and_intervals() -> None:
    """每次检查异步读取当前registry状态，并与DB revision核对。"""
    now = datetime.now(UTC)
    status = RuntimeClientStatus(
        revision=1,
        has_duty=True,
        contact_configured=False,
        wecom_poll_interval_seconds=60.0,
        hostex_reconcile_interval_seconds=200.0,
        resources_healthy=True,
    )
    database_revision = 2

    async def database_probe() -> bool:
        """模拟健康数据库连接。"""
        return True

    async def runtime_status_provider() -> RuntimeClientStatus:
        """返回测试当前运行状态。"""
        return status

    async def runtime_revision_provider() -> int:
        """返回数据库激活指针revision。"""
        return database_revision

    service = OperationalHealthService(
        database_probe=database_probe,
        heartbeat_getter=lambda: now,
        poll_heartbeat_getter=lambda: now - timedelta(seconds=100),
        hostex_heartbeat_getter=lambda: now - timedelta(seconds=300),
        context_heartbeat_getter=lambda: now,
        lifecycle_heartbeat_getter=lambda: now,
        configuration_ok=True,
        web_search_status_getter=lambda: "ok",
        runtime_status_provider=runtime_status_provider,
        runtime_revision_provider=runtime_revision_provider,
    )

    mismatched = await service.check()
    assert mismatched["configuration"] == "incomplete"

    database_revision = 1
    initial = await service.check()
    assert initial["configuration"] == "ok"
    assert initial["wecom_contact_sync"] == "not_configured"
    assert initial["wecom_polling"] == "ok"
    assert initial["hostex_reconcile"] == "ok"

    status = RuntimeClientStatus(
        revision=2,
        has_duty=True,
        contact_configured=True,
        wecom_poll_interval_seconds=5.0,
        hostex_reconcile_interval_seconds=20.0,
        resources_healthy=True,
    )
    database_revision = 2
    current = await service.check()
    assert current["configuration"] == "ok"
    assert current["wecom_contact_sync"] == "ok"
    assert current["wecom_polling"] == "stale"
    assert current["hostex_reconcile"] == "stale"


@pytest.mark.asyncio
async def test_webhook_health_never_borrows_the_reconcile_heartbeat() -> None:
    """Webhook 的健康状态必须由 Webhook 自己的心跳决定。

    此前只有一个心跳，同时被对账轮询和 Webhook 事件刷新，而对账每 15 分钟必成功
    一次。于是一个叫 `hostex_webhook_sync` 的健康项，在 Webhook 一次都没到达过的
    情况下照样报 ok——名字说的是回调，测的是轮询。生产实测：`hostex_webhook_events`
    表 0 行，该项仍为 ok。
    """
    now = datetime.now(UTC)
    fresh = now - timedelta(seconds=30)

    async def probe() -> bool:
        """模拟健康数据库连接。"""
        return True

    async def _same_revision() -> int:
        """与运行状态一致的 revision，避免因不匹配而整体降级。"""
        return 1

    def build(*, webhook_heartbeat, configured):
        """按给定的 Webhook 心跳与配置状态装配健康检查。"""

        async def status_provider() -> RuntimeClientStatus:
            """返回只在 Webhook 配置位上有差异的运行状态。"""
            return RuntimeClientStatus(
                revision=1,
                has_duty=True,
                contact_configured=False,
                wecom_poll_interval_seconds=60.0,
                hostex_reconcile_interval_seconds=200.0,
                resources_healthy=True,
                hostex_webhook_configured=configured,
            )

        return OperationalHealthService(
            database_probe=probe,
            heartbeat_getter=lambda: fresh,
            poll_heartbeat_getter=lambda: fresh,
            hostex_heartbeat_getter=lambda: fresh,
            hostex_webhook_heartbeat_getter=lambda: webhook_heartbeat,
            context_heartbeat_getter=lambda: fresh,
            lifecycle_heartbeat_getter=lambda: fresh,
            configuration_ok=True,
            web_search_status_getter=lambda: "ok",
            runtime_status_provider=status_provider,
            # 两个 provider 必须成对提供，否则 check() 会退回 runtime_status=None，
            # 读不到 Webhook 的配置位。
            runtime_revision_provider=_same_revision,
        )

    # 配置了回调却从未收到：对账心跳再新鲜也不能替它作证。
    never = await build(webhook_heartbeat=None, configured=True).check()
    assert never["hostex_reconcile"] == "ok"
    assert never["hostex_webhook"] == "never_received"
    assert never["status"] == "degraded"

    # 根本没配回调：如实报未配置，且不因此降级——与 wecom_contact_sync 一致。
    unset = await build(webhook_heartbeat=None, configured=False).check()
    assert unset["hostex_webhook"] == "not_configured"
    assert unset["status"] == "ok"

    # 真收到过才算 ok。
    live = await build(webhook_heartbeat=fresh, configured=True).check()
    assert live["hostex_webhook"] == "ok"
    assert live["status"] == "ok"


@pytest.mark.asyncio
async def test_benign_check_states_do_not_degrade() -> None:
    """诊断页标注为「不影响整体状态」的组合，必须真的不拉低整体健康。

    这份表在 routes/admin.py 里是 health.py 判定的副本，副本会漂移，
    所以由这条测试把两边钉在一起。
    """

    async def database_probe() -> bool:
        """模拟可用数据库。"""
        return True

    now = datetime.now(UTC)

    for key, state in sorted(_BENIGN_CHECK_STATES):
        web_search = "unknown" if key == "web_search" else "ok"
        service = OperationalHealthService(
            database_probe=database_probe,
            heartbeat_getter=lambda: now,
            poll_heartbeat_getter=lambda: now,
            hostex_heartbeat_getter=lambda: now,
            context_heartbeat_getter=lambda: now,
            lifecycle_heartbeat_getter=lambda: now,
            configuration_ok=True,
            web_search_status_getter=lambda value=web_search: value,
            contact_sync_configured=state == "ok",
        )

        result = await service.check()

        assert result[key] == state, f"{key} 未落在预期状态 {state}"
        assert result["status"] == "ok", f"{key}={state} 不应导致整体降级"


@pytest.mark.asyncio
async def test_configured_webhook_without_delivery_degrades_health() -> None:
    """配了回调密钥却从未收到推送，属于必须暴露的降级，不是良性状态。"""

    async def database_probe() -> bool:
        """模拟可用数据库。"""
        return True

    status = RuntimeClientStatus(
        revision=3,
        has_duty=True,
        contact_configured=True,
        wecom_poll_interval_seconds=60.0,
        hostex_reconcile_interval_seconds=200.0,
        resources_healthy=True,
        hostex_webhook_configured=True,
    )

    async def runtime_status_provider() -> RuntimeClientStatus:
        """提供已配置回调密钥的运行状态。"""
        return status

    async def runtime_revision_provider() -> int:
        """与运行配置 revision 保持一致，避免配置项误报。"""
        return 3

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
        hostex_webhook_heartbeat_getter=lambda: None,
        runtime_status_provider=runtime_status_provider,
        runtime_revision_provider=runtime_revision_provider,
    )

    result = await service.check()

    assert result["hostex_webhook"] == "never_received"
    assert result["status"] == "degraded"
    assert ("hostex_webhook", "never_received") not in _BENIGN_CHECK_STATES
