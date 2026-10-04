import re
from dataclasses import replace
from datetime import date

from admin_auth_helpers import configure_admin_auth, login_admin
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from homestay_bot.domain.enums import ApprovalStatus, EmployeeRole
from homestay_bot.routes.approvals import router as approvals_router
from homestay_bot.routes.employee_auth import router as employee_auth_router
from homestay_bot.services.approval_page_service import ApprovalPageView


class ApprovalPageStub:
    """提供审批详情并记录确认次数。"""

    def __init__(self) -> None:
        self.confirm_calls = 0
        self.reject_calls: list[tuple[int, int, str]] = []
        self.list_calls: list[tuple[int, int]] = []
        self.approval = ApprovalPageView(
            id=1,
            approval_code="APP-1",
            status=ApprovalStatus.PENDING,
            check_in_date=date(2026, 8, 1),
            check_out_date=date(2026, 8, 2),
            number_of_guests=2,
            guest_name="张三",
            room_type_preference="江景房",
            special_requests="高楼层",
        )

        self.reference_unavailable: list[str] = []
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
            "reference_price_groups": [{"id": 101, "title": "江景大床房 101", "prices": [
                {"date": "2026-08-01", "price": 399, "channel_type": "booking_site",
                 "currency_label": "CNY"}
            ]}],
            "unmatched_reference_prices": [],
            "income_methods": [{"id": 1, "name": "微信支付"}],
            "reference_unavailable": self.reference_unavailable,
            "can_confirm": not self.reference_unavailable,
        }

    async def confirm(self, approval_id: int, employee_id: int, command):
        """记录确认并返回已预订状态。"""
        self.confirm_calls += 1
        self.approval = replace(
            self.approval,
            status=ApprovalStatus.BOOKED,
        )
        return self.approval

    async def reject(self, approval_id: int, employee_id: int, reason: str):
        """记录拒绝原因并返回已拒绝状态。"""
        self.reject_calls.append((approval_id, employee_id, reason))
        self.approval = replace(
            self.approval,
            status=ApprovalStatus.REJECTED,
        )
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


def test_approval_pages_use_shell_and_emphasize_money_confirmation() -> None:
    """审批页应激活导航，并把关键金额与最终确认集中在醒目区域。"""
    client, _ = build_client(EmployeeRole.ADMIN)
    login(client)

    index = client.get("/employee/approvals")
    detail = client.get("/employee/approvals/1")

    assert '/static/admin.js' in index.text
    assert 'href="/employee/approvals" aria-current="page"' in detail.text
    assert 'class="decision-panel' in detail.text
    assert 'data-unsaved-warning' in detail.text
    assert 'data-confirm=' in detail.text
    assert '<option value="" selected disabled>请选择房间</option>' in detail.text
    assert '<option value="" selected disabled>请选择收款方式</option>' in detail.text
    assert "13800138000" not in detail.text


def test_non_pending_approval_cannot_render_real_order_form() -> None:
    """冲突或需复核审批只能展示处理说明，不能继续提交真实订单。"""
    client, approvals = build_client(EmployeeRole.ADMIN)
    approvals.approval = replace(
        approvals.approval,
        status=ApprovalStatus.CONFLICT,
    )
    login(client)

    detail = client.get("/employee/approvals/1")

    assert detail.status_code == 200
    assert 'action="/employee/approvals/1/confirm"' not in detail.text
    assert "当前审批需要人工处理" in detail.text


