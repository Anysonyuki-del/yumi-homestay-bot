import secrets
from typing import Annotated, Any, Protocol, cast
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.domain.models import Employee
from homestay_bot.routes.employee_auth import require_employee_session
from homestay_bot.services.customer_errors import (
    CustomerConflictError,
    CustomerNotFoundError,
    CustomerPermissionError,
)
from homestay_bot.web import templates

router = APIRouter(prefix="/employee/customers")


class CustomerAdminServicePort(Protocol):
    """定义客户管理页面所需的安全服务接口。"""

    async def list_customers(
        self,
        query: str | None,
        administrator: Employee,
        *,
        offset: int,
        limit: int,
    ) -> list[Any]:
        """按分页边界返回脱敏客户卡片。"""

    async def get_detail(
        self,
        customer_id: int,
        administrator: Employee,
    ) -> dict[str, Any]:
        """返回脱敏客户详情。"""

    async def get_merge_detail(
        self,
        suggestion_id: int,
        administrator: Employee,
    ) -> dict[str, Any]:
        """返回合并人工复核信息。"""

    async def set_tags(
        self,
        customer_id: int,
        tag_ids: list[int],
        administrator: Employee,
    ) -> None:
        """替换客户多选标签。"""

    async def update_note(
        self,
        customer_id: int,
        note: str,
        administrator: Employee,
    ) -> None:
        """更新员工备注。"""

    async def update_summary(
        self,
        customer_id: int,
        administrator: Employee,
        *,
        short_summary: str,
        long_summary: str,
        unresolved_items: list[str],
    ) -> None:
        """更正客户摘要。"""

    async def delete_summary(
        self,
        customer_id: int,
        administrator: Employee,
    ) -> None:
        """删除客户摘要。"""

    async def review_merge(
        self,
        suggestion_id: int,
        administrator: Employee,
        *,
        accepted: bool,
    ) -> None:
        """确认或拒绝客户合并建议。"""

    async def create_manual_merge(
        self,
        source_customer_id: int,
        target_customer_id: int,
        administrator: Employee,
    ) -> int:
        """创建待二次确认的管理员手动合并建议。"""


