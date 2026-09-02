import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.routes.employee_auth import AdminLoginRateLimiter
from homestay_bot.services.admin_auth_service import AdminSession
from homestay_bot.services.admin_csrf import AdminCsrfCapacityError


class MemoryAdminCsrfService:
    """以内存原子映射模拟服务端一次性认证 nonce。"""

    def __init__(self, *, max_active_per_scope: int = 8) -> None:
        """初始化 nonce 序号、未消费集合和作用域容量。"""
        self.sequence = 0
        self.pending: dict[str, tuple[str, int | None]] = {}
        self.issued_ttl: dict[str, timedelta | None] = {}
        self.max_active_per_scope = max_active_per_scope

    async def issue(
        self,
        purpose: str,
        *,
        admin_id: int | None,
        evict_oldest_in_scope: bool = False,
        ttl: timedelta | None = None,
    ) -> str:
        """签发测试可识别且用途绑定的唯一 nonce。

        复刻生产语义：作用域满时，开启淘汰则删最旧、否则拒绝。字典保持插入顺序，
        因此作用域列表天然是从旧到新。
        """
        scope = [
            token
            for token, stored in self.pending.items()
            if stored == (purpose, admin_id)
        ]
        if len(scope) >= self.max_active_per_scope:
            if not evict_oldest_in_scope:
                raise AdminCsrfCapacityError("测试作用域容量已满")
            for stale in scope[: len(scope) - self.max_active_per_scope + 1]:
                del self.pending[stale]
                self.issued_ttl.pop(stale, None)
        self.sequence += 1
        token = f"csrf-{self.sequence}-{purpose}"
        self.pending[token] = (purpose, admin_id)
        self.issued_ttl[token] = ttl
        return token

    async def consume(
        self,
        token: str,
        purpose: str,
        *,
        admin_id: int | None,
    ) -> bool:
        """仅在用途和主体匹配时原子弹出 nonce。"""
        if self.pending.get(token) != (purpose, admin_id):
            return False
        del self.pending[token]
        return True


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
    app.state.admin_csrf_service = MemoryAdminCsrfService()
    app.state.admin_login_rate_limiter = AdminLoginRateLimiter()
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
