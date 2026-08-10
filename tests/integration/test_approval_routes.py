import re
from datetime import date

from admin_auth_helpers import configure_admin_auth, login_admin
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from homestay_bot.domain.enums import ApprovalStatus, EmployeeRole
from homestay_bot.domain.models import BookingApproval
from homestay_bot.routes.approvals import router as approvals_router
from homestay_bot.routes.employee_auth import router as employee_auth_router


class ApprovalPageStub:
    """提供审批详情并记录确认次数。"""

    def __init__(self) -> None:
        self.confirm_calls = 0
        self.list_calls: list[tuple[int, int]] = []
        self.approval = BookingApproval(
            id=1,
            approval_code="APP-1",
            conversation_id=1,
            status=ApprovalStatus.PENDING,
            check_in_date=date(2026, 8, 1),
            check_out_date=date(2026, 8, 2),
            number_of_guests=2,
            guest_name="张三",
            guest_mobile="13800138000",
            room_type_preference="江景房",
            special_requests="高楼层",
        )

    async def list_pending(self, *, offset: int, limit: int):
        """记录审批分页边界并返回足够判断下一页的数据。"""
        self.list_calls.append((offset, limit))
        return [self.approval] * limit

    async def get_detail(self, approval_id: int):
        """返回页面展示所需的审批、房间、价格和收入方式。"""
        assert approval_id == 1
        return {
            "approval": self.approval,
            "masked_mobile": "138****8000",
            "properties": [{"id": 101, "title": "江景大床房 101"}],
            "reference_prices": [{"date": "2026-08-01", "price": 399}],
            "income_methods": [{"id": 1, "name": "微信支付"}],
        }

    async def confirm(self, approval_id: int, employee_id: int, command):
        """记录确认并返回已预订状态。"""
        self.confirm_calls += 1
        self.approval.status = ApprovalStatus.BOOKED
        self.approval.hostex_reservation_code = "R-1"
        return self.approval


def build_client(role: EmployeeRole) -> tuple[TestClient, ApprovalPageStub]:
    """创建带签名会话和测试服务的审批应用。"""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-session-secret")
    app.include_router(employee_auth_router)
    app.include_router(approvals_router)
    configure_admin_auth(app, role)
    approvals = ApprovalPageStub()
    app.state.approval_page_service = approvals
    return TestClient(app), approvals


def login(client: TestClient) -> None:
    """通过独立账号密码表单获得版本化会话。"""
    login_admin(client, next_path="/employee/approvals/1")


def valid_form(nonce: str) -> dict[str, str]:
    """返回员工已明确确认收款的有效表单。"""
    return {
        "property_id": "101",
        "final_rate_amount": "399",
        "received_amount": "399",
        "income_method_id": "1",
        "payment_confirmed": "true",
        "confirmation_nonce": nonce,
    }


def test_unauthenticated_employee_is_redirected_to_login() -> None:
    """浏览器未登录访问审批详情时应跳转独立管理员登录页。"""
    client, _ = build_client(EmployeeRole.ADMIN)

    response = client.get(
        "/employee/approvals/1",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/employee/login")


def test_staff_cannot_view_or_confirm_booking() -> None:
    """普通员工不能查看审批详情或创建订单。"""
    client, approvals = build_client(EmployeeRole.STAFF)
    login(client)
    detail = client.get("/employee/approvals/1")

    response = client.post(
        "/employee/approvals/1/confirm",
        data=valid_form("forged"),
    )

    assert detail.status_code == 403
    assert response.status_code == 403
    assert approvals.confirm_calls == 0


def test_approval_list_uses_bounded_pagination() -> None:
    """审批列表第二页必须有查询上限和稳定导航。"""
    client, approvals = build_client(EmployeeRole.ADMIN)
    login(client)

    response = client.get("/employee/approvals?page=2")

    assert response.status_code == 200
    assert approvals.list_calls == [(50, 51)]
    assert 'href="/employee/approvals?page=1"' in response.text
    assert 'href="/employee/approvals?page=3"' in response.text


def test_admin_confirm_nonce_is_single_use_and_mobile_is_masked() -> None:
    """管理员可确认一次，同一 nonce 重放必须失败且页面不得暴露完整手机号。"""
    client, approvals = build_client(EmployeeRole.ADMIN)
    login(client)
    detail = client.get("/employee/approvals/1")
    nonce = re.search(
        r'name="confirmation_nonce" value="([^"]+)"', detail.text
    ).group(1)

    first = client.post(
        "/employee/approvals/1/confirm",
        data=valid_form(nonce),
        follow_redirects=False,
    )
    second = client.post(
        "/employee/approvals/1/confirm",
        data=valid_form(nonce),
        follow_redirects=False,
    )

    assert "138****8000" in detail.text
    assert "13800138000" not in detail.text
    assert "人民币" in detail.text
    assert first.status_code == 303
    assert second.status_code == 409
    assert approvals.confirm_calls == 1
