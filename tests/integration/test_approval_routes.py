import re
from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from homestay_bot.domain.enums import ApprovalStatus, EmployeeRole
from homestay_bot.domain.models import BookingApproval, Employee
from homestay_bot.routes.approvals import router as approvals_router
from homestay_bot.routes.employee_auth import router as employee_auth_router


class EmployeeAuthStub:
    """用 OAuth code 返回指定角色的本地员工。"""

    def __init__(self, role: EmployeeRole) -> None:
        self.role = role

    def authorization_url(self, redirect_uri: str, state: str) -> str:
        """返回测试授权地址。"""
        return f"https://wecom.example/authorize?state={state}"

    async def authenticate(self, code: str) -> Employee:
        """返回与测试角色匹配的员工。"""
        return Employee(
            id=1,
            wecom_userid="staff-1",
            name="员工甲",
            role=self.role,
            is_active=True,
        )


class ApprovalPageStub:
    """提供审批详情并记录确认次数。"""

    def __init__(self) -> None:
        self.confirm_calls = 0
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
    app.state.employee_auth_service = EmployeeAuthStub(role)
    approvals = ApprovalPageStub()
    app.state.approval_page_service = approvals
    return TestClient(app), approvals


def login(client: TestClient) -> None:
    """走完整 OAuth state 校验流程获得员工会话。"""
    login_response = client.get(
        "/employee/login",
        params={"next": "/employee/approvals/1"},
        follow_redirects=False,
    )
    state = re.search(r"state=([^&]+)", login_response.headers["location"]).group(1)
    callback = client.get(
        "/employee/oauth/callback",
        params={"code": "oauth-code", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code == 303


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
    """未登录访问审批详情时应跳转企业微信授权入口。"""
    client, _ = build_client(EmployeeRole.ADMIN)

    response = client.get("/employee/approvals/1", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/employee/login")


def test_regular_customer_service_cannot_confirm_booking() -> None:
    """普通客服可查看，但没有最终创建订单权限。"""
    client, approvals = build_client(EmployeeRole.CUSTOMER_SERVICE)
    login(client)
    detail = client.get("/employee/approvals/1")
    nonce = re.search(
        r'name="confirmation_nonce" value="([^"]+)"', detail.text
    ).group(1)

    response = client.post(
        "/employee/approvals/1/confirm",
        data=valid_form(nonce),
    )

    assert response.status_code == 403
    assert approvals.confirm_calls == 0


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
