import logging
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, Protocol, cast
from urllib.parse import quote, urlencode

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
    ARCHIVABLE_TASK_STATUSES,
    BusinessTaskStatus,
    BusinessTaskType,
    EmployeeRole,
)
from homestay_bot.domain.errors import OperationRefused
from homestay_bot.domain.models import BusinessTask, Employee
from homestay_bot.routes.admin_form_csrf import (
    TASK_CSRF_FAMILY,
    consume_form_csrf,
    drop_legacy_session_key,
    issue_form_csrf,
)
from homestay_bot.routes.employee_auth import require_employee_session
from homestay_bot.routes.page_errors import raise_page_error, safe_return_path
from homestay_bot.routes.query_params import empty_query_to_none
from homestay_bot.services.room_readiness_service import (
    REQUIRED_READINESS_CHECKS,
)
from homestay_bot.services.task_page_service import TaskFilters
from homestay_bot.web import templates

router = APIRouter(prefix="/employee/tasks")
logger = logging.getLogger(__name__)
PositiveQueryId = Annotated[int, Field(ge=1)]


# 任务中心的「队列」和「附加筛选」是两件事：队列回答「我现在在看哪一批」，
# 附加筛选回答「在这一批里再缩小到哪些」。同一个 URL 可能同时带上归档、逾期和
# 状态，若每个条件各自决定高亮，页面上会同时亮起多个队列，标题也说不清位置。
# 这里给出唯一的队列判定，让高亮、标题和「清除附加筛选」都从同一个答案出发。
_QUEUE_STATUSES = (
    BusinessTaskStatus.PENDING_CONFIRMATION,
    BusinessTaskStatus.PENDING_ASSIGNMENT,
)


@dataclass(frozen=True, slots=True)
class _TaskQueue:
    """描述一个队列的入口地址，以及它在两种角色下的称呼。

    管理员看的是全部任务，员工看的只有分配给自己的那些；同一个队列因此需要两
    套说法，否则员工会以为「逾期」是全公司的逾期。
    """

    url: str
    admin_title: str
    admin_heading: str
    staff_title: str
    staff_heading: str

    def title(self, *, is_admin: bool) -> str:
        """返回浏览器标签页使用的完整称呼。"""
        return self.admin_title if is_admin else self.staff_title

    def heading(self, *, is_admin: bool) -> str:
        """返回页面主标题使用的简短称呼。"""
        return self.admin_heading if is_admin else self.staff_heading


_QUEUES = {
    "archived": _TaskQueue(
        "/employee/tasks?archived=true",
        "已归档任务",
        "已归档",
        "已归档任务",
        "已归档",
    ),
    "overdue": _TaskQueue(
        "/employee/tasks?overdue=true",
        "逾期任务",
        "逾期",
        "我的逾期任务",
        "我的逾期任务",
    ),
    "pending_confirmation": _TaskQueue(
        "/employee/tasks?status_filter=pending_confirmation",
        "待确认任务",
        "待确认",
        "我的待确认任务",
        "我的待确认任务",
    ),
    "pending_assignment": _TaskQueue(
        "/employee/tasks?status_filter=pending_assignment",
        "待分派任务",
        "待分派",
        "我的待分派任务",
        "我的待分派任务",
    ),
    "open": _TaskQueue(
        "/employee/tasks",
        "全部待办任务",
        "全部待办",
        "自己的任务",
        "分配给我的任务",
    ),
}


def _current_queue(filters: TaskFilters) -> str:
    """按固定优先级定出当前所在的唯一队列。

    归档是与开放任务互斥的另一维度，因此排在最前；其余按「哪一条最能说明用户
    此刻在看什么」排序。判定放在这里而不是模板，是为了能被单独测试。
    """
    if filters.archived:
        return "archived"
    if filters.overdue:
        return "overdue"
    if filters.status in _QUEUE_STATUSES:
        return filters.status.value
    return "open"


def _extra_filters(filters: TaskFilters, queue: str) -> tuple[str, ...]:
    """列出队列本身之外仍然生效的筛选条件。

    定义队列的那个条件不算「附加」，否则点一下「待分派」就会被当成加了筛选，
    把整套高级表单展开在用户面前。
    """
    extras: list[str] = []
    if filters.status is not None and filters.status.value != queue:
        extras.append("status")
    if filters.task_type is not None:
        extras.append("task_type")
    if filters.service_date is not None:
        extras.append("service_date")
    if filters.property_id is not None:
        extras.append("property_id")
    if filters.assigned_employee_id is not None:
        extras.append("assigned_employee_id")
    if filters.overdue and queue != "overdue":
        extras.append("overdue")
    if filters.archived and queue != "archived":
        extras.append("archived")
    return tuple(extras)


