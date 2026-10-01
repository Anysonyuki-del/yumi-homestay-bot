from typing import Annotated, Any, Protocol, cast

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.domain.errors import OperationRefused
from homestay_bot.routes.admin_form_csrf import (
    COMPLAINT_CSRF_FAMILY,
    consume_form_csrf,
    drop_legacy_session_key,
    issue_form_csrf,
)
from homestay_bot.routes.employee_auth import require_employee_session
from homestay_bot.web import templates

router = APIRouter(prefix="/employee/complaints")


class ComplaintAdminServicePort(Protocol):
    """定义客诉编辑页面所需业务接口。"""

    async def list_open(self, *, offset: int, limit: int) -> list[Any]: ...
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


async def _csrf(request: Request, review_id: int) -> str:
    """为单条客诉签发服务端一次性表单令牌。"""
    drop_legacy_session_key(request, "complaint_csrf")
    return await issue_form_csrf(
        request,
        family=COMPLAINT_CSRF_FAMILY,
        entity_id=review_id,
    )


async def _consume_csrf(request: Request, review_id: int, token: str) -> None:
    """校验并原子消费客诉令牌；令牌绑定该复核单，跨单重放必然失败。"""
    await consume_form_csrf(
        request,
        family=COMPLAINT_CSRF_FAMILY,
        entity_id=review_id,
        token=token,
    )


async def _require_admin(request: Request) -> int:
    """客诉包含客人对话正文与对外回复权限，只允许管理员进入。"""
    employee_id, role = await require_employee_session(request)
    if role is not EmployeeRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有管理员可以复核客诉",
        )
    return employee_id


@router.get("", response_class=HTMLResponse)
async def complaint_index(
    request: Request,
    page: int = Query(1, ge=1, le=10_000),
) -> Response:
    """展示管理员可重新发现的待处理客诉列表。"""
    await _require_admin(request)
    items = await _service(request).list_open(
        offset=(page - 1) * 50,
        limit=51,
    )
    return templates.TemplateResponse(
        request=request,
        name="complaints/index.html",
        context={
            "complaints": items[:50],
            "page": page,
            "previous_page": page - 1 if page > 1 else None,
            "next_page": page + 1 if len(items) > 50 else None,
            "page_title": "待处理客诉",
            "active_nav": "complaints",
        },
    )


@router.get("/{review_id}", response_class=HTMLResponse)
async def complaint_detail(
    request: Request,
    review_id: int,
    before_message_id: Annotated[int | None, Query(gt=0)] = None,
) -> Response:
    """展示客诉分页对话、分析和可编辑回复草稿。"""
    employee_id = await _require_admin(request)
    return await _render_detail(
        request, review_id, employee_id, before_message_id=before_message_id
    )


async def _render_detail(
    request: Request,
    review_id: int,
    employee_id: int,
    *,
    before_message_id: int | None = None,
    status_code: int = status.HTTP_200_OK,
    error: str | None = None,
    submitted_draft: str | None = None,
    confirm_draft: str | None = None,
    confirm_version: int | None = None,
) -> Response:
    """渲染客诉详情；失败或待确认时带上员工刚提交的正文。

    submitted_draft 只用于在本次已认证的响应里把员工的输入还给他核对或复制，
    不写数据库、不覆盖最新草稿；能否再次发送仍按最新状态判定。PRG 做不到这一点
    （重定向会丢掉 textarea），而把长正文塞进签名 Cookie 会超出大小限制。
    """
    detail = await _service(request).get_detail(
        review_id,
        before_message_id=before_message_id,
    )
    if confirm_version is not None and confirm_version != detail["review"].version:
        # 确认步骤不能把员工手里的旧版本悄悄换成最新版本：那等于绕过版本冲突，
        # 用旧稿覆盖别人刚保存的内容（Codex M1）。改走冲突恢复，不出确认面板。
        status_code = status.HTTP_409_CONFLICT
        error = "客诉草稿已被其他员工更新，请核对最新内容后再发送"
        confirm_draft = None
    return templates.TemplateResponse(
        request=request,
        name="complaints/edit.html",
        status_code=status_code,
        context={
            **detail,
            "employee_id": employee_id,
            "csrf_token": await _csrf(request, review_id),
            "page_title": f"客诉复核 #{review_id}",
            "active_nav": "complaints",
            "error": error,
            "submitted_draft": submitted_draft,
            "confirm_draft": confirm_draft,
            # 确认表单沿用员工最初提交的版本：确认面板打开后又有人保存，最终发送
            # 仍会被版本条件拒绝。
            "confirm_version": confirm_version,
        },
    )


