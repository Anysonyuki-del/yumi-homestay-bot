from datetime import UTC, date, datetime
from types import SimpleNamespace

from admin_auth_helpers import configure_admin_auth, login_admin
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.routes.admin import router as admin_router
from homestay_bot.routes.employee_auth import router as auth_router
from homestay_bot.services.admin_dashboard_service import Snapshot


class DashboardStub:
    """返回不含任何客户信息的固定总览。"""

    async def snapshot(self, now: datetime | None = None) -> Snapshot:
        """构造空数据快照。"""
        return Snapshot.empty(date(2026, 8, 11))


class HealthStub:
    """返回可控健康状态。"""

    def __init__(self, status: str = "degraded") -> None:
        """保存总体状态。"""
        self.status = status

    async def check(self) -> dict[str, str]:
        """返回内部组件的枚举状态，不含原始异常。"""
        return {
            "status": self.status,
            "database": "ok",
            "worker_heartbeat": "stale",
            "configuration": "incomplete",
        }


class MustChangeVerifier:
    """模拟仍处于首次改密阶段的活动管理员。"""

    async def get_active_admin(self, admin_id: int, employee_id: int) -> object:
        """返回必须先修改密码的版本化会话。"""
        return SimpleNamespace(
            employee_id=employee_id,
            role=EmployeeRole.ADMIN,
            is_active=True,
            session_version=1,
            must_change_password=True,
        )


def build_client() -> TestClient:
    """装配真实认证路由与后台页面。"""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="dashboard-test-secret")
    app.include_router(auth_router)
    app.include_router(admin_router)
    configure_admin_auth(app, EmployeeRole.ADMIN)
    app.state.admin_dashboard_service = DashboardStub()
    app.state.health_service = HealthStub()
    app.state.started_at = datetime(2026, 8, 11, tzinfo=UTC)
    return TestClient(app)


def test_dashboard_requires_login_and_first_password_change() -> None:
    """总览沿用统一会话门控，匿名用户不得读取页面。"""
    client = build_client()

    response = client.get(
        "/employee/admin",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/employee/login?")

    login_admin(client, next_path="/employee/admin")
    client.app.state.employee_access_verifier = MustChangeVerifier()
    forced_change = client.get("/employee/admin", follow_redirects=False)
    assert forced_change.status_code == 303
    assert forced_change.headers["location"] == "/employee/account"


def test_dashboard_renders_unified_safe_shell_for_empty_data() -> None:
    """空数据页面仍应包含统一导航、移动 viewport 和安全空状态。"""
    client = build_client()
    login_admin(client, next_path="/employee/admin")

    response = client.get("/employee/admin")

    assert response.status_code == 200
    assert '<meta name="viewport"' in response.text
    assert 'aria-current="page"' in response.text
    assert "总览" in response.text
    assert "任务中心" in response.text
    assert "今日暂无入住" in response.text
    assert "系统当前处于降级状态" in response.text
    for secret in ("uid-secret", "message-secret", "lock-secret", "password", "secret"):
        assert secret not in response.text.lower()
    assert "<pre" not in response.text.lower()


def test_diagnostics_keeps_http_200_when_health_is_degraded() -> None:
    """诊断页面应明确降级但保持可访问，且不输出原始 JSON。"""
    client = build_client()
    login_admin(client, next_path="/employee/admin/diagnostics")

    response = client.get("/employee/admin/diagnostics")

    assert response.status_code == 200
    assert "系统诊断" in response.text
    assert "需要关注" in response.text
    assert "{&quot;status&quot;" not in response.text
    assert "worker_heartbeat" not in response.text
    assert "incomplete" not in response.text
    assert "admin.js" in response.text
