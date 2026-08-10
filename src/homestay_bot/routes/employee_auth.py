import asyncio
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NoReturn, Protocol, cast
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.services.admin_auth_service import (
    AUTHENTICATION_ERROR_MESSAGE,
    AdminSession,
    AuthenticationError,
)

router = APIRouter(prefix="/employee")
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")
SESSION_IDLE_TIMEOUT = timedelta(hours=8)
DEFAULT_NEXT_PATH = "/employee/tasks"
FIRST_LOGIN_ALLOWED_PATHS = {
    "/employee/account",
    "/employee/account/password",
    "/employee/logout",
}
LOGIN_RATE_WINDOW = timedelta(minutes=1)
LOGIN_RATE_PER_IP = 10
LOGIN_RATE_GLOBAL = 500


class AdminLoginRateLimiter:
    """提供单进程、内存有界且不记录登录正文的登录限速。"""

    def __init__(self, *, max_clients: int = 1024) -> None:
        """初始化按 IP 与全局时间队列。"""
        self._max_clients = max_clients
        self._by_ip: dict[str, deque[datetime]] = {}
        self._global: deque[datetime] = deque()
        self._lock = asyncio.Lock()

    async def allow(self, client_ip: str, now: datetime) -> bool:
        """原子清理过期记录并判断本次尝试是否仍在固定上限内。"""
        cutoff = now - LOGIN_RATE_WINDOW
        async with self._lock:
            while self._global and self._global[0] <= cutoff:
                self._global.popleft()
            if client_ip not in self._by_ip and len(self._by_ip) >= self._max_clients:
                # 全局限速仍覆盖被淘汰来源；强制淘汰最老键以确保内存严格有界。
                del self._by_ip[next(iter(self._by_ip))]
            attempts = self._by_ip.setdefault(client_ip, deque())
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            if len(attempts) >= LOGIN_RATE_PER_IP or len(self._global) >= LOGIN_RATE_GLOBAL:
                return False
            attempts.append(now)
            self._global.append(now)
            return True


class AdminAuthServicePort(Protocol):
    """定义账号密码路由所需的唯一管理员认证接口。"""

    async def authenticate(
        self,
        username: str,
        password: str,
        now: datetime,
    ) -> AdminSession:
        """校验账号密码并返回最小会话身份。"""

    async def change_password(self, admin_id: int, current: str, new: str) -> None:
        """复核当前密码并原子修改密码。"""

    async def reverify(self, admin_id: int, password: str) -> None:
        """高风险操作前重新校验当前密码。"""

    async def revoke_other_sessions(self, admin_id: int) -> int:
        """撤销其他会话并返回当前最新版本。"""

    async def reverify_and_revoke_sessions(
        self,
        admin_id: int,
        password: str,
        expected_session_version: int,
    ) -> int:
        """在一个事务中复核密码并撤销其他会话。"""


class ActiveAdminState(Protocol):
    """定义请求期间只读的管理员与员工联合投影。"""

    employee_id: int
    role: EmployeeRole
    is_active: bool
    session_version: int
    must_change_password: bool


class EmployeeAccessVerifier(Protocol):
    """定义请求期间重新验证唯一管理员状态的接口。"""

    async def get_active_admin(
        self,
        admin_id: int,
        employee_id: int,
    ) -> ActiveAdminState | None:
        """同时复核凭证、外键员工、角色与会话版本。"""


class AdminCsrfServicePort(Protocol):
    """定义认证路由所需的服务端一次性 nonce 接口。"""

    async def issue(self, purpose: str, *, admin_id: int | None) -> str:
        """签发按用途和管理员绑定的 nonce。"""

    async def consume(
        self,
        token: str,
        purpose: str,
        *,
        admin_id: int | None,
    ) -> bool:
        """原子消费匹配 nonce。"""


