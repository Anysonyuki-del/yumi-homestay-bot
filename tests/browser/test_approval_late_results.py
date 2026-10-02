"""用真实审批门面、路由与临时 SQLite 核对迟到提醒，不调用真实上游。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from admin_auth_helpers import configure_admin_auth, login_admin
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from homestay_bot.db import create_engine, create_session_factory
from homestay_bot.domain.enums import ApprovalStatus, EmployeeRole
from homestay_bot.domain.models import AuditLog, Base, BookingApproval
from homestay_bot.routes.approvals import router as approvals_router
from homestay_bot.routes.employee_auth import router as employee_auth_router
from tests.browser.test_admin_interactions import (
    ADMIN_CSS,
    browser,  # noqa: F401
    playwright_runtime,  # noqa: F401
)
from tests.integration.test_approval_review_actions import (
    BookingHostex,
    _facade,
    new_sensitive,
    seed_world,
)


@pytest.fixture
def approval_client(tmp_path):
    """真实读取服务使用临时 SQLite；只替换上游参考数据和测试认证。"""
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'approvals.db'}")
    factory = create_session_factory(engine)
    sensitive = new_sensitive()

    async def seed():
        """合成两种审批状态及乱序审计，验证目标过滤、倒序、上限和空标识。"""
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await seed_world(factory, sensitive)
        async with factory() as session:
            first = await session.get(BookingApproval, 1)
            second = await session.get(BookingApproval, 2)
            first.status = ApprovalStatus.PENDING
            second.status = ApprovalStatus.NEEDS_REVIEW
            now = datetime.now(UTC)
            for index in reversed(range(12)):
                session.add(AuditLog(
                    actor_employee_id=None, action="booking_approval_late_result_discarded",
                    target_type="booking_approval", target_id="1",
                    created_at=now + timedelta(seconds=index),
                    details={
                        "request_id": f"REQ-{index}", "reservation_codes": [f"HX-{index}"],
                        "error_code": 400, "guest_name": "不应出现在提醒中",
                    },
                ))
            session.add(AuditLog(
                actor_employee_id=None, action="booking_approval_late_result_discarded",
                target_type="booking_approval", target_id="2", details={
                    "request_id": None, "reservation_codes": [],
                },
            ))
            session.add(AuditLog(
                actor_employee_id=1, action="booking_approval_reopened",
                target_type="booking_approval", target_id="1",
                details={"request_id": "WRONG-ACTION", "reservation_codes": ["WRONG-CODE"]},
            ))
            await session.commit()
        # TestClient 的请求运行在自己的事件循环；清空建库阶段的连接池再交给它。
        await engine.dispose()

    # Playwright 同步运行时占用当前线程的事件循环，数据库生命周期放到独立线程。
    with ThreadPoolExecutor(max_workers=1) as worker:
        try:
            worker.submit(asyncio.run, seed()).result()
            app = FastAPI()
            app.add_middleware(SessionMiddleware, secret_key="synthetic-session")
            app.include_router(employee_auth_router)
            app.include_router(approvals_router)
            configure_admin_auth(app, EmployeeRole.ADMIN)
            app.state.approval_page_service = _facade(factory, sensitive, BookingHostex())
            with TestClient(app) as client:
                login_admin(client, next_path="/employee/approvals/1")
                yield client, app
        finally:
            worker.submit(asyncio.run, engine.dispose()).result()


@pytest.mark.parametrize("approval_id", [1, 2])
def test_late_results_show_across_states_without_guest_data(
    approval_client, browser, approval_id,  # noqa: F811
):
    """Chromium 检查真实路由响应；新增警示不暴露隐私，原有预订资料仍可见。"""
    client, _ = approval_client
    response = client.get(f"/employee/approvals/{approval_id}")
    assert response.status_code == 200
    page = browser.new_page(viewport={"width": 390, "height": 844})
    try:
        # 本项是静态提醒：载入真实响应与样式，阻止浏览器请求任何额外资源。
        page.route("**/*", lambda route: route.abort())
        page.set_content(response.text)
        page.add_style_tag(content=ADMIN_CSS)
        warning = page.get_by_role("alert").filter(has_text="上一轮建单结果需核对")
        assert warning.is_visible()
        text = warning.inner_text()
        assert "张三" not in text
        assert "13800138000" not in text
        assert "不应出现在提醒中" not in text
        assert "WRONG-ACTION" not in text
        assert "WRONG-CODE" not in text
        assert "张三" in page.locator("body").inner_text()
        assert "13800138000" in page.locator("body").inner_text()
        rows = warning.locator("li")
        if approval_id == 1:
            assert rows.count() == 10
            assert "REQ-11" in rows.first.inner_text()
            assert "REQ-2" in rows.last.inner_text()
            assert "HX-11" in text
            assert "错误码：400" in text
        else:
            assert rows.count() == 1
            assert text.count("未取得") == 2
            assert "REQ-" not in text
        assert page.locator("body").evaluate(
            "node => node.scrollWidth <= window.innerWidth"
        )
    finally:
        page.close()


def test_staff_cannot_read_late_audits(approval_client):
    """沿用真实路由的管理员限制，普通员工不得取得审计和客人资料。"""
    client, app = approval_client
    configure_admin_auth(app, EmployeeRole.STAFF)
    login_admin(client, next_path="/employee/approvals/1")
    response = client.get("/employee/approvals/1")
    assert response.status_code == 403
    assert "REQ-11" not in response.text
    assert "张三" not in response.text