def _wants_html(request: Request) -> bool:
    """表单提交期望页面；接口调用继续得到 JSON。"""
    return "text/html" in request.headers.get("accept", "")


async def _action(
    request: Request,
    review_id: int,
    version: int,
    csrf_token: str,
    action: str,
    draft: str = "",
    confirmed: str = "",
) -> Response:
    """统一处理客诉编辑页的保存、发送、退回和关闭动作。

    失败的恢复方式按「请求里有没有未保存的输入」区分（Spec §4.3）：保存和发送
    带着员工正在编辑的正文，被拒时原地重新渲染，同时给出最新状态和原文；退回和
    关闭没有输入要保留，用 OperationRefused 回跳详情页显示原因。
    """
    employee_id = await _require_admin(request)
    await _consume_csrf(request, review_id, csrf_token)
    if action == "send" and confirmed != "1":
        # 没有脚本确认（或脚本被禁用）时，先让员工看一遍将要发出的正文，
        # 确认后才真正登记发送；不能因为缺少脚本就跳过确认。
        return await _render_detail(
            request,
            review_id,
            employee_id,
            submitted_draft=draft,
            confirm_draft=draft,
            confirm_version=version,
        )
    service = _service(request)
    try:
        if action == "save":
            await service.update_draft(review_id, version, draft)
        elif action == "send":
            await service.send(review_id, version, draft, employee_id)
        elif action == "return":
            await service.return_for_analysis(review_id, version, employee_id)
        else:
            await service.cancel(review_id, version, employee_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail="客诉记录不存在") from error
    except OperationRefused as error:
        if action in {"save", "send"}:
            if not _wants_html(request):
                raise HTTPException(
                    status_code=error.status_code, detail=str(error)
                ) from error
            return await _render_detail(
                request,
                review_id,
                employee_id,
                status_code=error.status_code,
                error=str(error),
                submitted_draft=draft,
            )
        raise OperationRefused(
            str(error),
            status_code=error.status_code,
            return_to=f"/employee/complaints/{review_id}",
        ) from error
    return RedirectResponse(
        f"/employee/complaints/{review_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{review_id}/save")
async def complaint_save(
    request: Request,
    review_id: int,
    version: int = Form(ge=0),
    # 默认空串：清空回复框后提交，也要进到业务判断，得到可恢复的页面而不是 JSON 422。
    draft: str = Form("", max_length=4000),
    csrf_token: str = Form(min_length=1, max_length=128),
) -> Response:
    """保存员工编辑草稿。"""
    return await _action(request, review_id, version, csrf_token, "save", draft)


@router.post("/{review_id}/send")
async def complaint_send(
    request: Request,
    review_id: int,
    version: int = Form(ge=0),
    # 默认空串：清空回复框后提交，也要进到业务判断，得到可恢复的页面而不是 JSON 422。
    draft: str = Form("", max_length=4000),
    csrf_token: str = Form(min_length=1, max_length=128),
    confirmed: str = Form("", max_length=1),
) -> Response:
    """发送回复框里的当前内容；confirmed=1 表示员工已看过将要发出的正文。"""
    return await _action(
        request, review_id, version, csrf_token, "send", draft, confirmed
    )


@router.post("/{review_id}/return")
async def complaint_return(
    request: Request,
    review_id: int,
    version: int = Form(ge=0),
    csrf_token: str = Form(min_length=1, max_length=128),
) -> Response:
    """退回客诉重新生成分析。"""
    return await _action(request, review_id, version, csrf_token, "return")


@router.post("/{review_id}/cancel")
async def complaint_cancel(
    request: Request,
    review_id: int,
    version: int = Form(ge=0),
    csrf_token: str = Form(min_length=1, max_length=128),
) -> Response:
    """关闭当前客诉。"""
    return await _action(request, review_id, version, csrf_token, "cancel")
