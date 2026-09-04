import logging
import secrets
from datetime import date
from typing import Annotated, Any, Protocol, cast
from urllib.parse import urlencode

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BeforeValidator, Field

from homestay_bot.domain.enums import (
    BusinessTaskStatus,
    BusinessTaskType,
    EmployeeRole,
)
from homestay_bot.domain.models import BusinessTask, Employee
from homestay_bot.routes.admin_form_csrf import (
    TASK_CSRF_FAMILY,
    consume_form_csrf,
    drop_legacy_session_key,
    issue_form_csrf,
)
from homestay_bot.routes.employee_auth import require_employee_session
from homestay_bot.routes.query_params import empty_query_to_none
from homestay_bot.services.task_page_service import TaskFilters
from homestay_bot.web import templates

router = APIRouter(prefix="/employee/tasks")
logger = logging.getLogger(__name__)
PositiveQueryId = Annotated[int, Field(ge=1)]


class TaskPageServicePort(Protocol):
    """定义任务路由所需的页面服务。"""

    async def list_for(
        self,
        employee: Employee,
        *,
        offset: int,
        limit: int,
        filters: TaskFilters | None = None,
    ) -> list[Any]:
        """分页返回当前员工可见任务。"""

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

    async def update_checklist(
        self,
        task_id: int,
        employee: Employee,
        checklist: dict[str, bool],
    ) -> BusinessTask:
        """保存执行员工提交的检查清单。"""

    async def upload_photo(
        self,
        task_id: int,
        employee: Employee,
        stream: Any,
        content_type: str,
    ) -> object:
        """保存执行员工上传的现场照片。"""

    async def archive(self, task_id: int, employee: Employee) -> None:
        """把单条终态任务移入归档。"""

    async def restore(self, task_id: int, employee: Employee) -> None:
        """把任务移出归档。"""

    async def archive_filtered(
        self,
        employee: Employee,
        filters: TaskFilters,
    ) -> int:
        """按筛选条件批量归档，返回归档数量。"""

    async def mark_ready(self, task_id: int, employee: Employee) -> object:
        """把具备完整证据的房间标记为可入住。"""

    async def revoke_ready(self, task_id: int, employee: Employee) -> object:
        """由管理员撤回任务关联房间的可入住状态。"""


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


async def _issue_csrf(request: Request, task_id: int) -> str:
    """为单个任务写操作签发服务端一次性令牌。"""
    drop_legacy_session_key(request, "task_csrf")
    return await issue_form_csrf(
        request,
        family=TASK_CSRF_FAMILY,
        entity_id=task_id,
    )


_BULK_CSRF_ENTITY = 0


async def _consume_csrf(request: Request, task_id: int, token: str) -> None:
    """校验并原子消费任务一次性令牌；令牌绑定该任务，跨任务重放必然失败。"""
    await consume_form_csrf(
        request,
        family=TASK_CSRF_FAMILY,
        entity_id=task_id,
        token=token,
    )


def _raise_page_error(error: Exception) -> None:
    """把页面服务领域异常转换为稳定 HTTP 状态。"""
    if isinstance(error, PermissionError):
        raise HTTPException(
            status_code=403,
            detail="没有权限执行任务操作",
        ) from error
    if isinstance(error, LookupError):
        raise HTTPException(
            status_code=404,
            detail="任务不存在或不可见",
        ) from error
    # 未知异常只记录类型和内部追踪号，页面不得回显异常原文。
    trace_id = secrets.token_hex(8)
    logger.error(
        "任务页面操作失败：error_type=%s trace_id=%s",
        type(error).__name__,
        trace_id,
    )
    raise HTTPException(
        status_code=409,
        detail="任务操作未完成",
    ) from error


