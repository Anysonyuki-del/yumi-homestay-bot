import re
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.services.admin_auth_service import AdminSession


class RouteAdminAuthStub:
    """为既有后台路由测试提供独立账号密码认证。"""

    def __init__(self, role: EmployeeRole) -> None:
        """保存测试期希望业务路由观察到的角色。"""
        self.role = role

    async def authenticate(
        self,
        username: str,
        password: str,
        now: datetime,
    ) -> AdminSession:
        """接受固定测试凭据并返回完整版本化会话。"""
        assert username == "admin"
        assert password == "test-password"
        return AdminSession(
            admin_id=1,
            employee_id=1 if self.role is EmployeeRole.ADMIN else 2,
            username="admin",
            must_change_password=False,
            session_version=1,
        )


class RouteAdminVerifierStub:
    """为路由权限测试返回与目标角色一致的活动身份。"""

    def __init__(self, role: EmployeeRole) -> None:
        """保存当前业务角色。"""
        self.role = role
        self.calls: list[tuple[int, int]] = []

    async def get_active_admin(self, admin_id: int, employee_id: int):
        """返回测试专用的活动会话投影。"""
        self.calls.append((admin_id, employee_id))
        assert admin_id == 1
        assert employee_id == (1 if self.role is EmployeeRole.ADMIN else 2)
        return SimpleNamespace(
            admin_id=1,
            employee_id=employee_id,
            role=self.role,
            is_active=True,
            session_version=1,
            must_change_password=False,
            username="admin",
        )


def configure_admin_auth(
    app: FastAPI,
    role: EmployeeRole,
) -> RouteAdminVerifierStub:
    """给既有路由测试应用装配密码认证与请求期复核器。"""
    app.state.admin_auth_service = RouteAdminAuthStub(role)
    verifier = RouteAdminVerifierStub(role)
    app.state.employee_access_verifier = verifier
    app.state.admin_auth_clock = lambda: datetime.now(UTC)
    return verifier


def login_admin(
    client: TestClient,
    *,
    next_path: str = "/employee/tasks",
) -> None:
    """通过真实 GET/POST 登录表单建立测试管理员会话。"""
    page = client.get("/employee/login", params={"next": next_path})
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match is not None
    response = client.post(
        "/employee/login",
        data={
            "username": "admin",
            "password": "test-password",
            "next": next_path,
            "csrf_token": match.group(1),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