def _clock(request: Request) -> Callable[[], datetime]:
    """读取测试可注入的 UTC 时钟，生产默认使用当前 UTC 时间。"""
    value = getattr(request.app.state, "admin_auth_clock", None)
    if callable(value):
        return cast(Callable[[], datetime], value)
    return lambda: datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    """把会话中的无时区兼容时间统一解释为 UTC。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _safe_next(next_path: str) -> str:
    """只允许以单斜杠开头的站内绝对路径，阻断开放重定向。"""
    if next_path.startswith("/") and not next_path.startswith("//"):
        return next_path
    return DEFAULT_NEXT_PATH


def _prefers_html(accept: str) -> bool:
    """按 q 值协商响应；只有明确偏好 HTML 时才执行浏览器跳转。"""
    qualities: dict[str, float] = {}
    for raw_item in accept.lower().split(","):
        parts = [part.strip() for part in raw_item.split(";")]
        media_type = parts[0]
        if not media_type:
            continue
        quality = 1.0
        for parameter in parts[1:]:
            if parameter.startswith("q="):
                try:
                    quality = min(1.0, max(0.0, float(parameter[2:])))
                except ValueError:
                    quality = 0.0
        qualities[media_type] = max(quality, qualities.get(media_type, 0.0))
    html_quality = qualities.get("text/html", 0.0)
    competing_quality = max(
        qualities.get("application/json", 0.0),
        qualities.get("application/*", 0.0),
        qualities.get("*/*", 0.0),
    )
    return html_quality > 0 and html_quality > competing_quality


def _get_csrf_service(request: Request) -> AdminCsrfServicePort:
    """读取服务端 nonce 服务，未装配时明确拒绝认证表单。"""
    service = getattr(request.app.state, "admin_csrf_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="认证表单安全服务尚未配置")
    return cast(AdminCsrfServicePort, service)


async def _consume_csrf(
    request: Request,
    token: str,
    purpose: str,
    *,
    admin_id: int | None,
) -> None:
    """原子消费服务端 nonce，拒绝缺失、伪造、过期和重放。"""
    if not await _get_csrf_service(request).consume(
        token,
        purpose,
        admin_id=admin_id,
    ):
        raise HTTPException(status_code=409, detail="表单令牌无效或已使用")


def _get_auth_service(request: Request) -> AdminAuthServicePort:
    """从应用状态读取管理员认证服务。"""
    service = getattr(request.app.state, "admin_auth_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="管理员认证服务尚未配置",
        )
    return cast(AdminAuthServicePort, service)


def _get_access_verifier(request: Request) -> EmployeeAccessVerifier:
    """读取生产必需的管理员会话复核器。"""
    verifier = getattr(request.app.state, "employee_access_verifier", None)
    if verifier is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="管理员会话复核服务尚未配置",
        )
    return cast(EmployeeAccessVerifier, verifier)


def _clear_and_reject(request: Request, detail: str) -> NoReturn:
    """清空会话；HTML 请求跳登录，API 请求保留 401 边界。"""
    request.session.clear()
    if _prefers_html(request.headers.get("accept", "")):
        # POST 动作不能成为登录后的 next，统一回到安全的 GET 后台页。
        next_path = request.url.path if request.method == "GET" else DEFAULT_NEXT_PATH
        location = f"/employee/login?{urlencode({'next': next_path})}"
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail=detail,
            headers={"Location": location},
        )
    raise HTTPException(status_code=401, detail=detail)


def _parse_last_activity(request: Request) -> datetime:
    """严格解析最后活动时间，异常或缺失均视为无效会话。"""
    value = request.session.get("last_activity_at")
    if not isinstance(value, str):
        _clear_and_reject(request, "管理员会话无效")
    try:
        return _as_utc(datetime.fromisoformat(value))
    except ValueError:
        _clear_and_reject(request, "管理员会话无效")


async def require_employee_session(
    request: Request,
) -> tuple[int, EmployeeRole]:
    """复核唯一管理员、会话版本和八小时闲置边界。"""
    employee_id = request.session.get("employee_id")
    admin_id = request.session.get("admin_id")
    session_version = request.session.get("admin_session_version")
    if not all(isinstance(value, int) for value in (employee_id, admin_id, session_version)):
        _clear_and_reject(request, "管理员尚未登录")

    now = _as_utc(_clock(request)())
    if now - _parse_last_activity(request) > SESSION_IDLE_TIMEOUT:
        _clear_and_reject(request, "管理员会话已超时")

    state = await _get_access_verifier(request).get_active_admin(
        cast(int, admin_id),
        cast(int, employee_id),
    )
    if state is None or not state.is_active:
        _clear_and_reject(request, "管理员已停用或不存在")
    if state.session_version != session_version:
        _clear_and_reject(request, "管理员会话已失效")

    request.session["employee_role"] = state.role.value
    request.session["last_activity_at"] = now.isoformat()
    if state.must_change_password and request.url.path not in FIRST_LOGIN_ALLOWED_PATHS:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="首次登录必须修改密码",
            headers={"Location": "/employee/account"},
        )
    return cast(int, employee_id), state.role


async def _current_admin_state(request: Request) -> ActiveAdminState:
    """读取当前已验证会话对应的不含密码哈希的管理员投影。"""
    state = await _get_access_verifier(request).get_active_admin(
        cast(int, request.session["admin_id"]),
        cast(int, request.session["employee_id"]),
    )
    if state is None:
        _clear_and_reject(request, "管理员已停用或不存在")
    return state


async def _login_page(
    request: Request,
    *,
    next_path: str,
    error: str | None = None,
    status_code: int = 200,
) -> Response:
    """渲染不回填密码的登录页并重新签发令牌。"""
    csrf_token = await _get_csrf_service(request).issue("login", admin_id=None)
    return templates.TemplateResponse(
        request=request,
        name="auth/login.html",
        context={
            "csrf_token": csrf_token,
            "next_path": _safe_next(next_path),
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/login", response_class=HTMLResponse)
async def employee_login(
    request: Request,
    next_path: str = Query(DEFAULT_NEXT_PATH, alias="next"),
) -> Response:
    """展示独立管理员账号密码登录页。"""
    return await _login_page(request, next_path=next_path)


@router.post("/login")
async def employee_login_submit(
    request: Request,
    username: str = Form(),
    password: str = Form(),
    csrf_token: str = Form(""),
    next_path: str = Form(DEFAULT_NEXT_PATH, alias="next"),
) -> Response:
    """校验一次性令牌和账号密码，并建立最小管理员会话。"""
    await _consume_csrf(
        request,
        csrf_token,
        "login",
        admin_id=None,
    )
    limiter = getattr(request.app.state, "admin_login_rate_limiter", None)
    if limiter is None:
        limiter = AdminLoginRateLimiter()
        request.app.state.admin_login_rate_limiter = limiter
    client_ip = request.client.host if request.client is not None else "unknown"
    now = _as_utc(_clock(request)())
    if not await cast(AdminLoginRateLimiter, limiter).allow(client_ip, now):
        return await _login_page(
            request,
            next_path=next_path,
            error="登录尝试过于频繁，请稍后再试",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    if len(username) > 128 or len(password) > 128:
        return await _login_page(
            request,
            next_path=next_path,
            error=AUTHENTICATION_ERROR_MESSAGE,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    try:
        authenticated = await _get_auth_service(request).authenticate(
            username,
            password,
            now,
        )
    except AuthenticationError:
        return await _login_page(
            request,
            next_path=next_path,
            error=AUTHENTICATION_ERROR_MESSAGE,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # 认证成功后先清除登录令牌和任何旧身份，避免会话固定与脏状态继承。
    request.session.clear()
    request.session.update(
        {
            "employee_id": authenticated.employee_id,
            "employee_role": EmployeeRole.ADMIN.value,
            "admin_id": authenticated.admin_id,
            "admin_session_version": authenticated.session_version,
            "last_activity_at": now.isoformat(),
        }
    )
    location = "/employee/account" if authenticated.must_change_password else _safe_next(next_path)
    return RedirectResponse(location, status_code=status.HTTP_303_SEE_OTHER)


async def _account_page(
    request: Request,
    *,
    must_change_password: bool,
    error: str | None = None,
    notice: str | None = None,
    status_code: int = 200,
) -> Response:
    """渲染账号安全页，页面上下文不包含密码或哈希。"""
    template_name = "auth/change_password.html" if must_change_password else "account/detail.html"
    admin_id = cast(int, request.session["admin_id"])
    csrf_service = _get_csrf_service(request)
    password_csrf = await csrf_service.issue("password", admin_id=admin_id)
    logout_csrf = await csrf_service.issue("logout", admin_id=admin_id)
    revoke_csrf = await csrf_service.issue("revoke-sessions", admin_id=admin_id)
    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context={
            "password_csrf_token": password_csrf,
            "logout_csrf_token": logout_csrf,
            "revoke_csrf_token": revoke_csrf,
            "error": error,
            "notice": notice,
        },
        status_code=status_code,
    )


@router.get("/account", response_class=HTMLResponse)
async def employee_account(request: Request) -> Response:
    """展示当前唯一管理员的账号安全操作。"""
    await require_employee_session(request)
    state = await _current_admin_state(request)
    return await _account_page(
        request,
        must_change_password=state.must_change_password,
    )


@router.post("/account/password")
async def employee_change_password(
    request: Request,
    current_password: str = Form(),
    new_password: str = Form(),
    csrf_token: str = Form(""),
) -> Response:
    """使用当前密码修改密码，并把本会话推进到原子更新后的版本。"""
    await require_employee_session(request)
    admin_id = cast(int, request.session["admin_id"])
    await _consume_csrf(
        request,
        csrf_token,
        "password",
        admin_id=admin_id,
    )
    before_change = await _current_admin_state(request)
    if len(current_password) > 128:
        return await _account_page(
            request,
            must_change_password=before_change.must_change_password,
            error=AUTHENTICATION_ERROR_MESSAGE,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    try:
        await _get_auth_service(request).change_password(
            admin_id,
            current_password,
            new_password,
        )
    except AuthenticationError:
        return await _account_page(
            request,
            must_change_password=before_change.must_change_password,
            error=AUTHENTICATION_ERROR_MESSAGE,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    except ValueError as error:
        return await _account_page(
            request,
            must_change_password=before_change.must_change_password,
            error=str(error),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    state = await _current_admin_state(request)
    request.session["admin_session_version"] = state.session_version
    return RedirectResponse("/employee/account", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/account/revoke-sessions")
async def employee_revoke_sessions(
    request: Request,
    password: str = Form(),
    csrf_token: str = Form(""),
) -> Response:
    """复核当前密码后撤销其他会话，并保留当前浏览器会话。"""
    await require_employee_session(request)
    admin_id = cast(int, request.session["admin_id"])
    await _consume_csrf(
        request,
        csrf_token,
        "revoke-sessions",
        admin_id=admin_id,
    )
    if len(password) > 128:
        return await _account_page(
            request,
            must_change_password=False,
            error=AUTHENTICATION_ERROR_MESSAGE,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    try:
        service = _get_auth_service(request)
        version = await service.reverify_and_revoke_sessions(
            admin_id,
            password,
            cast(int, request.session["admin_session_version"]),
        )
    except AuthenticationError:
        return await _account_page(
            request,
            must_change_password=False,
            error=AUTHENTICATION_ERROR_MESSAGE,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    request.session["admin_session_version"] = version
    return RedirectResponse("/employee/account", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout")
async def employee_logout(
    request: Request,
    csrf_token: str = Form(""),
) -> RedirectResponse:
    """校验令牌后清除完整会话并返回登录页。"""
    await require_employee_session(request)
    await _consume_csrf(
        request,
        csrf_token,
        "logout",
        admin_id=cast(int, request.session["admin_id"]),
    )
    request.session.clear()
    return RedirectResponse("/employee/login", status_code=status.HTTP_303_SEE_OTHER)
