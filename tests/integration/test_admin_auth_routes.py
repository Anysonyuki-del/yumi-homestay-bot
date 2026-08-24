import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from admin_auth_helpers import MemoryAdminCsrfService
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.routes.approvals import router as approvals_router
from homestay_bot.routes.complaints import router as complaints_router
from homestay_bot.routes.employee_auth import (
    AdminLoginRateLimiter,
    require_employee_session,
)
from homestay_bot.routes.employee_auth import (
    router as employee_auth_router,
)
from homestay_bot.routes.tasks import router as tasks_router
from homestay_bot.services.admin_auth_service import (
    AdminSession,
    AuthenticationError,
)
from homestay_bot.services.admin_csrf import AdminCsrfCapacityError

NOW = datetime(2026, 8, 11, 9, tzinfo=UTC)


class AdminAuthStub:
    """模拟唯一管理员认证、改密和会话撤销。"""

    def __init__(self) -> None:
        """初始化固定管理员状态和调用记录。"""
        self.version = 4
        self.must_change_password = False
        self.authenticate_calls: list[tuple[str, str, datetime]] = []
        self.changed_passwords: list[tuple[int, str, str]] = []
        self.reverified_passwords: list[tuple[int, str]] = []

    async def authenticate(
        self,
        username: str,
        password: str,
        now: datetime,
    ) -> AdminSession:
        """仅接受测试管理员凭据，并对所有失败使用统一错误。"""
        self.authenticate_calls.append((username, password, now))
        if username != "admin" or password != "correct-password":
            raise AuthenticationError("用户名或密码错误")
        return AdminSession(
            admin_id=1,
            employee_id=7,
            username="admin",
            must_change_password=self.must_change_password,
            session_version=self.version,
        )

    async def change_password(self, admin_id: int, current: str, new: str) -> None:
        """校验当前密码后模拟原子改密和会话版本递增。"""
        self.changed_passwords.append((admin_id, current, new))
        if current != "correct-password":
            raise AuthenticationError("用户名或密码错误")
        if not new.strip():
            raise ValueError("新密码不能为空或全为空白")
        if len(new) > 128:
            raise ValueError("新密码不能超过 128 个字符")
        self.version += 1
        self.must_change_password = False

    async def reverify(self, admin_id: int, password: str) -> None:
        """模拟敏感操作前的当前密码复核。"""
        self.reverified_passwords.append((admin_id, password))
        if password != "correct-password":
            raise AuthenticationError("用户名或密码错误")

    async def revoke_other_sessions(self, admin_id: int) -> int:
        """递增并返回最新会话版本。"""
        self.version += 1
        return self.version

    async def reverify_and_revoke_sessions(
        self,
        admin_id: int,
        password: str,
        expected_session_version: int,
    ) -> int:
        """模拟单事务密码复核与版本 CAS。"""
        assert expected_session_version == self.version
        await self.reverify(admin_id, password)
        return await self.revoke_other_sessions(admin_id)


class AdminAccessVerifierStub:
    """把认证服务当前状态返回给请求期复核逻辑。"""

    def __init__(self, auth: AdminAuthStub) -> None:
        """共享认证状态，以便改密后立即返回新版本。"""
        self.auth = auth
        self.active = True

    async def get_active_admin(self, admin_id: int, employee_id: int):
        """返回不含密码哈希的活动管理员投影。"""
        if not self.active or admin_id != 1 or employee_id != 7:
            return None
        return SimpleNamespace(
            admin_id=1,
            employee_id=7,
            role=EmployeeRole.ADMIN,
            is_active=True,
            session_version=self.auth.version,
            must_change_password=self.auth.must_change_password,
            username="admin",
        )


