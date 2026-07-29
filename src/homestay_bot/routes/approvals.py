import secrets
from pathlib import Path
from typing import Any, Protocol, cast

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.domain.models import BookingApproval
from homestay_bot.domain.schemas import ConfirmBookingCommand

router = APIRouter(prefix="/employee/approvals")
templates = Jinja2Templates(
    directory=Path(__file__).resolve().parent.parent / "templates"
)


class ApprovalPageServicePort(Protocol):
    """定义审批页面读取与确认所需的业务接口。"""

    async def get_detail(self, approval_id: int) -> dict[str, Any]:
        """返回审批详情及可选房间、参考价和收入方式。"""

    async def confirm(
        self,
        approval_id: int,
        employee_id: int,
        command: ConfirmBookingCommand,
    ) -> BookingApproval:
        """由授权员工确认并执行安全下单流程。"""


def _require_employee(request: Request) -> tuple[int, EmployeeRole]:
    """从签名会话读取可信员工身份，未登录时由路由重定向。"""
    employee_id = request.session.get("employee_id")
    role = request.session.get("employee_role")
    if not isinstance(employee_id, int) or not isinstance(role, str):
        raise HTTPException(status_code=401, detail="员工尚未登录")
    try:
        return employee_id, EmployeeRole(role)
    except ValueError as error:
        raise HTTPException(status_code=401, detail="员工角色无效") from error


def _get_page_service(request: Request) -> ApprovalPageServicePort:
    """从应用状态读取审批页面业务服务。"""
    service = getattr(request.app.state, "approval_page_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="审批服务尚未配置",
        )
    return cast(ApprovalPageServicePort, service)


@router.get("/{approval_id}", response_class=HTMLResponse)
async def approval_detail(request: Request, approval_id: int) -> Response:
    """展示脱敏审批详情，并签发一次性确认令牌。"""
    try:
        _, role = _require_employee(request)
    except HTTPException:
        return RedirectResponse(
            f"/employee/login?next=/employee/approvals/{approval_id}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    detail = await _get_page_service(request).get_detail(approval_id)
    nonce = secrets.token_urlsafe(24)
    nonces = dict(request.session.get("approval_nonces", {}))
    nonces[str(approval_id)] = nonce
    request.session["approval_nonces"] = nonces
    return templates.TemplateResponse(
        request=request,
        name="approvals/detail.html",
        context={**detail, "confirmation_nonce": nonce, "employee_role": role},
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
    confirmation_nonce: str = Form(),
) -> RedirectResponse:
    """校验角色和一次性令牌后，调用安全下单状态机。"""
    employee_id, role = _require_employee(request)
    if role not in {EmployeeRole.ADMIN, EmployeeRole.BOOKING_APPROVER}:
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
