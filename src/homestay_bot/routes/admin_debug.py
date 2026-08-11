"""提供仅管理员可访问且不触发生产写操作的 AI 调试页面。"""

import logging
from datetime import date
from typing import Annotated, Protocol, cast

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, Response
from fastapi.routing import APIRoute

from homestay_bot.domain.enums import EmployeeRole, Language
from homestay_bot.routes.employee_auth import AdminCsrfServicePort, require_employee_session
from homestay_bot.services.admin_csrf import AdminCsrfCapacityError
from homestay_bot.services.admin_debug_service import (
    DebugPreviewCommand,
    DebugPreviewInputError,
    DebugPreviewRateLimitError,
    DebugPreviewResult,
    DebugProperty,
)
from homestay_bot.web import templates

logger = logging.getLogger(__name__)
DEBUG_CSRF_PURPOSE = "admin-debug-preview"


def _no_store(response: Response) -> Response:
    """禁止浏览器和中间代理缓存问题或模型预览。"""
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


class AdminDebugSafeRoute(APIRoute):
    """统一处理框架校验和权限错误，禁止回显原始表单。"""

    def get_route_handler(self):  # type: ignore[no-untyped-def]
        """为调试路径的所有响应补充 no-store 和安全错误正文。"""
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            """收敛框架异常，保留登录跳转响应头。"""
            try:
                response = await original(request)
            except RequestValidationError:
                response = HTMLResponse(
                    "调试输入格式无效，请刷新页面后重试。",
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                )
            except HTTPException as error:
                response = HTMLResponse(
                    "调试请求未完成，请刷新页面后重试。",
                    status_code=error.status_code,
                    headers=error.headers,
                )
            return _no_store(response)

        return handler


router = APIRouter(
    prefix="/employee/admin/debug",
    route_class=AdminDebugSafeRoute,
)


class AdminDebugServicePort(Protocol):
    """定义调试路由使用的安全服务接口。"""

    async def list_properties(self) -> tuple[DebugProperty, ...]:
        """返回房源安全投影。"""

    async def preview(self, command: DebugPreviewCommand) -> DebugPreviewResult:
        """生成只读模型预览。"""


def _service(request: Request) -> AdminDebugServicePort:
    """读取应用生命周期装配的调试服务。"""
    service = getattr(request.app.state, "admin_debug_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="AI 调试服务尚未配置")
    return cast(AdminDebugServicePort, service)


def _csrf_service(request: Request) -> AdminCsrfServicePort:
    """读取服务端原子 nonce 服务。"""
    service = getattr(request.app.state, "admin_csrf_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="调试表单安全服务尚未配置")
    return cast(AdminCsrfServicePort, service)


async def _admin_context(request: Request) -> tuple[int, int]:
    """持续复核管理员，并读取审计主体与后台凭证编号。"""
    employee_id, role = await require_employee_session(request)
    if role is not EmployeeRole.ADMIN:
        raise HTTPException(status_code=403, detail="只有管理员可以使用 AI 调试")
    admin_id = request.session.get("admin_id")
    if not isinstance(admin_id, int):
        raise HTTPException(status_code=401, detail="管理员会话无效")
    return employee_id, admin_id


async def _issue_csrf(request: Request, admin_id: int) -> str:
    """为每次页面渲染签发用途绑定的一次性 nonce。"""
    try:
        return await _csrf_service(request).issue(DEBUG_CSRF_PURPOSE, admin_id=admin_id)
    except AdminCsrfCapacityError as error:
        raise HTTPException(status_code=429, detail="调试表单请求过于频繁") from error


async def _consume_csrf(request: Request, token: str, admin_id: int) -> None:
    """原子消费 nonce，拒绝伪造和重放。"""
    if not await _csrf_service(request).consume(
        token,
        DEBUG_CSRF_PURPOSE,
        admin_id=admin_id,
    ):
        raise HTTPException(status_code=409, detail="调试表单令牌无效或已使用")


async def _render(
    request: Request,
    *,
    admin_id: int,
    result: object | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> Response:
    """只渲染受控 view model，异常正文和原始输入永不进入模板。"""
    response = templates.TemplateResponse(
        request=request,
        name="admin/debug.html",
        context={
            "page_title": "AI 调试台",
            "active_nav": "debug",
            "properties": await _service(request).list_properties(),
            "csrf_token": await _issue_csrf(request, admin_id),
            "result": result,
            "error": error,
        },
        status_code=status_code,
    )
    return _no_store(response)


@router.get("", response_class=HTMLResponse)
async def debug_page(request: Request) -> Response:
    """展示安全说明与一次性模拟问题表单。"""
    _, admin_id = await _admin_context(request)
    return await _render(request, admin_id=admin_id)


@router.post("", response_class=HTMLResponse)
async def debug_preview(
    request: Request,
    csrf_token: Annotated[str, Form(min_length=1, max_length=256)],
    question: Annotated[str, Form(min_length=1, max_length=1000)],
    language: Annotated[Language, Form()] = Language.ZH,
    property_id: Annotated[int | None, Form(gt=0)] = None,
    check_in_date: Annotated[date | None, Form()] = None,
    check_out_date: Annotated[date | None, Form()] = None,
) -> Response:
    """消费 nonce 后调用只读调试服务，输入错误不回显原文。"""
    employee_id, admin_id = await _admin_context(request)
    await _consume_csrf(request, csrf_token, admin_id)
    try:
        result = await _service(request).preview(
            DebugPreviewCommand(
                actor_employee_id=employee_id,
                admin_id=admin_id,
                question=question,
                language=language,
                property_id=property_id,
                check_in_date=check_in_date,
                check_out_date=check_out_date,
            )
        )
    except DebugPreviewInputError:
        return await _render(
            request,
            admin_id=admin_id,
            error="房间、日期或问题不在允许范围，请检查后重试。",
            status_code=422,
        )
    except DebugPreviewRateLimitError:
        return await _render(
            request,
            admin_id=admin_id,
            error="AI 调试请求过于频繁，请稍后重试。",
            status_code=429,
        )
    except Exception as error:
        logger.warning("管理员 AI 调试失败：error_type=%s", type(error).__name__)
        return await _render(
            request,
            admin_id=admin_id,
            error="AI 调试暂时不可用，请稍后重试。",
            status_code=503,
        )
    return await _render(request, admin_id=admin_id, result=result)