def build_client(*, must_change_password: bool = False) -> tuple[TestClient, AdminAuthStub]:
    """创建包含真实会话中间件与认证路由的测试应用。"""
    auth = AdminAuthStub()
    auth.must_change_password = must_change_password
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="admin-auth-test-secret")
    app.include_router(employee_auth_router)
    app.include_router(tasks_router)
    app.include_router(approvals_router)
    app.include_router(complaints_router)
    app.state.admin_auth_service = auth
    app.state.admin_csrf_service = MemoryAdminCsrfService()
    app.state.employee_access_verifier = AdminAccessVerifierStub(auth)
    app.state.admin_auth_clock = lambda: NOW
    app.state.admin_login_rate_limiter = AdminLoginRateLimiter()

    @app.get("/employee/protected")
    async def protected(request: Request) -> dict[str, object]:
        """返回测试可观察的受保护会话字段。"""
        employee_id, role = await require_employee_session(request)
        return {
            "employee_id": employee_id,
            "role": role.value,
            "session": dict(request.session),
        }

    return TestClient(app), auth


REAL_PROTECTED_GET_PATHS = [
    "/employee/tasks",
    "/employee/approvals",
    "/employee/approvals/1",
    "/employee/complaints/1",
]


def csrf_from(response_text: str) -> str:
    """从基础认证表单提取一次性 CSRF 令牌。"""
    match = re.search(r'name="csrf_token" value="([^"]+)"', response_text)
    assert match is not None
    return match.group(1)


def csrf_for_action(response_text: str, action: str) -> str:
    """从指定认证表单提取其独立用途 nonce。"""
    match = re.search(
        rf'<form method="post" action="{re.escape(action)}">.*?'
        r'name="csrf_token" value="([^"]+)"',
        response_text,
        re.DOTALL,
    )
    assert match is not None
    return match.group(1)


def login(
    client: TestClient,
    *,
    username: str = "admin",
    password: str = "correct-password",
    next_path: str = "/employee/protected",
):
    """读取登录页令牌并提交账号密码。"""
    page = client.get("/employee/login", params={"next": next_path})
    return client.post(
        "/employee/login",
        data={
            "username": username,
            "password": password,
            "next": next_path,
            "csrf_token": csrf_from(page.text),
        },
        follow_redirects=False,
    )


def test_get_login_renders_html_and_only_keeps_internal_next() -> None:
    """登录页应返回 HTML，外部或协议相对 next 必须回退默认站内页。"""
    client, _ = build_client()

    normal = client.get("/employee/login", params={"next": "/employee/account"})
    external = client.get("/employee/login", params={"next": "https://evil.test"})
    protocol_relative = client.get("/employee/login", params={"next": "//evil.test"})

    assert normal.status_code == 200
    assert 'value="/employee/account"' in normal.text
    assert "https://evil.test" not in external.text
    assert "//evil.test" not in protocol_relative.text


def test_login_page_refresh_reuses_unconsumed_nonce() -> None:
    """同一浏览器刷新登录页应复用未消费 nonce，避免数据库持续插入。"""
    client, _ = build_client()

    first = client.get("/employee/login")
    second = client.get("/employee/login")

    assert csrf_from(first.text) == csrf_from(second.text)
    assert client.app.state.admin_csrf_service.sequence == 1


def test_anonymous_login_get_is_rate_limited_by_real_client_ip() -> None:
    """匿名 GET 也受共享限速，伪造 XFF 不能绕过真实来源 IP 上限。"""
    client, _ = build_client()
    client.app.state.admin_login_rate_limiter = AdminLoginRateLimiter(
        page_per_ip_limit=2,
        page_global_limit=3,
    )

    statuses = [
        client.get(
            "/employee/login",
            headers={"X-Forwarded-For": f"198.51.100.{index}"},
        ).status_code
        for index in range(3)
    ]

    assert statuses == [200, 200, 429]


def test_anonymous_login_get_returns_429_when_nonce_capacity_is_full() -> None:
    """服务端活动 nonce 达到硬上限时匿名登录页必须返回 429。"""

    class FullCsrfService(MemoryAdminCsrfService):
        """模拟全局 nonce 容量已满。"""

        async def issue(self, purpose: str, *, admin_id: int | None) -> str:
            """稳定抛出生产容量异常。"""
            raise AdminCsrfCapacityError("full")

    client, _ = build_client()
    client.app.state.admin_csrf_service = FullCsrfService()

    response = client.get("/employee/login")

    assert response.status_code == 429