def _missing_readiness_evidence(
    task: BusinessTask,
    attachments: list[Any],
) -> list[str]:
    """列出距离「可标记可入住」还缺的材料，供页面提前说明。

    只做展示，不替代服务端守卫：真正的判定仍在 RoomReadinessService.mark_ready，
    这里与它共用同一份必填清单定义，避免页面说齐了而提交仍被拒绝。
    """
    missing = [
        label
        for key, label in REQUIRED_READINESS_CHECKS
        if task.checklist.get(key) is not True
    ]
    if not attachments:
        missing.append("至少一张现场照片")
    return missing


def _detail_redirect(task_id: int, return_to: str) -> RedirectResponse:
    """回到详情页并把来源一起带回去。

    详情 GET 早就接受 return_to，但操作表单不透传它，于是每提交一次就把管家
    丢回裸列表，房间、状态、日期和页码全部重来。这里在 PRG 的重定向上补回
    来源；路径仍经 safe_return_path 校验，不接受站外目标。
    """
    target = f"/employee/tasks/{task_id}"
    if return_to:
        target = f"{target}?return_to={quote(safe_return_path(return_to), safe='')}"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


def _raise_page_error(error: Exception) -> None:
    """把任务页面领域异常转换为稳定 HTTP 状态。"""
    raise_page_error(
        error,
        forbidden="没有权限执行任务操作",
        not_found="任务不存在或不可见",
        unknown="任务操作未完成",
        log_subject="任务页面操作失败",
    )


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

    async def create_manual(
        self,
        employee: Employee,
        *,
        task_type: BusinessTaskType,
        property_id: int,
        service_date: date,
        description: str,
        assigned_employee_id: int | None = None,
    ) -> BusinessTask:
        """管理员手动创建任务。"""

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

    async def purge(self, task_id: int, employee: Employee) -> None:
        """永久删除一条已归档任务。"""

    async def assign_many(
        self,
        task_ids: list[int],
        employee: Employee,
        assigned_employee_id: int,
    ) -> int:
        """批量分派给同一名执行员工，返回分派数量。"""

    async def cancel_many(
        self,
        task_ids: list[int],
        employee: Employee,
    ) -> int:
        """批量取消开放态任务，返回取消数量。"""

    async def purge_many(
        self,
        task_ids: list[int],
        employee: Employee,
    ) -> int:
        """永久删除勾选的已归档任务，返回删除数量。"""

    async def archive_many(
        self,
        employee: Employee,
        task_ids: list[int],
    ) -> int:
        """按显式勾选归档，返回归档数量。"""

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
# 建单表单没有任务 id，用独立 entity，与批量(0)及任何任务(正数)都不撞。
_CREATE_CSRF_ENTITY = -1


async def _consume_csrf(request: Request, task_id: int, token: str) -> None:
    """校验并原子消费任务一次性令牌；令牌绑定该任务，跨任务重放必然失败。"""
    await consume_form_csrf(
        request,
        family=TASK_CSRF_FAMILY,
        entity_id=task_id,
        token=token,
    )



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

    queue = _current_queue(selected_filters)
    is_admin = employee.role is EmployeeRole.ADMIN

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
            # 批量与勾选归档是管理员能力；给员工签发只会白占令牌额度。
            "bulk_csrf_token": (
                await _issue_csrf(request, _BULK_CSRF_ENTITY)
                if employee.role is EmployeeRole.ADMIN
                else ""
            ),
            "create_csrf_token": (
                await _issue_csrf(request, _CREATE_CSRF_ENTITY)
                if employee.role is EmployeeRole.ADMIN
                else ""
            ),
            "archivable_statuses": ARCHIVABLE_TASK_STATUSES,
            # 可取消 == 非终态。判定放在这里而不是模板：它与 cancel_many 的
            # 校验必须是同一条规则，写进模板就没法和服务端一起被测试锁住。
            "cancellable_count": sum(
                1
                for item in items[:50]
                if item.status not in ARCHIVABLE_TASK_STATUSES
            ),
            # 可批量分派的条件放在这里判定而不是模板里：规则含状态与两个字段，
            # 写进模板既难读也无法单独测试。
            "assignable_ids": {
                item.id
                for item in items[:50]
                if item.status
                in (
                    BusinessTaskStatus.PENDING_CONFIRMATION,
                    BusinessTaskStatus.PENDING_ASSIGNMENT,
                )
                and item.property_id is not None
                and item.service_date is not None
            },
            "task_types": list(BusinessTaskType),
            "properties": options.get("properties", []),
            "employees": options.get("employees", []),
            "queue": queue,
            "queue_heading": _QUEUES[queue].heading(is_admin=is_admin),
            "queue_url": _QUEUES[queue].url,
            "extra_filters": _extra_filters(selected_filters, queue),
            "page_title": _QUEUES[queue].title(is_admin=is_admin),
            "active_nav": "tasks",
        },
    )


