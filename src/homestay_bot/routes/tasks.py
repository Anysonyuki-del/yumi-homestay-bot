import secrets
from datetime import date
from pathlib import Path
from typing import Any, Protocol, cast

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.domain.models import BusinessTask, Employee
from homestay_bot.routes.employee_auth import require_employee_session

router = APIRouter(prefix="/employee/tasks")
templates = Jinja2Templates(
    directory=Path(__file__).resolve().parent.parent / "templates"
)


class TaskPageServicePort(Protocol):
    """定义任务路由所需的页面服务。"""

    async def list_for(self, employee: Employee) -> list[BusinessTask]:
        """返回当前员工可见任务。"""

    async def detail_for(
        self,
        task_id: int,
        employee: Employee,
    ) -> dict[str, object]:
        """返回安全任务详情。"""

    async def transition(
        self,
        task_id: int,
        employee: Employee,
        target: str,
    ) -> BusinessTask:
        """推进任务状态。"""

    async def assign(
        self,
        task_id: int,
        employee: Employee,
        *,
        assigned_employee_id: int,
        property_id: int,
        service_date: date,
    ) -> BusinessTask:
        """由管理员分派任务。"""

    async def assignment_options(self) -> dict[str, list[Any]]:
        """返回员工和房间选项。"""


def _get_service(request: Request) -> TaskPageServicePort:
    """从应用状态读取任务页面服务。"""
    service = getattr(request.app.state, "task_page_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="任务服务尚未配置")
    return cast(TaskPageServicePort, service)


async def _current_employee(request: Request) -> Employee:
    """把持续复核后的签名会话转换为最小员工对象。"""
    employee_id, role = await require_employee_session(request)
    return Employee(
        id=employee_id,
        wecom_userid="",
        name="",
        role=role,
        is_active=True,
    )


def _issue_csrf(request: Request, task_id: int) -> str:
    """为单个任务写操作签发一次性令牌。"""
    token = secrets.token_urlsafe(24)
    tokens = dict(request.session.get("task_csrf", {}))
    tokens[str(task_id)] = token
    request.session["task_csrf"] = tokens
    return token


def _consume_csrf(request: Request, task_id: int, token: str) -> None:
    """校验并立即消耗任务一次性 CSRF 令牌。"""
    tokens = dict(request.session.get("task_csrf", {}))
    expected = tokens.pop(str(task_id), None)
    request.session["task_csrf"] = tokens
    if not isinstance(expected, str) or not secrets.compare_digest(
        expected,
        token,
    ):
        raise HTTPException(status_code=409, detail="表单令牌无效或已使用")


def _raise_page_error(error: Exception) -> None:
    """把页面服务领域异常转换为稳定 HTTP 状态。"""
    if isinstance(error, PermissionError):
        raise HTTPException(status_code=403, detail=str(error)) from error
    if isinstance(error, LookupError):
        raise HTTPException(status_code=404, detail=str(error)) from error
    raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("", response_class=HTMLResponse)
async def task_index(request: Request) -> Response:
    """展示管理员全部待办或员工自己的任务。"""
    try:
        employee = await _current_employee(request)
    except HTTPException:
        return RedirectResponse(
            "/employee/login?next=/employee/tasks",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    items = await _get_service(request).list_for(employee)
    return templates.TemplateResponse(
        request=request,
        name="tasks/index.html",
        context={
            "tasks": items,
            "is_admin": employee.role is EmployeeRole.ADMIN,
        },
    )


@router.get("/{task_id}", response_class=HTMLResponse)
async def task_detail(request: Request, task_id: int) -> Response:
    """展示不含客户电话、金额、完整地址和凭证的任务详情。"""
    try:
        employee = await _current_employee(request)
        detail = await _get_service(request).detail_for(task_id, employee)
    except HTTPException:
        raise
    except Exception as error:
        _raise_page_error(error)
    options: dict[str, list[Any]] = {
        "employees": [],
        "properties": [],
    }
    if employee.role is EmployeeRole.ADMIN:
        options = await _get_service(request).assignment_options()
    return templates.TemplateResponse(
        request=request,
        name="tasks/detail.html",
        context={
            **detail,
            **options,
            "is_admin": employee.role is EmployeeRole.ADMIN,
            "csrf_token": _issue_csrf(request, task_id),
        },
    )


@router.post("/{task_id}/transition")
async def transition_task(
    request: Request,
    task_id: int,
    target: str = Form(),
    csrf_token: str = Form(),
) -> RedirectResponse:
    """校验一次性令牌并推进当前员工可操作的任务。"""
    employee = await _current_employee(request)
    _consume_csrf(request, task_id, csrf_token)
    try:
        await _get_service(request).transition(task_id, employee, target)
    except Exception as error:
        _raise_page_error(error)
    return RedirectResponse(
        f"/employee/tasks/{task_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{task_id}/assign")
async def assign_task(
    request: Request,
    task_id: int,
    assigned_employee_id: int = Form(),
    property_id: int = Form(),
    service_date_value: str = Form(alias="service_date"),
    csrf_token: str = Form(),
) -> RedirectResponse:
    """只允许管理员补齐执行信息并分派任务。"""
    employee = await _current_employee(request)
    _consume_csrf(request, task_id, csrf_token)
    try:
        await _get_service(request).assign(
            task_id,
            employee,
            assigned_employee_id=assigned_employee_id,
            property_id=property_id,
            service_date=date.fromisoformat(service_date_value),
        )
    except Exception as error:
        _raise_page_error(error)
    return RedirectResponse(
        f"/employee/tasks/{task_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
