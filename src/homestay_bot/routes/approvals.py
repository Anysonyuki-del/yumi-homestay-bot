import secrets
from typing import Any, Protocol, cast

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.domain.models import BookingApproval
from homestay_bot.domain.schemas import ConfirmBookingCommand
from homestay_bot.routes.employee_auth import require_employee_session
from homestay_bot.services.approval_page_service import ApprovalPageView
from homestay_bot.web import templates

router = APIRouter(prefix="/employee/approvals")


class ApprovalPageServicePort(Protocol):
    """定义审批页面读取与确认所需的业务接口。"""

    async def get_detail(self, approval_id: int) -> dict[str, Any]:
        """返回审批详情及可选房间、参考价和收入方式。"""

    async def list_pending(
        self, *, offset: int, limit: int
    ) -> list[ApprovalPageView]:
        """分页返回员工需要处理的审批单。"""

    async def confirm(
        self,
        approval_id: int,
        employee_id: int,
        command: ConfirmBookingCommand,
    ) -> BookingApproval:
        """由授权员工确认并执行安全下单流程。"""


def _get_page_service(request: Request) -> ApprovalPageServicePort:
    """从应用状态读取审批页面业务服务。"""
    service = getattr(request.app.state, "approval_page_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="审批服务尚未配置",
        )
    return cast(ApprovalPageServicePort, service)


@router.get("", response_class=HTMLResponse)
async def approval_index(
    request: Request,
    page: int = Query(1, ge=1, le=10_000),
) -> Response:
    """只向管理员展示待处理审批列表。"""
    _, role = await require_employee_session(request)
    if role is not EmployeeRole.ADMIN:
        raise HTTPException(status_code=403, detail="只有管理员可以查看预订审批")
    approvals = await _get_page_service(request).list_pending(
        offset=(page - 1) * 50,
        limit=51,
    )
    return templates.TemplateResponse(
        request=request,
        name="approvals/index.html",
        context={
            "approvals": approvals[:50],
            "page": page,
            "previous_page": page - 1 if page > 1 else None,
            "next_page": page + 1 if len(approvals) > 50 else None,
            "page_title": "待处理预订",
            "active_nav": "approvals",
        },
    )


@router.get("/{approval_id}", response_class=HTMLResponse)
async def approval_detail(request: Request, approval_id: int) -> Response:
    """展示脱敏审批详情，并签发一次性确认令牌。"""
    _, role = await require_employee_session(request)
    if role is not EmployeeRole.ADMIN:
        raise HTTPException(status_code=403, detail="只有管理员可以查看预订审批")

    detail = await _get_page_service(request).get_detail(approval_id)
    nonce = secrets.token_urlsafe(24)
    nonces = dict(request.session.get("approval_nonces", {}))
    nonces[str(approval_id)] = nonce
    request.session["approval_nonces"] = nonces
    return templates.TemplateResponse(
        request=request,
        name="approvals/detail.html",
        context={
            **detail,
            "confirmation_nonce": nonce,
            "employee_role": role,
            "page_title": f"预订审批 {detail['approval'].approval_code}",
            "active_nav": "approvals",
        },
    )


@router.post("/{approval_id}/confirm")
async def confirm_approval(
    request: Request,
    approval_id: int,
    property_id: int = Form(),
    final_rate_amount: int = Form(),
    received_amount: int = Form(),
    income_method_id: int = Form(),
    payment_confirmed: bool = Form(),
    confirmation_nonce: str = Form(min_length=1, max_length=128),
) -> RedirectResponse:
    """校验角色和一次性令牌后，调用安全下单状态机。"""
    employee_id, role = await require_employee_session(request)
    if role is not EmployeeRole.ADMIN:
        raise HTTPException(status_code=403, detail="当前员工没有确认下单权限")

    nonces = dict(request.session.get("approval_nonces", {}))
    expected_nonce = nonces.pop(str(approval_id), None)
    request.session["approval_nonces"] = nonces
    if expected_nonce is None or not secrets.compare_digest(
        expected_nonce, confirmation_nonce
    ):
        raise HTTPException(status_code=409, detail="确认令牌无效或已使用")

    command = ConfirmBookingCommand(
        property_id=property_id,
        final_rate_amount=final_rate_amount,
        received_amount=received_amount,
        income_method_id=income_method_id,
        payment_confirmed=payment_confirmed,
    )
    await _get_page_service(request).confirm(approval_id, employee_id, command)
    return RedirectResponse(
        f"/employee/approvals/{approval_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
