"""覆盖后台复审修复：匿名 nonce 作用域、登录限速默认值、缓存头和客诉角色边界。"""

import re
from datetime import UTC, datetime

import pytest
from admin_auth_helpers import MemoryAdminCsrfService, configure_admin_auth, login_admin
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.routes.complaints import router as complaints_router
from homestay_bot.routes.employee_auth import (
    LOGIN_PAGE_RATE_GLOBAL,
    LOGIN_RATE_GLOBAL,
    LOGIN_RATE_PER_IP,
    AdminLoginRateLimiter,
)
from homestay_bot.routes.employee_auth import (
    router as employee_auth_router,
)
from homestay_bot.routes.tasks import router as tasks_router

NOW = datetime(2026, 8, 12, 9, tzinfo=UTC)


def _client(role: EmployeeRole = EmployeeRole.ADMIN) -> TestClient:
    """装配含真实会话中间件、认证路由与业务路由的测试应用。"""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="admin-hardening-secret")
    app.include_router(employee_auth_router)
    app.include_router(tasks_router)
    app.include_router(complaints_router)
    configure_admin_auth(app, role)
    return TestClient(app)


def test_anonymous_login_nonce_scope_is_per_browser_session() -> None:
    """不同匿名浏览器必须落在互相独立的 nonce 作用域，避免一方占满另一方被拒。"""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="admin-hardening-secret")
    app.include_router(employee_auth_router)
    configure_admin_auth(app, EmployeeRole.ADMIN)
    service = MemoryAdminCsrfService()
    app.state.admin_csrf_service = service

    attacker = TestClient(app)
    for _ in range(8):
        attacker.cookies.clear()
        assert attacker.get("/employee/login").status_code == 200

    scopes = {purpose for purpose, _ in service.pending.values()}
    assert len(scopes) == 8, f"匿名 nonce 应按浏览器隔离，实际作用域: {scopes}"
    assert all(scope.startswith("login:") for scope in scopes)


def test_login_rate_limiter_defaults_to_per_ip_constant() -> None:
    """未显式传参时每 IP 上限必须是 LOGIN_RATE_PER_IP，而不是全局上限。"""
    limiter = AdminLoginRateLimiter()

    assert limiter._per_ip_limit == LOGIN_RATE_PER_IP


@pytest.mark.asyncio
async def test_login_page_flood_cannot_starve_credential_submissions() -> None:
    """刷登录页占满页面类别后，其他 IP 的凭据提交仍必须获得独立额度。"""
    limiter = AdminLoginRateLimiter()
    now = NOW

    page_allowed = 0
    for index in range(200):
        if await limiter.allow(f"page:203.0.113.{index % 250}", now, category="page"):
            page_allowed += 1
    assert page_allowed == LOGIN_PAGE_RATE_GLOBAL

    assert await limiter.allow("page:203.0.113.7", now, category="page") is False
    assert await limiter.allow("login:198.51.100.7", now, category="login") is True


@pytest.mark.asyncio
async def test_credential_submissions_keep_their_own_global_cap() -> None:
    """凭据提交类别独立计数，达到上限后不影响页面类别。"""
    limiter = AdminLoginRateLimiter()
    now = NOW

    login_allowed = 0
    for index in range(200):
        if await limiter.allow(f"login:203.0.113.{index % 250}", now, category="login"):
            login_allowed += 1

    assert login_allowed == LOGIN_RATE_GLOBAL
    assert await limiter.allow("page:198.51.100.9", now, category="page") is True


@pytest.mark.parametrize(
    "path",
    [
        "/employee/login",
        "/employee/tasks",
        "/employee/customers",
        "/employee/complaints/1",
        "/employee/knowledge",
        "/employee/approvals",
        "/employee/account",
    ],
)
def test_admin_surface_sets_no_store(path: str) -> None:
    """真实后台必须禁止缓存、嵌套展示和跨页面泄漏来源地址。"""
    from homestay_bot.main import app as real_app

    with TestClient(real_app) as client:
        response = client.get(path, follow_redirects=False)

    assert response.headers.get("cache-control") == "no-store"
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("content-security-policy") == "frame-ancestors 'none'"
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("referrer-policy") == "no-referrer"


@pytest.mark.parametrize("path", ["/static/app.css", "/health"])
def test_non_admin_paths_stay_uncapped(path: str) -> None:
    """静态资源和健康检查不属于敏感面，不得被后台专用响应头影响。"""
    from homestay_bot.main import app as real_app

    with TestClient(real_app) as client:
        response = client.get(path)

    assert response.headers.get("cache-control") != "no-store"
    assert response.headers.get("content-security-policy") is None
    assert response.headers.get("x-frame-options") is None
    assert response.headers.get("referrer-policy") is None


def test_staff_cannot_reach_complaint_review() -> None:
    """普通员工不得读取客诉对话或提交客诉动作。"""
    client = _client(EmployeeRole.STAFF)
    login_admin(client, next_path="/employee/tasks")

    detail = client.get("/employee/complaints/1", follow_redirects=False)
    assert detail.status_code == 403

    action = client.post(
        "/employee/complaints/1/save",
        data={"version": 1, "csrf_token": "x", "draft": "普通员工不应写入"},
        follow_redirects=False,
    )
    assert action.status_code == 403


def test_admin_still_reaches_complaint_review() -> None:
    """管理员的客诉复核入口必须保持可用。"""
    client = _client(EmployeeRole.ADMIN)
    login_admin(client)

    response = client.get("/employee/complaints/1", follow_redirects=False)

    assert response.status_code not in {401, 403}


def test_login_page_nonce_is_reused_within_one_session() -> None:
    """同一浏览器刷新登录页仍复用未消费 nonce，不额外占用作用域。"""
    client = _client()

    first = client.get("/employee/login")
    second = client.get("/employee/login")

    pattern = r'name="csrf_token" value="([^"]+)"'
    first_token = re.search(pattern, first.text)
    second_token = re.search(pattern, second.text)
    assert first_token is not None
    assert second_token is not None
    assert first_token.group(1) == second_token.group(1)


def test_login_succeeds_after_scope_isolation() -> None:
    """作用域隔离不能破坏正常登录的令牌签发与消费闭环。"""
    client = _client()

    login_admin(client)

    # 任务服务未在本测试装配，只断言会话已建立而不是业务渲染成功。
    protected = client.get("/employee/tasks", follow_redirects=False)
    assert protected.status_code not in {401, 403}