def test_login_reports_503_when_admin_auth_is_degraded() -> None:
    """后台引导不可用时登录页应明确 503，而不是影响应用其他路由启动。"""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="admin-auth-test-secret")
    app.include_router(employee_auth_router)

    response = TestClient(app).get("/employee/login")

    assert response.status_code == 503


def test_unauthenticated_html_redirects_but_api_boundary_stays_401() -> None:
    """浏览器页面统一跳登录，非 HTML 调用仍保留明确 401 安全边界。"""
    client, _ = build_client()

    html = client.get(
        "/employee/protected?tab=security",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )
    api = client.get(
        "/employee/protected",
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )

    assert html.status_code == 303
    assert html.headers["location"].startswith("/employee/login?next=%2Femployee%2Fprotected")
    assert api.status_code == 401


@pytest.mark.parametrize(
    ("accept", "expected_status"),
    [
        ("*/*", 401),
        ("text/html;q=0, application/json;q=0.5", 401),
        ("text/html;q=0.4, application/json;q=0.9", 401),
        ("application/json;q=0.4, text/html;q=0.9", 303),
        ("text/html, */*;q=0.1", 303),
    ],
)
def test_unauthenticated_response_honors_accept_quality(
    accept: str,
    expected_status: int,
) -> None:
    """认证边界必须按 q 值协商，通配 Accept 默认保持 API 401。"""
    client, _ = build_client()

    response = client.get(
        "/employee/protected",
        headers={"Accept": accept},
        follow_redirects=False,
    )

    assert response.status_code == expected_status


