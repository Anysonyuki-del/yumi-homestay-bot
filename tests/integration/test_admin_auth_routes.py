import logging
import re
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.routes.employee_auth import (
    require_employee_session,
)
from homestay_bot.routes.employee_auth import (
    router as employee_auth_router,
)
from homestay_bot.services.admin_auth_service import (
    AdminSession,
    AuthenticationError,
)

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
        if len(new) < 12:
            raise ValueError("新密码长度必须在 12 到 128 个字符之间")
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
    app.state.admin_auth_service = auth
    app.state.employee_access_verifier = AdminAccessVerifierStub(auth)
    app.state.admin_auth_clock = lambda: NOW

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


def csrf_from(response_text: str) -> str:
    """从基础认证表单提取一次性 CSRF 令牌。"""
    match = re.search(r'name="csrf_token" value="([^"]+)"', response_text)
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
    assert html.headers["location"].startswith(
        "/employee/login?next=%2Femployee%2Fprotected"
    )
    assert api.status_code == 401


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
            "csrf_token": csrf_from(account.text),
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


def test_logout_and_revoke_sessions_require_csrf_and_revoke_keeps_current_session() -> None:
    """退出与撤销其他会话均需 CSRF，撤销后只更新当前会话版本。"""
    client, auth = build_client()
    login(client)
    account = client.get("/employee/account")
    token = csrf_from(account.text)

    missing = client.post(
        "/employee/account/revoke-sessions",
        data={"password": "correct-password"},
    )
    account = client.get("/employee/account")
    token = csrf_from(account.text)
    revoked = client.post(
        "/employee/account/revoke-sessions",
        data={"password": "correct-password", "csrf_token": token},
        follow_redirects=False,
    )
    current = client.get("/employee/protected")
    account_again = client.get("/employee/account")
    logged_out = client.post(
        "/employee/logout",
        data={"csrf_token": csrf_from(account_again.text)},
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
