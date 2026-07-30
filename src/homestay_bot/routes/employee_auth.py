import secrets
from typing import Protocol, cast
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.domain.models import Employee

router = APIRouter(prefix="/employee")


class EmployeeOAuthPort(Protocol):
    """定义企业微信 OAuth 换取成员身份的接口。"""

    async def get_employee_userid(self, code: str) -> str:
        """用一次性 OAuth code 换取企业成员 userid。"""


class EmployeeRepository(Protocol):
    """定义本地员工授权查询接口。"""

    async def get_active_by_wecom_userid(self, userid: str) -> Employee | None:
        """返回当前启用的本地员工。"""


class EmployeeAuthService:
    """组合企业微信 OAuth 与本地员工角色授权。"""

    def __init__(
        self,
        *,
        corp_id: str,
        oauth: EmployeeOAuthPort,
        employees: EmployeeRepository,
    ) -> None:
        """注入企业 ID、OAuth 客户端和员工仓储。"""
        self._corp_id = corp_id
        self._oauth = oauth
        self._employees = employees

    def authorization_url(self, redirect_uri: str, state: str) -> str:
        """构造企业微信网页授权地址。"""
        query = urlencode(
            {
                "appid": self._corp_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": "snsapi_base",
                "state": state,
            }
        )
        return f"https://open.weixin.qq.com/connect/oauth2/authorize?{query}#wechat_redirect"

    async def authenticate(self, code: str) -> Employee:
        """换取企业成员身份，并拒绝未在本地启用的员工。"""
        userid = await self._oauth.get_employee_userid(code)
        employee = await self._employees.get_active_by_wecom_userid(userid)
        if employee is None:
            raise PermissionError("当前企业微信成员未获系统授权")
        return employee


class EmployeeAuthServicePort(Protocol):
    """定义登录路由所需的认证服务接口。"""

    def authorization_url(self, redirect_uri: str, state: str) -> str:
        """返回企业微信授权链接。"""

    async def authenticate(self, code: str) -> Employee:
        """返回已授权本地员工。"""


class EmployeeAccessVerifier(Protocol):
    """定义请求期间重新验证员工状态的接口。"""

    async def get_active(self, employee_id: int) -> Employee | None:
        """返回仍处于启用状态的员工。"""


async def require_employee_session(
    request: Request,
) -> tuple[int, EmployeeRole]:
    """读取签名会话，并在生产环境重新验证员工状态与最新角色。"""
    employee_id = request.session.get("employee_id")
    role_value = request.session.get("employee_role")
    if not isinstance(employee_id, int):
        raise HTTPException(status_code=401, detail="员工尚未登录")

    verifier = getattr(request.app.state, "employee_access_verifier", None)
    if verifier is not None:
        employee = await cast(EmployeeAccessVerifier, verifier).get_active(
            employee_id
        )
        if employee is None:
            request.session.clear()
            raise HTTPException(status_code=401, detail="员工已停用或不存在")
        role = employee.role
        request.session["employee_role"] = role.value
    else:
        if not isinstance(role_value, str):
            raise HTTPException(status_code=401, detail="员工角色无效")
        try:
            role = EmployeeRole(role_value)
        except ValueError as error:
            raise HTTPException(status_code=401, detail="员工角色无效") from error
    return employee_id, role


def _get_auth_service(request: Request) -> EmployeeAuthServicePort:
    """从应用状态读取员工认证服务。"""
    service = getattr(request.app.state, "employee_auth_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="员工认证服务尚未配置",
        )
    return cast(EmployeeAuthServicePort, service)


@router.get("/login")
async def employee_login(
    request: Request,
    next_path: str = Query("/employee/tasks", alias="next"),
) -> RedirectResponse:
    """生成一次性 OAuth state，并跳转企业微信授权页。"""
    safe_next = next_path if next_path.startswith("/") and not next_path.startswith("//") else "/"
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    request.session["oauth_next"] = safe_next
    redirect_uri = str(request.url_for("employee_oauth_callback"))
    location = _get_auth_service(request).authorization_url(redirect_uri, state)
    return RedirectResponse(location, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/oauth/callback", name="employee_oauth_callback")
async def employee_oauth_callback(
    request: Request,
    code: str,
    state_value: str = Query(alias="state"),
) -> RedirectResponse:
    """校验 OAuth state，并把可信员工身份写入签名会话。"""
    expected_state = request.session.pop("oauth_state", None)
    if expected_state is None or not secrets.compare_digest(expected_state, state_value):
        raise HTTPException(status_code=400, detail="OAuth state 无效或已过期")
    try:
        employee = await _get_auth_service(request).authenticate(code)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error

    request.session["employee_id"] = employee.id
    request.session["employee_role"] = employee.role.value
    next_path = request.session.pop("oauth_next", "/employee/tasks")
    return RedirectResponse(next_path, status_code=status.HTTP_303_SEE_OTHER)