@router.get("/{task_id}", response_class=HTMLResponse)
async def task_detail(
    request: Request,
    task_id: int,
    return_to: Annotated[str, Query(max_length=200)] = "",
) -> Response:
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
            "archivable_statuses": ARCHIVABLE_TASK_STATUSES,
            "csrf_token": await _issue_csrf(request, task_id),
            # 回跳路径必须经过校验：它来自查询串，未经校验就是开放重定向。
            "return_to": safe_return_path(return_to),
            # 「还缺什么」在提交之前就说清楚：此前执行员工要先点「确认可入住」
            # 被拒绝，才知道少了哪一项。判据与 mark_ready 的守卫同源。
            "missing_evidence": _missing_readiness_evidence(
                cast(BusinessTask, detail["task"]),
                cast(list[Any], detail.get("attachments") or []),
            ),
            "page_title": f"任务 #{task_id}",
            "active_nav": "tasks",
        },
    )


@router.post("/purge-selected")
async def purge_selected_tasks(
    request: Request,
    csrf_token: str = Form(min_length=1, max_length=128),
    confirm_count: Annotated[int, Form()] = 0,
    task_ids: Annotated[list[int] | None, Form()] = None,
) -> RedirectResponse:
    """永久删除勾选的已归档任务；不可恢复。

    要求提交的确认数字与实际勾选数一致：这挡住的是「习惯性点确定」这一整类
    事故，提交者必须真的看过数量。
    """
    employee = await _current_employee(request)
    await _consume_csrf(request, _BULK_CSRF_ENTITY, csrf_token)
    selected = task_ids or []
    if confirm_count != len(selected):
        raise OperationRefused(
            f"确认数字与勾选数量不一致：勾选了 {len(selected)} 条，"
            f"确认输入 {confirm_count}。请重新确认后再删除。"
        )
    try:
        await _get_service(request).purge_many(selected, employee)
    except Exception as error:
        _raise_page_error(error)
    return RedirectResponse(
        "/employee/tasks?archived=true",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/assign-selected")
async def assign_selected_tasks(
    request: Request,
    csrf_token: str = Form(min_length=1, max_length=128),
    assigned_employee_id: Annotated[int, Form()] = 0,
    task_ids: Annotated[list[int] | None, Form()] = None,
) -> RedirectResponse:
    """把勾选的任务批量分派给同一名执行员工。"""
    employee = await _current_employee(request)
    await _consume_csrf(request, _BULK_CSRF_ENTITY, csrf_token)
    if assigned_employee_id <= 0:
        raise OperationRefused("请先选择要分派给哪位员工")
    try:
        await _get_service(request).assign_many(
            task_ids or [],
            employee,
            assigned_employee_id,
        )
    except Exception as error:
        _raise_page_error(error)
    return RedirectResponse(
        "/employee/tasks",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/cancel-selected")
async def cancel_selected_tasks(
    request: Request,
    csrf_token: str = Form(min_length=1, max_length=128),
    return_to: Annotated[str, Form(max_length=200)] = "",
    confirm_count: Annotated[int, Form(ge=0)] = 0,
    task_ids: Annotated[list[int] | None, Form()] = None,
) -> RedirectResponse:
    """把勾选的开放态任务批量取消。

    取消不可逆，因此与永久删除同样要求手输条数：数字对不上说明操作者没有真正
    看过数量，直接拒绝。脚本不可用时提交的确认数为 0，同样被这里挡下。
    """
    employee = await _current_employee(request)
    await _consume_csrf(request, _BULK_CSRF_ENTITY, csrf_token)
    selected = task_ids or []
    if confirm_count != len(selected) or not selected:
        raise OperationRefused(
            f"已勾选 {len(selected)} 条，确认输入 {confirm_count}。请重新确认后再取消。",
            return_to=return_to,
        )
    try:
        await _get_service(request).cancel_many(selected, employee)
    except OperationRefused as refused:
        refused.return_to = return_to
        raise
    except Exception as error:
        _raise_page_error(error)
    return RedirectResponse(
        return_to or "/employee/tasks",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/archive-selected")
async def archive_selected_tasks(
    request: Request,
    csrf_token: str = Form(min_length=1, max_length=128),
    return_to: Annotated[str, Form(max_length=200)] = "",
    task_ids: Annotated[list[int] | None, Form()] = None,
) -> RedirectResponse:
    """按勾选归档任务；混入开放态任务时整批拒绝。"""
    employee = await _current_employee(request)
    await _consume_csrf(request, _BULK_CSRF_ENTITY, csrf_token)
    try:
        await _get_service(request).archive_many(employee, task_ids or [])
    except OperationRefused as refused:
        refused.return_to = return_to
        raise
    except Exception as error:
        _raise_page_error(error)
    return RedirectResponse(
        "/employee/tasks?archived=true",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/archive-filtered")
async def archive_filtered_tasks(
    request: Request,
    csrf_token: str = Form(min_length=1, max_length=128),
    return_to: Annotated[str, Form(max_length=200)] = "",
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
    service_date: Annotated[
        date | None,
        BeforeValidator(empty_query_to_none),
        Form(),
    ] = None,
    property_id: Annotated[
        int | None,
        BeforeValidator(empty_query_to_none),
        Form(),
    ] = None,
    assigned_employee_id: Annotated[
        int | None,
        BeforeValidator(empty_query_to_none),
        Form(),
    ] = None,
    overdue: Annotated[bool, Form()] = False,
) -> RedirectResponse:
    """按当前筛选条件批量归档终态任务。

    筛选条件即选择范围：不引入多选提交，列表页既有筛选器就是最自然的选择方式。
    正因为范围由筛选决定，列表用到的每个条件都必须原样传到归档；少传一个，
    实际归档的就是比用户看到的更宽的一批任务。
    """
    employee = await _current_employee(request)
    await _consume_csrf(request, _BULK_CSRF_ENTITY, csrf_token)
    if overdue:
        # 归档查询表达不了「逾期」，与其按更宽的范围执行，不如直接拒绝。
        raise OperationRefused(
            "「只看逾期」不能作为批量归档的范围，请先取消该条件再归档",
            return_to=return_to,
        )
    filters = TaskFilters(
        status=status_filter,
        task_type=task_type,
        service_date=service_date,
        property_id=property_id,
        assigned_employee_id=assigned_employee_id,
    )
    try:
        await _get_service(request).archive_filtered(employee, filters)
    except OperationRefused as refused:
        refused.return_to = return_to
        raise
    except Exception as error:
        _raise_page_error(error)
    return RedirectResponse(
        "/employee/tasks?archived=true",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/create")
async def create_manual_task(
    request: Request,
    task_type: Annotated[BusinessTaskType, Form()],
    property_id: Annotated[int, Form(ge=1)],
    service_date_value: Annotated[str, Form(min_length=10, max_length=10, alias="service_date")],
    description: Annotated[str, Form(min_length=1, max_length=2000)],
    csrf_token: str = Form(min_length=1, max_length=128),
    assigned_employee_id: Annotated[int | None, Form(ge=1)] = None,
) -> RedirectResponse:
    """管理员手动创建一条运营任务并跳转到新任务详情。"""
    employee = await _current_employee(request)
    await _consume_csrf(request, _CREATE_CSRF_ENTITY, csrf_token)
    try:
        task = await _get_service(request).create_manual(
            employee,
            task_type=task_type,
            property_id=property_id,
            service_date=date.fromisoformat(service_date_value),
            description=description,
            assigned_employee_id=assigned_employee_id,
        )
    except Exception as error:
        _raise_page_error(error)
    return RedirectResponse(
        f"/employee/tasks/{task.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{task_id}/archive")
async def archive_task(
    request: Request,
    task_id: int,
    csrf_token: str = Form(min_length=1, max_length=128),
    return_to: Annotated[str, Form(max_length=200)] = "",
) -> RedirectResponse:
    """管理员把单条终态任务移入归档。"""
    employee = await _current_employee(request)
    await _consume_csrf(request, task_id, csrf_token)
    try:
        await _get_service(request).archive(task_id, employee)
    except Exception as error:
        _raise_page_error(error)
    return _detail_redirect(task_id, return_to)


@router.post("/{task_id}/purge")
async def purge_task(
    request: Request,
    task_id: int,
    csrf_token: str = Form(min_length=1, max_length=128),
    return_to: Annotated[str, Form(max_length=200)] = "",
) -> RedirectResponse:
    """永久删除一条已归档任务；不可恢复。"""
    employee = await _current_employee(request)
    await _consume_csrf(request, task_id, csrf_token)
    try:
        await _get_service(request).purge(task_id, employee)
    except Exception as error:
        _raise_page_error(error)
    # 任务已不存在，回来源列表而不是回详情页；没有来源时才落到归档视图。
    return RedirectResponse(
        safe_return_path(return_to) if return_to else "/employee/tasks?archived=true",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{task_id}/restore")
async def restore_task(
    request: Request,
    task_id: int,
    csrf_token: str = Form(min_length=1, max_length=128),
    return_to: Annotated[str, Form(max_length=200)] = "",
) -> RedirectResponse:
    """管理员把任务移出归档，状态本身不变。"""
    employee = await _current_employee(request)
    await _consume_csrf(request, task_id, csrf_token)
    try:
        await _get_service(request).restore(task_id, employee)
    except Exception as error:
        _raise_page_error(error)
    return _detail_redirect(task_id, return_to)


@router.post("/{task_id}/transition")
async def transition_task(
    request: Request,
    task_id: int,
    target: str = Form(min_length=1, max_length=32),
    csrf_token: str = Form(min_length=1, max_length=128),
    return_to: Annotated[str, Form(max_length=200)] = "",
) -> RedirectResponse:
    """校验一次性令牌并推进当前员工可操作的任务。"""
    employee = await _current_employee(request)
    await _consume_csrf(request, task_id, csrf_token)
    try:
        await _get_service(request).transition(task_id, employee, target)
    except Exception as error:
        _raise_page_error(error)
    return _detail_redirect(task_id, return_to)


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
    return_to: Annotated[str, Form(max_length=200)] = "",
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
    return _detail_redirect(task_id, return_to)


@router.post("/{task_id}/checklist")
async def update_task_checklist(
    request: Request,
    task_id: int,
    clean: bool = Form(False),
    supplies: bool = Form(False),
    damage: bool = Form(False),
    csrf_token: str = Form(min_length=1, max_length=128),
    return_to: Annotated[str, Form(max_length=200)] = "",
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
    return _detail_redirect(task_id, return_to)


@router.post("/{task_id}/photos")
async def upload_task_photo(
    request: Request,
    task_id: int,
    photo: Annotated[UploadFile, File()],
    csrf_token: str = Form(min_length=1, max_length=128),
    return_to: Annotated[str, Form(max_length=200)] = "",
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
    return _detail_redirect(task_id, return_to)


@router.post("/{task_id}/ready")
async def mark_room_ready(
    request: Request,
    task_id: int,
    csrf_token: str = Form(min_length=1, max_length=128),
    return_to: Annotated[str, Form(max_length=200)] = "",
) -> RedirectResponse:
    """允许任务执行员工在证据完整后标记房间可入住。"""
    employee = await _current_employee(request)
    await _consume_csrf(request, task_id, csrf_token)
    try:
        await _get_service(request).mark_ready(task_id, employee)
    except Exception as error:
        _raise_page_error(error)
    return _detail_redirect(task_id, return_to)


@router.post("/{task_id}/revoke-ready")
async def revoke_room_ready(
    request: Request,
    task_id: int,
    csrf_token: str = Form(min_length=1, max_length=128),
    return_to: Annotated[str, Form(max_length=200)] = "",
) -> RedirectResponse:
    """允许管理员把任务关联房间撤回待检查。"""
    employee = await _current_employee(request)
    await _consume_csrf(request, task_id, csrf_token)
    try:
        await _get_service(request).revoke_ready(task_id, employee)
    except Exception as error:
        _raise_page_error(error)
    return _detail_redirect(task_id, return_to)