def _get_service(request: Request) -> CustomerAdminServicePort:
    """从应用状态读取客户管理服务。"""
    service = getattr(request.app.state, "customer_admin_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="客户管理服务尚未配置")
    return cast(CustomerAdminServicePort, service)


async def _current_admin(request: Request) -> Employee:
    """持续复核员工会话并拒绝普通员工进入 CRM。"""
    employee_id, role = await require_employee_session(request)
    if role is not EmployeeRole.ADMIN:
        raise HTTPException(status_code=403, detail="只有管理员可以管理客户")
    return Employee(
        id=employee_id,
        wecom_userid="",
        name="",
        role=role,
        is_active=True,
    )


def _issue_csrf(
    request: Request,
    *,
    namespace: str,
    object_id: int,
) -> str:
    """为客户或合并建议签发一次性表单令牌。"""
    token = secrets.token_urlsafe(24)
    tokens = dict(request.session.get(namespace, {}))
    tokens[str(object_id)] = token
    request.session[namespace] = tokens
    return token


def _consume_csrf(
    request: Request,
    *,
    namespace: str,
    object_id: int,
    token: str,
) -> None:
    """校验并立即消耗客户管理一次性令牌。"""
    tokens = dict(request.session.get(namespace, {}))
    expected = tokens.pop(str(object_id), None)
    request.session[namespace] = tokens
    if not isinstance(expected, str) or not secrets.compare_digest(
        expected,
        token,
    ):
        raise HTTPException(status_code=409, detail="表单令牌无效或已使用")


def _raise_page_error(error: Exception) -> None:
    """把客户服务领域异常转换为稳定 HTTP 状态。"""
    if isinstance(error, CustomerPermissionError):
        raise HTTPException(status_code=403, detail=str(error)) from error
    if isinstance(error, CustomerNotFoundError):
        raise HTTPException(status_code=404, detail=str(error)) from error
    if isinstance(error, CustomerConflictError):
        raise HTTPException(status_code=409, detail=str(error)) from error
    # 未知异常可能携带 SQL 或敏感值，只向页面返回统一文案。
    raise HTTPException(
        status_code=500,
        detail="客户管理操作失败",
    ) from error


@router.get("", response_class=HTMLResponse)
async def customer_index(
    request: Request,
    query: Annotated[str | None, Query(max_length=100)] = None,
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
) -> Response:
    """展示管理员可搜索的脱敏客户列表。"""
    administrator = await _current_admin(request)
    try:
        customers = await _get_service(request).list_customers(
            query,
            administrator,
            offset=(page - 1) * 50,
            limit=51,
        )
    except Exception as error:
        _raise_page_error(error)
    return templates.TemplateResponse(
        request=request,
        name="customers/index.html",
        context={
            "customers": customers[:50],
            "query": query or "",
            "previous_url": (
                "/employee/customers?"
                + urlencode({"query": query or "", "page": page - 1})
                if page > 1
                else None
            ),
            "next_url": (
                "/employee/customers?"
                + urlencode({"query": query or "", "page": page + 1})
                if len(customers) > 50
                else None
            ),
        },
    )


@router.get("/merge/{suggestion_id}", response_class=HTMLResponse)
async def customer_merge_detail(
    request: Request,
    suggestion_id: int,
) -> Response:
    """展示两侧脱敏档案并要求管理员明确决定。"""
    administrator = await _current_admin(request)
    try:
        detail = await _get_service(request).get_merge_detail(
            suggestion_id,
            administrator,
        )
    except Exception as error:
        _raise_page_error(error)
    return templates.TemplateResponse(
        request=request,
        name="customers/merge.html",
        context={
            **detail,
            "csrf_token": _issue_csrf(
                request,
                namespace="customer_merge_csrf",
                object_id=suggestion_id,
            ),
        },
    )


@router.post("/merge/{suggestion_id}/{decision}")
async def review_customer_merge(
    request: Request,
    suggestion_id: int,
    decision: str,
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """校验管理员和一次性令牌后确认或拒绝客户合并。"""
    administrator = await _current_admin(request)
    if decision not in {"confirm", "reject"}:
        raise HTTPException(status_code=404, detail="不支持的合并操作")
    _consume_csrf(
        request,
        namespace="customer_merge_csrf",
        object_id=suggestion_id,
        token=csrf_token,
    )
    try:
        await _get_service(request).review_merge(
            suggestion_id,
            administrator,
            accepted=decision == "confirm",
        )
    except Exception as error:
        _raise_page_error(error)
    return RedirectResponse(
        "/employee/customers",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/{customer_id}", response_class=HTMLResponse)
async def customer_detail(
    request: Request,
    customer_id: int,
    merge_query: Annotated[str | None, Query(max_length=100)] = None,
) -> Response:
    """展示脱敏手机号、标签、备注、摘要和待合并建议。"""
    administrator = await _current_admin(request)
    service = _get_service(request)
    try:
        detail = await service.get_detail(
            customer_id,
            administrator,
        )
        merge_targets = (
            [
                customer
                for customer in await service.list_customers(
                    merge_query,
                    administrator,
                    offset=0,
                    limit=50,
                )
                if customer.id != customer_id
            ]
            if merge_query and merge_query.strip()
            else []
        )
    except Exception as error:
        _raise_page_error(error)
    return templates.TemplateResponse(
        request=request,
        name="customers/detail.html",
        context={
            **detail,
            "merge_query": merge_query or "",
            "merge_targets": merge_targets,
            "csrf_token": _issue_csrf(
                request,
                namespace="customer_csrf",
                object_id=customer_id,
            ),
        },
    )


async def _customer_form_context(
    request: Request,
    customer_id: int,
    csrf_token: str,
) -> tuple[Employee, CustomerAdminServicePort]:
    """统一复核管理员并消耗客户详情页令牌。"""
    administrator = await _current_admin(request)
    _consume_csrf(
        request,
        namespace="customer_csrf",
        object_id=customer_id,
        token=csrf_token,
    )
    return administrator, _get_service(request)


def _customer_redirect(customer_id: int) -> RedirectResponse:
    """返回客户详情页的统一 303 跳转。"""
    return RedirectResponse(
        f"/employee/customers/{customer_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{customer_id}/merge/manual")
async def create_manual_customer_merge(
    request: Request,
    customer_id: int,
    target_customer_id: int = Form(),
    csrf_token: str = Form(),
) -> RedirectResponse:
    """消耗详情页令牌后创建建议，并进入既有二次复核页。"""
    administrator, service = await _customer_form_context(
        request,
        customer_id,
        csrf_token,
    )
    try:
        suggestion_id = await service.create_manual_merge(
            customer_id,
            target_customer_id,
            administrator,
        )
    except Exception as error:
        _raise_page_error(error)
    return RedirectResponse(
        f"/employee/customers/merge/{suggestion_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{customer_id}/tags")
async def update_customer_tags(
    request: Request,
    customer_id: int,
    tag_ids: Annotated[list[int] | None, Form(max_length=100)] = None,
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """保存客户多选标签，本地提交不依赖外部同步。"""
    administrator, service = await _customer_form_context(
        request,
        customer_id,
        csrf_token,
    )
    try:
        await service.set_tags(
            customer_id,
            tag_ids or [],
            administrator,
        )
    except Exception as error:
        _raise_page_error(error)
    return _customer_redirect(customer_id)


@router.post("/{customer_id}/note")
async def update_customer_note(
    request: Request,
    customer_id: int,
    note: str = Form("", max_length=2000),
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """更新客户员工备注。"""
    administrator, service = await _customer_form_context(
        request,
        customer_id,
        csrf_token,
    )
    try:
        await service.update_note(customer_id, note, administrator)
    except Exception as error:
        _raise_page_error(error)
    return _customer_redirect(customer_id)


@router.post("/{customer_id}/summary")
async def update_customer_summary(
    request: Request,
    customer_id: int,
    short_summary: str = Form("", max_length=4000),
    long_summary: str = Form("", max_length=4000),
    unresolved_items: str = Form("", max_length=4000),
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """更正客户短期、长期摘要和待确认事项。"""
    administrator, service = await _customer_form_context(
        request,
        customer_id,
        csrf_token,
    )
    try:
        await service.update_summary(
            customer_id,
            administrator,
            short_summary=short_summary,
            long_summary=long_summary,
            unresolved_items=unresolved_items.splitlines(),
        )
    except Exception as error:
        _raise_page_error(error)
    return _customer_redirect(customer_id)


@router.post("/{customer_id}/summary/delete")
async def delete_customer_summary(
    request: Request,
    customer_id: int,
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """删除客户 AI 摘要但保留最小审计记录。"""
    administrator, service = await _customer_form_context(
        request,
        customer_id,
        csrf_token,
    )
    try:
        await service.delete_summary(customer_id, administrator)
    except Exception as error:
        _raise_page_error(error)
    return _customer_redirect(customer_id)