@router.get("", response_class=HTMLResponse)
async def task_index(
    request: Request,
    page: int = Query(1, ge=1, le=10_000),
    status_filter: Annotated[
        BusinessTaskStatus | None,
        BeforeValidator(empty_query_to_none),
    ] = None,
    task_type: Annotated[
        BusinessTaskType | None,
        BeforeValidator(empty_query_to_none),
    ] = None,
    service_date: Annotated[
        date | None,
        BeforeValidator(empty_query_to_none),
    ] = None,
    property_id: Annotated[
        PositiveQueryId | None,
        BeforeValidator(empty_query_to_none),
    ] = None,
    assigned_employee_id: Annotated[
        PositiveQueryId | None,
        BeforeValidator(empty_query_to_none),
    ] = None,
    overdue: bool = False,
    archived: bool = False,
) -> Response:
    """展示管理员全部待办或员工自己的任务。"""
    employee = await _current_employee(request)
    try:
        selected_filters = TaskFilters(
            status=status_filter,
            task_type=task_type,
            service_date=service_date,
            property_id=property_id,
            assigned_employee_id=(
                assigned_employee_id
                if employee.role is EmployeeRole.ADMIN
                else None
            ),
            overdue=overdue,
            archived=archived,
        )
        items = await _get_service(request).list_for(
            employee,
            offset=(page - 1) * 50,
            limit=51,
            filters=selected_filters,
        )
        options = await _get_service(request).assignment_options()
    except Exception as error:
        _raise_page_error(error)
    params = {
        "status_filter": status_filter.value if status_filter else "",
        "task_type": task_type.value if task_type else "",
        "service_date": service_date.isoformat() if service_date else "",
        "property_id": str(property_id) if property_id else "",
        "assigned_employee_id": (
            str(assigned_employee_id) if assigned_employee_id else ""
        ),
        "overdue": "true" if overdue else "",
        "archived": "true" if archived else "",
    }
    active_params = {key: value for key, value in params.items() if value}

    def page_url(target_page: int) -> str:
        """生成保留当前筛选条件的稳定分页链接。"""
        query_string = urlencode({**active_params, "page": target_page})
        return f"/employee/tasks?{query_string}"

    return templates.TemplateResponse(
        request=request,
        name="tasks/index.html",
        context={
            "tasks": items[:50],
            "is_admin": employee.role is EmployeeRole.ADMIN,
            "page": page,
            "previous_url": page_url(page - 1) if page > 1 else None,
            "next_url": page_url(page + 1) if len(items) > 50 else None,
            "filters": selected_filters,
            "task_statuses": list(BusinessTaskStatus),
            "bulk_csrf_token": await _issue_csrf(request, _BULK_CSRF_ENTITY),
            "task_types": list(BusinessTaskType),
            "properties": options.get("properties", []),
            "employees": options.get("employees", []),
            "page_title": (
                "全部待办任务"
                if employee.role is EmployeeRole.ADMIN
                else "自己的任务"
            ),
            "active_nav": "tasks",
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
        try:
            options = await _get_service(request).assignment_options()
        except Exception as error:
            _raise_page_error(error)
    return templates.TemplateResponse(
        request=request,
        name="tasks/detail.html",
        context={
            **detail,
            **options,
            "is_admin": employee.role is EmployeeRole.ADMIN,
            # 现场证据与确认可入住由服务端按「是否本任务执行员工」判断，与角色无关，
            # 详见 require_evidence_editor 与 room_readiness_service.mark_ready。
            "is_assignee": (
                cast(BusinessTask, detail["task"]).assigned_employee_id
                == employee.id
            ),
            "csrf_token": await _issue_csrf(request, task_id),
            "page_title": f"任务 #{task_id}",
            "active_nav": "tasks",
        },
    )


@router.post("/archive-filtered")
async def archive_filtered_tasks(
    request: Request,
    csrf_token: str = Form(min_length=1, max_length=128),
    status_filter: Annotated[
        BusinessTaskStatus | None,
        BeforeValidator(empty_query_to_none),
        Form(),
    ] = None,
    task_type: Annotated[
        BusinessTaskType | None,
        BeforeValidator(empty_query_to_none),
        Form(),
    ] = None,
    property_id: Annotated[
        int | None,
        BeforeValidator(empty_query_to_none),
        Form(),
    ] = None,
) -> RedirectResponse:
    """按当前筛选条件批量归档终态任务。

    筛选条件即选择范围：不引入多选提交，列表页既有筛选器就是最自然的选择方式。
    """
    employee = await _current_employee(request)
    await _consume_csrf(request, _BULK_CSRF_ENTITY, csrf_token)
    filters = TaskFilters(
        status=status_filter,
        task_type=task_type,
        property_id=property_id,
    )
    try:
        await _get_service(request).archive_filtered(employee, filters)
    except Exception as error:
        _raise_page_error(error)
    return RedirectResponse(
        "/employee/tasks?archived=true",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{task_id}/archive")
async def archive_task(
    request: Request,
    task_id: int,
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """管理员把单条终态任务移入归档。"""
    employee = await _current_employee(request)
    await _consume_csrf(request, task_id, csrf_token)
    try:
        await _get_service(request).archive(task_id, employee)
    except Exception as error:
        _raise_page_error(error)
    return RedirectResponse(
        f"/employee/tasks/{task_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{task_id}/restore")
async def restore_task(
    request: Request,
    task_id: int,
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """管理员把任务移出归档，状态本身不变。"""
    employee = await _current_employee(request)
    await _consume_csrf(request, task_id, csrf_token)
    try:
        await _get_service(request).restore(task_id, employee)
    except Exception as error:
        _raise_page_error(error)
    return RedirectResponse(
        f"/employee/tasks/{task_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{task_id}/transition")
async def transition_task(
    request: Request,
    task_id: int,
    target: str = Form(min_length=1, max_length=32),
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """校验一次性令牌并推进当前员工可操作的任务。"""
    employee = await _current_employee(request)
    await _consume_csrf(request, task_id, csrf_token)
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
    service_date_value: str = Form(
        min_length=10,
        max_length=10,
        alias="service_date",
    ),
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """只允许管理员补齐执行信息并分派任务。"""
    employee = await _current_employee(request)
    await _consume_csrf(request, task_id, csrf_token)
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


@router.post("/{task_id}/checklist")
async def update_task_checklist(
    request: Request,
    task_id: int,
    clean: bool = Form(False),
    supplies: bool = Form(False),
    damage: bool = Form(False),
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """校验执行权限后保存三项房间检查结果。"""
    employee = await _current_employee(request)
    await _consume_csrf(request, task_id, csrf_token)
    try:
        await _get_service(request).update_checklist(
            task_id,
            employee,
            {
                "clean": clean,
                "supplies": supplies,
                "damage": damage,
            },
        )
    except Exception as error:
        _raise_page_error(error)
    return RedirectResponse(
        f"/employee/tasks/{task_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{task_id}/photos")
async def upload_task_photo(
    request: Request,
    task_id: int,
    photo: Annotated[UploadFile, File()],
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """校验执行权限后把现场照片存入私有目录。"""
    employee = await _current_employee(request)
    await _consume_csrf(request, task_id, csrf_token)
    try:
        await _get_service(request).upload_photo(
            task_id,
            employee,
            photo.file,
            photo.content_type or "application/octet-stream",
        )
    except Exception as error:
        _raise_page_error(error)
    finally:
        await photo.close()
    return RedirectResponse(
        f"/employee/tasks/{task_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{task_id}/ready")
async def mark_room_ready(
    request: Request,
    task_id: int,
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """允许任务执行员工在证据完整后标记房间可入住。"""
    employee = await _current_employee(request)
    await _consume_csrf(request, task_id, csrf_token)
    try:
        await _get_service(request).mark_ready(task_id, employee)
    except Exception as error:
        _raise_page_error(error)
    return RedirectResponse(
        f"/employee/tasks/{task_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{task_id}/revoke-ready")
async def revoke_room_ready(
    request: Request,
    task_id: int,
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """允许管理员把任务关联房间撤回待检查。"""
    employee = await _current_employee(request)
    await _consume_csrf(request, task_id, csrf_token)
    try:
        await _get_service(request).revoke_ready(task_id, employee)
    except Exception as error:
        _raise_page_error(error)
    return RedirectResponse(
        f"/employee/tasks/{task_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