def test_unauthenticated_post_redirect_uses_safe_get_next() -> None:
    """未登录 POST 的跳转目标必须是安全 GET 页面，不能回到仅 POST 动作。"""
    client, _ = build_client()

    response = client.post(
        "/employee/logout",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith("next=%2Femployee%2Ftasks")


def test_login_nonce_is_rejected_without_the_issuing_browser_session() -> None:
    """匿名 nonce 绑定签发它的浏览器作用域，换一个 Cookie 一律不得消费。"""
    client, _ = build_client()
    page = client.get("/employee/login")
    token = csrf_from(page.text)

    def submit() -> int:
        """使用与签发无关的旧 Cookie 模拟并发提交。"""
        request_client = TestClient(client.app)
        request_client.cookies.set("session", "stale-browser-session")
        return request_client.post(
            "/employee/login",
            data={
                "username": "admin",
                "password": "correct-password",
                "next": "/employee/protected",
                "csrf_token": token,
            },
            follow_redirects=False,
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = sorted(executor.map(lambda _: submit(), range(2)))

    assert statuses == [409, 409]


def test_concurrent_login_posts_can_only_consume_same_nonce_once() -> None:
    """同一浏览器会话的两个并发登录 POST 仍然只有一个能消费该 nonce。"""
    client, _ = build_client()
    page = client.get("/employee/login")
    token = csrf_from(page.text)
    issuing_cookies = dict(client.cookies)

    def submit() -> int:
        """复用签发时的真实会话 Cookie 并发提交同一 token。"""
        request_client = TestClient(client.app)
        for name, value in issuing_cookies.items():
            request_client.cookies.set(name, value)
        return request_client.post(
            "/employee/login",
            data={
                "username": "admin",
                "password": "correct-password",
                "next": "/employee/protected",
                "csrf_token": token,
            },
            follow_redirects=False,
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = sorted(executor.map(lambda _: submit(), range(2)))

    assert statuses == [303, 409]


@pytest.mark.parametrize("path", REAL_PROTECTED_GET_PATHS)
def test_real_routes_preserve_html_redirect_and_api_401(path: str) -> None:
    """真实后台 GET 路由不得把 API 未登录错误改写成 HTML 登录跳转。"""
    client, _ = build_client()

    html = client.get(path, headers={"Accept": "text/html"}, follow_redirects=False)
    api = client.get(
        path,
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )

    assert html.status_code == 303
    assert html.headers["location"].startswith("/employee/login?next=")
    assert api.status_code == 401


@pytest.mark.parametrize("path", REAL_PROTECTED_GET_PATHS)
def test_real_routes_preserve_first_password_change_redirect(path: str) -> None:
    """首次改密会话访问真实后台页时必须跳账号页，不能被改写到登录页。"""
    client, auth = build_client()
    login(client)
    auth.must_change_password = True

    response = client.get(
        path,
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/employee/account"


@pytest.mark.parametrize("path", REAL_PROTECTED_GET_PATHS)
def test_real_routes_do_not_swallow_session_verifier_503(path: str) -> None:
    """复核服务故障属于 503，真实路由不得误报为未登录。"""
    client, _ = build_client()
    login(client)
    delattr(client.app.state, "employee_access_verifier")

    response = client.get(path, follow_redirects=False)

    assert response.status_code == 503


def test_login_requires_one_time_csrf() -> None:
    """登录 POST 缺失、伪造或重放 CSRF 时不得调用认证服务。"""
    client, auth = build_client()
    page = client.get("/employee/login")
    token = csrf_from(page.text)

    missing = client.post(
        "/employee/login",
        data={"username": "admin", "password": "correct-password"},
    )
    forged_page = client.get("/employee/login")
    forged = client.post(
        "/employee/login",
        data={
            "username": "admin",
            "password": "correct-password",
            "csrf_token": "forged",
        },
    )
    assert csrf_from(forged_page.text) != "forged"
    valid_page = client.get("/employee/login")
    token = csrf_from(valid_page.text)
    first = client.post(
        "/employee/login",
        data={
            "username": "admin",
            "password": "correct-password",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    replay = client.post(
        "/employee/login",
        data={
            "username": "admin",
            "password": "correct-password",
            "csrf_token": token,
        },
    )

    assert missing.status_code == 409
    assert forged.status_code == 409
    assert first.status_code == 303
    assert replay.status_code == 409
    assert len(auth.authenticate_calls) == 1


def test_unknown_username_and_wrong_password_return_same_safe_error(caplog) -> None:
    """账号枚举错误和密码错误不得返回不同文案，也不得泄露密码到日志。"""
    client, _ = build_client()
    secret = "ROUTE-SENSITIVE-WRONG-PASSWORD"

    with caplog.at_level(logging.INFO):
        unknown = login(client, username="missing", password=secret)
        wrong = login(client, username="admin", password=secret)

    assert unknown.status_code == wrong.status_code == 401
    assert "用户名或密码错误" in unknown.text
    assert "用户名或密码错误" in wrong.text
    assert secret not in unknown.text
    assert secret not in wrong.text
    assert secret not in caplog.text


def test_oversized_password_is_never_copied_into_validation_response(caplog) -> None:
    """超长敏感输入也只能返回统一错误，不能由 422 详情复制到响应。"""
    client, _ = build_client()
    secret = "OVERSIZED-SENSITIVE-" + "x" * 129

    with caplog.at_level(logging.INFO):
        response = login(client, password=secret)

    assert response.status_code == 401
    assert "用户名或密码错误" in response.text
    assert secret not in response.text
    assert secret not in caplog.text


def test_login_is_rate_limited_without_exposing_credentials() -> None:
    """单 IP 短时重复登录必须被有界限速，响应不得复制凭据。"""
    client, _ = build_client()
    responses = [login(client, username="missing", password="wrong-password") for _ in range(11)]

    assert [response.status_code for response in responses[:10]] == [401] * 10
    assert responses[-1].status_code == 429
    assert "missing" not in responses[-1].text
    assert "wrong-password" not in responses[-1].text


def test_login_clears_old_session_and_writes_complete_admin_identity() -> None:
    """成功登录前必须清空旧会话，并写入完整管理员版本与活动时间。"""
    client, auth = build_client()

    response = login(client, next_path="https://evil.test/steal")
    protected = client.get("/employee/protected")
    session = protected.json()["session"]

    assert response.status_code == 303
    assert response.headers["location"] == "/employee/tasks"
    assert auth.authenticate_calls == [("admin", "correct-password", NOW)]
    assert session["employee_id"] == 7
    assert session["employee_role"] == "admin"
    assert session["admin_id"] == 1
    assert session["admin_session_version"] == 4
    assert session["last_activity_at"] == NOW.isoformat()
    assert "auth_csrf" not in session


def test_first_login_only_allows_password_change_then_updates_current_version() -> None:
    """首次登录应强制进入改密页，改密成功后当前会话继续有效。"""
    client, auth = build_client(must_change_password=True)
    response = login(client)

    protected = client.get("/employee/protected", follow_redirects=False)
    account = client.get("/employee/account")
    changed = client.post(
        "/employee/account/password",
        data={
            "current_password": "correct-password",
            "new_password": "new-secure-password",
            "csrf_token": csrf_for_action(
                account.text,
                "/employee/account/password",
            ),
        },
        follow_redirects=False,
    )
    after = client.get("/employee/protected")

    assert response.headers["location"] == "/employee/account"
    assert protected.status_code == 303
    assert protected.headers["location"] == "/employee/account"
    assert "修改初始密码" in account.text
    assert changed.status_code == 303
    assert auth.changed_passwords == [(1, "correct-password", "new-secure-password")]
    assert after.status_code == 200
    assert after.json()["session"]["admin_session_version"] == 5


def test_password_forms_do_not_require_a_minimum_length() -> None:
    """普通改密页和首次改密页都不得在浏览器端恢复 12 位限制。"""
    client, _ = build_client()
    login(client)
    regular = client.get("/employee/account")

    first_login_client, _ = build_client(must_change_password=True)
    login(first_login_client)
    first_login = first_login_client.get("/employee/account")

    assert 'name="new_password"' in regular.text
    assert 'name="new_password"' in first_login.text
    assert 'minlength="12"' not in regular.text
    assert 'minlength="12"' not in first_login.text


def test_password_route_accepts_a_one_character_new_password() -> None:
    """控制页面提交一个字符的新密码时，后端路由必须正常受理。"""
    client, auth = build_client()
    login(client)
    account = client.get("/employee/account")

    changed = client.post(
        "/employee/account/password",
        data={
            "current_password": "correct-password",
            "new_password": "x",
            "csrf_token": csrf_for_action(
                account.text,
                "/employee/account/password",
            ),
        },
        follow_redirects=False,
    )

    assert changed.status_code == 303
    assert auth.changed_passwords == [(1, "correct-password", "x")]


def test_logout_and_revoke_sessions_require_csrf_and_revoke_keeps_current_session() -> None:
    """退出与撤销其他会话均需 CSRF，撤销后只更新当前会话版本。"""
    client, auth = build_client()
    login(client)
    account = client.get("/employee/account")
    token = csrf_for_action(
        account.text,
        "/employee/account/revoke-sessions",
    )

    missing = client.post(
        "/employee/account/revoke-sessions",
        data={"password": "correct-password"},
    )
    account = client.get("/employee/account")
    token = csrf_for_action(
        account.text,
        "/employee/account/revoke-sessions",
    )
    revoked = client.post(
        "/employee/account/revoke-sessions",
        data={"password": "correct-password", "csrf_token": token},
        follow_redirects=False,
    )
    current = client.get("/employee/protected")
    account_again = client.get("/employee/account")
    logged_out = client.post(
        "/employee/logout",
        data={
            "csrf_token": csrf_for_action(
                account_again.text,
                "/employee/logout",
            )
        },
        follow_redirects=False,
    )
    after_logout = client.get("/employee/protected", follow_redirects=False)

    assert missing.status_code == 409
    assert revoked.status_code == 303
    assert auth.reverified_passwords == [(1, "correct-password")]
    assert current.json()["session"]["admin_session_version"] == 5
    assert logged_out.status_code == 303
    assert logged_out.headers["location"] == "/employee/login"
    assert after_logout.status_code == 401
