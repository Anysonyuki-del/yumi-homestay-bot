import secrets
from typing import Annotated, Any, Protocol, cast

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from homestay_bot.repositories.complaints import ComplaintVersionConflict
from homestay_bot.routes.employee_auth import require_employee_session
from homestay_bot.web import templates

router = APIRouter(prefix="/employee/complaints")


class ComplaintAdminServicePort(Protocol):
    """定义客诉编辑页面所需业务接口。"""

    async def get_detail(
        self,
        review_id: int,
        *,
        before_message_id: int | None = None,
    ) -> dict[str, Any]: ...
    async def update_draft(self, review_id: int, version: int, draft: str) -> None: ...
    async def send(self, review_id: int, version: int, draft: str, employee_id: int) -> None: ...
    async def return_for_analysis(self, review_id: int, version: int, employee_id: int) -> None: ...
    async def cancel(self, review_id: int, version: int, employee_id: int) -> None: ...


def _service(request: Request) -> ComplaintAdminServicePort:
    """读取应用装配的客诉页面服务。"""
    service = getattr(request.app.state, "complaint_admin_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="客诉服务尚未配置")
    return cast(ComplaintAdminServicePort, service)


def _csrf(request: Request, review_id: int) -> str:
    """为单条客诉生成一次性表单令牌。"""
    token = secrets.token_urlsafe(24)
    values = dict(request.session.get("complaint_csrf", {}))
    values[str(review_id)] = token
    request.session["complaint_csrf"] = values
    return token


def _consume_csrf(request: Request, review_id: int, token: str) -> None:
    """校验并消耗客诉表单令牌。"""
    values = dict(request.session.get("complaint_csrf", {}))
    expected = values.pop(str(review_id), None)
    request.session["complaint_csrf"] = values
    if not isinstance(expected, str) or not secrets.compare_digest(expected, token):
        raise HTTPException(status_code=409, detail="表单令牌无效或已使用")


@router.get("/{review_id}", response_class=HTMLResponse)
async def complaint_detail(
    request: Request,
    review_id: int,
    before_message_id: Annotated[int | None, Query(gt=0)] = None,
) -> Response:
    """展示客诉分页对话、分析和可编辑回复草稿。"""
    employee_id, role = await require_employee_session(request)
    detail = await _service(request).get_detail(
        review_id,
        before_message_id=before_message_id,
    )
    return templates.TemplateResponse(
        request=request,
        name="complaints/edit.html",
        context={
            **detail,
            "employee_id": employee_id,
            "csrf_token": _csrf(request, review_id),
            "page_title": f"客诉复核 #{review_id}",
            "active_nav": None,
        },
    )


async def _action(
    request: Request,
    review_id: int,
    version: int,
    csrf_token: str,
    action: str,
    draft: str = "",
) -> RedirectResponse:
    """统一处理客诉编辑页的保存、发送、退回和关闭动作。"""
    employee_id, _ = await require_employee_session(request)
    _consume_csrf(request, review_id, csrf_token)
    try:
        service = _service(request)
        if action == "save":
            await service.update_draft(review_id, version, draft)
        elif action == "send":
            await service.send(review_id, version, draft, employee_id)
        elif action == "return":
            await service.return_for_analysis(review_id, version, employee_id)
        else:
            await service.cancel(review_id, version, employee_id)
    except ComplaintVersionConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return RedirectResponse(
        f"/employee/complaints/{review_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{review_id}/save")
async def complaint_save(
    request: Request,
    review_id: int,
    version: int = Form(ge=0),
    draft: str = Form(max_length=4000),
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """保存员工编辑草稿。"""
    return await _action(request, review_id, version, csrf_token, "save", draft)


@router.post("/{review_id}/send")
async def complaint_send(
    request: Request,
    review_id: int,
    version: int = Form(ge=0),
    draft: str = Form(max_length=4000),
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """发送人工确认后的回复。"""
    return await _action(request, review_id, version, csrf_token, "send", draft)


@router.post("/{review_id}/return")
async def complaint_return(
    request: Request,
    review_id: int,
    version: int = Form(ge=0),
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """退回客诉重新生成分析。"""
    return await _action(request, review_id, version, csrf_token, "return")


@router.post("/{review_id}/cancel")
async def complaint_cancel(
    request: Request,
    review_id: int,
    version: int = Form(ge=0),
    csrf_token: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """关闭当前客诉。"""
    return await _action(request, review_id, version, csrf_token, "cancel")