def test_approval_nonce_rejects_cross_entity_replay() -> None:
    """审批详情签发的确认令牌不得用于确认另一张审批单。

    下单会创建真实订单，令牌与审批单的绑定关系必须由测试锁定。
    """
    client, approvals = build_client(EmployeeRole.ADMIN)
    login(client)
    detail = client.get("/employee/approvals/1")
    nonce = re.search(
        r'name="confirmation_nonce" value="([^"]+)"',
        detail.text,
    ).group(1)

    response = client.post(
        "/employee/approvals/2/confirm",
        data=valid_form(nonce),
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert approvals.confirm_calls == 0


def nonce_from(client: TestClient) -> str:
    """读取审批详情页的一次性确认令牌。"""
    page = client.get("/employee/approvals/1")
    return re.search(
        r'name="confirmation_nonce" value="([^"]+)"',
        page.text,
    ).group(1)


def test_pending_approval_offers_rejection_entry() -> None:
    """待处理审批必须提供拒绝入口。

    ApprovalStatus.REJECTED 此前没有任何写入点，页面只能确认不能拒绝，
    一笔不该接的预订在后台无法结束。
    """
    client, _ = build_client(EmployeeRole.ADMIN)
    login(client)

    page = client.get("/employee/approvals/1")

    assert page.status_code == 200
    assert 'action="/employee/approvals/1/reject"' in page.text
    assert 'name="reason"' in page.text


def test_admin_rejects_approval_with_reason() -> None:
    """管理员提交拒绝原因后审批转为已拒绝。"""
    client, approvals = build_client(EmployeeRole.ADMIN)
    login(client)

    response = client.post(
        "/employee/approvals/1/reject",
        data={"reason": "所选日期已被其他渠道占用", "confirmation_nonce": nonce_from(client)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert approvals.reject_calls == [(1, 1, "所选日期已被其他渠道占用")]


def test_staff_cannot_reject_approval() -> None:
    """普通员工不得拒绝审批，与确认权限保持一致。"""
    client, approvals = build_client(EmployeeRole.STAFF)
    login_admin(client, next_path="/employee/approvals/1")

    response = client.post(
        "/employee/approvals/1/reject",
        data={"reason": "测试", "confirmation_nonce": "any"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert approvals.reject_calls == []


def test_rejection_requires_non_empty_reason() -> None:
    """空原因必须被拒绝：原因是拒绝决定的唯一留痕。"""
    client, approvals = build_client(EmployeeRole.ADMIN)
    login(client)

    response = client.post(
        "/employee/approvals/1/reject",
        data={"reason": "", "confirmation_nonce": nonce_from(client)},
        follow_redirects=False,
    )

    assert response.status_code == 422
    assert approvals.reject_calls == []


def test_degraded_reference_data_keeps_the_page_and_the_reject_path() -> None:
    """参考数据缺失时页面照常可看、可拒，只有确认下单不再提供入口。"""
    client, approvals = build_client(EmployeeRole.ADMIN)
    login(client)
    approvals.reference_unavailable = ["渠道日历参考价"]

    page = client.get("/employee/approvals/1")

    assert page.status_code == 200
    # 本地资料与拒绝入口不受上游影响。
    assert "138****8000" in page.text
    assert 'action="/employee/approvals/1/reject"' in page.text
    # 缺什么要说清楚，而不是给一个没有解释的灰按钮。
    assert "渠道日历参考价" in page.text
    assert "百居易参考数据暂不可用" in page.text
    # 下单入口必须真的消失，不能只是视觉禁用。
    assert 'action="/employee/approvals/1/confirm"' not in page.text


class ReviewActionStub(ApprovalPageStub):
    """记录人工核验动作；refuse 非空时按服务层方式拒绝。"""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple] = []
        self.refuse: str | None = None

    def _maybe_refuse(self, approval_id: int) -> None:
        from homestay_bot.services.approval_page_service import ApprovalActionRefused

        if self.refuse:
            raise ApprovalActionRefused(
                self.refuse, return_to=f"/employee/approvals/{approval_id}"
            )

    async def backfill_reservation(self, approval_id, employee_id, reservation_code):
        self._maybe_refuse(approval_id)
        self.calls.append(("backfill", approval_id, reservation_code))

    async def reopen_after_review(self, approval_id, employee_id):
        self._maybe_refuse(approval_id)
        self.calls.append(("reopen", approval_id))

    async def recheck_after_conflict(self, approval_id, employee_id):
        self._maybe_refuse(approval_id)
        self.calls.append(("recheck", approval_id))


def _review_client(role: EmployeeRole, status: ApprovalStatus):
    """装配真实审批路由、生产的业务拒绝处理器和人工核验替身。"""
    from homestay_bot.domain.errors import OperationRefused
    from homestay_bot.routes.page_errors import handle_operation_refused

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-session-secret")
    app.add_exception_handler(OperationRefused, handle_operation_refused)
    app.include_router(employee_auth_router)
    app.include_router(approvals_router)
    configure_admin_auth(app, role)
    stub = ReviewActionStub()
    stub.approval = replace(stub.approval, status=status)
    app.state.approval_page_service = stub
    return TestClient(app), stub


def _nonce(client: TestClient) -> str:
    return re.search(
        r'name="confirmation_nonce" value="([^"]+)"', client.get("/employee/approvals/1").text
    ).group(1)


def test_needs_review_page_offers_backfill_and_reopen_to_admins() -> None:
    """W5：需复核页给管理员两个入口，确认框重复显示所填订单号；不出现建单表单。"""
    client, _ = _review_client(EmployeeRole.ADMIN, ApprovalStatus.NEEDS_REVIEW)
    login(client)

    text = client.get("/employee/approvals/1").text

    assert 'action="/employee/approvals/1/backfill"' in text
    assert "{reservation_code}" in text
    assert 'action="/employee/approvals/1/reopen"' in text
    assert 'name="not_created_confirmed"' in text
    assert 'action="/employee/approvals/1/confirm"' not in text
    assert 'action="/employee/approvals/1/recheck"' not in text


def test_conflict_page_offers_recheck_only() -> None:
    """W5：有冲突页只给「回到待审批重新确认」，没有填订单号入口。"""
    client, _ = _review_client(EmployeeRole.ADMIN, ApprovalStatus.CONFLICT)
    login(client)

    text = client.get("/employee/approvals/1").text

    assert 'action="/employee/approvals/1/recheck"' in text
    assert 'action="/employee/approvals/1/backfill"' not in text


def test_review_actions_use_single_use_nonce_and_reach_the_service() -> None:
    """三个动作都消费一次性令牌：成功后回详情页，同一令牌重放被拒。"""
    client, stub = _review_client(EmployeeRole.ADMIN, ApprovalStatus.NEEDS_REVIEW)
    login(client)
    nonce = _nonce(client)

    first = client.post(
        "/employee/approvals/1/backfill",
        data={"reservation_code": "HX-1", "confirmation_nonce": nonce},
        follow_redirects=False,
    )
    replay = client.post(
        "/employee/approvals/1/reopen",
        data={"not_created_confirmed": "true", "confirmation_nonce": nonce},
        follow_redirects=False,
    )
    recheck = client.post(
        "/employee/approvals/1/recheck",
        data={"confirmation_nonce": _nonce(client)},
        follow_redirects=False,
    )

    assert first.status_code == 303
    assert first.headers["location"] == "/employee/approvals/1"
    assert replay.status_code == 409
    assert recheck.status_code == 303
    assert stub.calls == [("backfill", 1, "HX-1"), ("recheck", 1)]


def test_reopen_requires_the_not_created_confirmation() -> None:
    """没勾选「已确认没有这笔订单」不能回退。"""
    client, stub = _review_client(EmployeeRole.ADMIN, ApprovalStatus.NEEDS_REVIEW)
    login(client)

    response = client.post(
        "/employee/approvals/1/reopen",
        data={"not_created_confirmed": "false", "confirmation_nonce": _nonce(client)},
        follow_redirects=False,
    )

    assert response.status_code == 422
    assert stub.calls == []


def test_refused_review_action_returns_to_the_page_with_the_reason() -> None:
    """服务层拒绝（如反查到疑似订单）时回到详情页显示原因，不是 500 或 JSON。"""
    client, stub = _review_client(EmployeeRole.ADMIN, ApprovalStatus.NEEDS_REVIEW)
    login(client)
    stub.refuse = "百居易里已有同一客人、同一日期的订单（HX-9），不能回到待审批"

    response = client.post(
        "/employee/approvals/1/reopen",
        data={"not_created_confirmed": "true", "confirmation_nonce": _nonce(client)},
        headers={"accept": "text/html"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/employee/approvals/1"
    assert "HX-9" in client.get("/employee/approvals/1").text


def test_staff_cannot_use_review_actions() -> None:
    """普通员工看不到入口，直接构造请求也被拒。"""
    client, stub = _review_client(EmployeeRole.STAFF, ApprovalStatus.NEEDS_REVIEW)
    login(client)

    response = client.post(
        "/employee/approvals/1/recheck", data={"confirmation_nonce": "x"},
        follow_redirects=False,
    )

    assert response.status_code in {401, 403}
    assert stub.calls == []
