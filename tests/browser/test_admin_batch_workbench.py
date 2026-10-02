"""真实路由、会话门面与临时 SQLite 验证批量操作和未保存房态。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

import pytest
from admin_auth_helpers import configure_admin_auth, login_admin
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware
from test_admin_interactions import (
    ADMIN_CSS,
    ADMIN_SCRIPT,
    browser,  # noqa: F401
    playwright_runtime,  # noqa: F401
)

from homestay_bot.application import (
    SessionAdminOperationsService,
    SessionCustomerAdminService,
    SessionTaskPageService,
)
from homestay_bot.db import create_engine, create_session_factory
from homestay_bot.domain.enums import BusinessTaskStatus, BusinessTaskType, EmployeeRole
from homestay_bot.domain.errors import OperationRefused
from homestay_bot.domain.models import (
    Base,
    BusinessTask,
    Customer,
    Employee,
    PropertyProfile,
    StayOrder,
)
from homestay_bot.routes.admin import router as admin_router
from homestay_bot.routes.customers import router as customers_router
from homestay_bot.routes.employee_auth import router as employee_auth_router
from homestay_bot.routes.page_errors import handle_operation_refused
from homestay_bot.routes.tasks import router as tasks_router
from homestay_bot.services.private_file_storage import PrivateFileStorage
from homestay_bot.services.sensitive_data import SensitiveDataCipher


@pytest.fixture
def admin_client(tmp_path):
    """只使用合成记录；数据库与测试认证之外采用正式会话门面。"""
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'admin.db'}")
    factory = create_session_factory(engine)
    today = date(2026, 10, 2)
    observed = datetime(2026, 10, 2, 3, 59, 30, tzinfo=UTC)

    async def seed():
        """混合终态、开放态、缺字段和长列表，同时合成一个即将越过的退房节点。"""
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            session.add_all([
                Employee(id=1, wecom_userid="synthetic-admin", name="管理员",
                         role=EmployeeRole.ADMIN),
                Employee(id=2, wecom_userid="synthetic-staff", name="合成员工",
                         role=EmployeeRole.STAFF),
                PropertyProfile(id=101, title="合成房间", room_number="101", is_active=True),
                Customer(id=1, display_name="合成客户"),
            ])
            await session.flush()
            for task_id in range(11, 45):
                status = BusinessTaskStatus.ASSIGNED
                if task_id == 11:
                    status = BusinessTaskStatus.COMPLETED
                elif task_id == 12:
                    status = BusinessTaskStatus.PENDING_ASSIGNMENT
                elif task_id == 13:
                    status = BusinessTaskStatus.PENDING_CONFIRMATION
                session.add(BusinessTask(
                    id=task_id, task_type=BusinessTaskType.CLEANING, status=status,
                    property_id=None if task_id == 13 else 101,
                    service_date=None if task_id == 13 else today,
                    assigned_employee_id=2, customer_id=1,
                    description="合成任务说明，联系电话 13800138000",
                ))
            session.add(StayOrder(
                hostex_reservation_code="synthetic-order", stay_code="synthetic-order",
                property_id=101, customer_id=1, status="confirmed",
                check_in_date=today - timedelta(days=1), check_out_date=today,
            ))
            await session.commit()
        await engine.dispose()

    with ThreadPoolExecutor(max_workers=1) as worker:
        try:
            worker.submit(asyncio.run, seed()).result()
            app = FastAPI()
            app.add_middleware(SessionMiddleware, secret_key="synthetic-session")
            for router in (employee_auth_router, tasks_router, customers_router, admin_router):
                app.include_router(router)
            app.add_exception_handler(OperationRefused, handle_operation_refused)
            configure_admin_auth(app, EmployeeRole.ADMIN)
            app.state.task_page_service = SessionTaskPageService(
                factory, PrivateFileStorage(tmp_path / "files"), 1024,
            )
            app.state.customer_admin_service = SessionCustomerAdminService(
                factory, SensitiveDataCipher(Fernet.generate_key().decode()),
            )
            app.state.admin_operations_service = SessionAdminOperationsService(factory)
            app.state.admin_dashboard_clock = lambda: observed
            app.state.hostex_data_last_success = observed
            with TestClient(app) as client:
                login_admin(client, next_path="/employee/tasks")
                yield client
        finally:
            worker.submit(asyncio.run, engine.dispose()).result()


def load_page(page, client, path, *, scripted=True):
    """加载真实响应且禁止出网；提交请求只用于统计，不连接外部服务。"""
    response = client.get(path)
    assert response.status_code == 200, response.text
    posts = []

    def deny_network(route):
        """记录浏览器是否真的发起写请求，并阻止一切出网。"""
        if route.request.method == "POST":
            posts.append(route.request.url)
        route.abort()

    page.route("**/*", deny_network)
    page.set_content(response.text)
    page.add_style_tag(content=ADMIN_CSS)
    page.evaluate("""() => {
      window.dialogs = [];
      window.alert = (text) => { dialogs.push(text); };
      window.confirm = (text) => { dialogs.push(text); return false; };
      window.prompt = (text) => { dialogs.push(text); return "wrong"; };
    }""")
    if scripted:
        page.add_script_tag(content=ADMIN_SCRIPT)
    return posts


def submitted_ids(page):
    """读取浏览器实际表单数据，验证双布局只提交一份任务编号。"""
    return page.evaluate("""() => new FormData(document.querySelector('[data-default-action]'))
      .getAll('task_ids')""")


@pytest.mark.parametrize("path", ["/employee/tasks", "/employee/customers/1?tab=service"])
def test_mixed_selection_blocks_before_dialog_or_request(admin_client, browser, path):  # noqa: F811
    """混选和程序化提交均先拦截；明确过滤后只处理合格项。"""
    if path == "/employee/tasks":
        empty_page = browser.new_page()
        try:
            posts = load_page(empty_page, admin_client, "/employee/tasks?property_id=99999")
            empty_page.evaluate("document.querySelector('[data-default-action]').requestSubmit()")
            assert empty_page.evaluate("window.dialogs") == []
            assert posts == []
            assert "请先勾选" in empty_page.locator("[data-selection-count]").inner_text()
        finally:
            empty_page.close()
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    try:
        posts = load_page(page, admin_client, path)
        form = page.locator("[data-default-action]")
        task_page = path == "/employee/tasks"
        action = "assign" if task_page else "archive"
        form.locator(f'[data-requires="{action}"]').click()
        assert "请先勾选" in form.locator("[data-selection-count]").inner_text()
        for task_id in ((12, 13) if task_page else (11, 12)):
            form.locator(f'.responsive-table input[value="{task_id}"]').check()
        expected_ids = {"12", "13"} if task_page else {"11", "12"}
        assert set(submitted_ids(page)) == expected_ids
        for blocked in (("assign",) if task_page else ("archive", "cancel")):
            assert form.locator(f'[data-requires="{blocked}"]').is_disabled()
            page.evaluate("""(action) => {
              const f = document.querySelector('[data-default-action]');
              f.requestSubmit(f.querySelector(`[data-requires="${action}"]`));
            }""", blocked)
        page.evaluate("document.querySelector('[data-default-action]').requestSubmit()")
        assert page.evaluate("window.dialogs") == []
        assert posts == []
        assert form.get_attribute("data-submitting") is None
        keep_action = "assign" if task_page else "cancel"
        form.locator(f'[data-keep-eligible="{keep_action}"]').click()
        assert submitted_ids(page) == ["12"]
        page.evaluate("""() => {
          const f = document.querySelector('[data-default-action]');
          f.requestSubmit(f.querySelector('[data-requires="cancel"]'));
        }""")
        assert len(page.evaluate("window.dialogs")) == 1
        assert "1 条" in page.evaluate("window.dialogs[0]")
        assert form.locator("[data-confirm-count]").input_value() == "0"
        if not task_page:
            form.locator('.responsive-table input[value="11"]').check()
            form.locator('[data-keep-eligible="archive"]').click()
            assert submitted_ids(page) == ["11"]
        # 正式会话门面只提交保留的合格项，反馈取实际数量且只显示一次。
        token = form.locator('input[name="csrf_token"]').input_value()
        payload = {"csrf_token": token, "return_to": path,
                   "task_ids": [12] if task_page else [11]}
        if task_page:
            payload["assigned_employee_id"] = 2
        response = admin_client.post(f"/employee/tasks/{action}-selected", data=payload,
                                     follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == path
        notice = "已分派 1 条" if task_page else "已归档 1 条"
        assert notice in admin_client.get(path).text
        assert notice not in admin_client.get(path).text
    finally:
        page.close()


@pytest.mark.parametrize("path", [
    "/employee/login", "/employee/tasks", "/employee/customers/1?tab=service",
    "/employee/admin/operations",
])
def test_pages_fit_available_width_with_vertical_scrollbar(admin_client, playwright_runtime, path):  # noqa: F811
    """预留真实滚动条宽度，防止 320px 视口被根元素最小宽度撑出横向滚动。"""
    # Playwright 默认 --hide-scrollbars 会抹掉现场槽位，只为此回归取消这一参数。
    instance = playwright_runtime.chromium.launch(
        headless=True, ignore_default_args=["--hide-scrollbars"],
    )
    page = instance.new_page(viewport={"width": 320, "height": 740})
    try:
        load_page(page, admin_client, path)
        # 即使认证页内容不够长，也预留槽位，统一复现现场 15px 的宽度损失。
        page.add_style_tag(content="html { scrollbar-gutter: stable; overflow-y: scroll; }")
        for width in (320, 360, 390, 1280):
            page.set_viewport_size({"width": width, "height": 740})
            dimensions = page.evaluate("""() => ({
              client: document.documentElement.clientWidth,
              scroll: document.documentElement.scrollWidth,
              content: document.body.getBoundingClientRect().width,
            })""")
            assert dimensions["client"] < width, "回归环境必须保留垂直滚动条槽位"
            assert dimensions["scroll"] <= dimensions["client"], dimensions
            assert dimensions["content"] <= dimensions["client"], dimensions
    finally:
        instance.close()


@pytest.mark.parametrize("path", ["/employee/tasks", "/employee/customers/1?tab=service"])
def test_mobile_toolbar_stays_in_view_without_covering_content(admin_client, browser, path):  # noqa: F811
    """首条选择立即出现操作栏；底部内容、触控区、选择和焦点均可达。"""
    page = browser.new_page(viewport={"width": 390, "height": 844})
    try:
        load_page(page, admin_client, path)
        form = page.locator("[data-default-action]")
        first = form.locator('.mobile-card-list input[name="task_ids"]').first
        first.check()
        page.wait_for_function("""document.querySelector('.selection-actions')
          .getBoundingClientRect().bottom <= innerHeight + 1""")
        toolbar = form.locator(".selection-actions")
        assert toolbar.bounding_box()["y"] >= 0
        assert first.bounding_box()["y"] >= page.locator(".topbar").bounding_box()["height"]
        assert first.locator("..").bounding_box()["height"] >= 44
        assert first.locator("..").bounding_box()["width"] >= 44
        assert form.locator(".selection-bar__all input").bounding_box()["width"] == 20
        last = form.locator('.mobile-card-list input[name="task_ids"]').last
        last.focus()
        last.scroll_into_view_if_needed()
        box = last.bounding_box()
        assert box["y"] + box["height"] <= toolbar.bounding_box()["y"]
        selected = submitted_ids(page)
        page.set_viewport_size({"width": 1280, "height": 900})
        page.wait_for_function("!document.querySelector('.selection-actions').hasAttribute('data-fixed-bulk')")
        assert submitted_ids(page) == selected
        page.set_viewport_size({"width": 390, "height": 844})
        form.locator("[data-select-clear]").click()
        assert submitted_ids(page) == []
        assert toolbar.get_attribute("data-fixed-bulk") is None
    finally:
        page.close()


@pytest.mark.parametrize("path, label", [
    ("/employee/tasks?archived=true", "可删除"),
    ("/employee/customers/1?tab=service", "可归档"),
])
def test_archived_task_labels_only_offer_actions_on_the_current_page(
    admin_client, browser, path, label,  # noqa: F811
):
    """真实归档后，两种布局的行提示只列本页动作，服务端资格仍完整保留。"""
    source = browser.new_page()
    try:
        load_page(source, admin_client, "/employee/tasks")
        token = source.locator('[data-default-action] input[name="csrf_token"]').input_value()
        response = admin_client.post("/employee/tasks/archive-selected",
                                     data={"csrf_token": token, "task_ids": [11]},
                                     follow_redirects=False)
        assert response.status_code == 303
    finally:
        source.close()
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    try:
        load_page(page, admin_client, path)
        box = page.locator('.responsive-table input[name="task_ids"][value="11"]')
        assert set(box.get_attribute("data-eligible").split()) == {"archive", "purge"}
        assert box.locator("xpath=ancestor::tr").locator(".task-eligibility").inner_text() == label
        if "archived=true" in path:
            assert "条可归档" not in page.locator(".field-help").all_inner_texts()[-1]
        else:
            pending = page.locator('.responsive-table input[name="task_ids"][value="12"]')
            pending_label = pending.locator("xpath=ancestor::tr").locator(".task-eligibility")
            assert pending_label.inner_text() == "可取消"
        page.set_viewport_size({"width": 390, "height": 844})
        mobile = page.locator('.mobile-card-list input[name="task_ids"][value="11"]')
        mobile_label = mobile.locator("xpath=ancestor::li").locator(".task-eligibility")
        assert mobile_label.inner_text() == label
    finally:
        page.close()


@pytest.mark.parametrize("path", ["/employee/tasks", "/employee/customers/1?tab=service"])
def test_resize_preserves_submitting_button_and_blocks_repeated_confirmation(
    admin_client, browser, path,  # noqa: F811
):
    """保留真实提交后的页面，验证布局切换不解锁忙态，重复入口不再弹确认。"""
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    try:
        posts = load_page(page, admin_client, path)
        form = page.locator("[data-default-action]")
        form.locator('.responsive-table input[value="12"]').check()
        page.evaluate("""() => {
          window.prompt = (text) => { dialogs.push(text); return '1'; };
          const form = document.querySelector('[data-default-action]');
          // 在正式忙态监听之后阻止导航，保留节点检查提交中的交互状态。
          form.addEventListener('submit', (event) => event.preventDefault());
          form.requestSubmit(form.querySelector('[data-requires="cancel"]'));
        }""")
        button = form.locator('[data-requires="cancel"]')
        page.wait_for_function("document.querySelector('[data-requires=cancel]').disabled")
        assert button.inner_text() == "正在处理…"
        assert len(page.evaluate("window.dialogs")) == 1
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_function("document.querySelector('.selection-actions').hasAttribute('data-fixed-bulk')")
        assert button.is_disabled()
        assert button.inner_text() == "正在处理…"
        assert submitted_ids(page) == ["12"]
        assert form.locator(".selection-actions").bounding_box()["height"] > 0
        page.evaluate("""() => {
          const form = document.querySelector('[data-default-action]');
          form.requestSubmit(form.querySelector('[data-requires="cancel"]'));
          form.requestSubmit();
        }""")
        assert len(page.evaluate("window.dialogs")) == 1
        assert posts == []
        page.set_viewport_size({"width": 1280, "height": 900})
        page.wait_for_function("!document.querySelector('.selection-actions').hasAttribute('data-fixed-bulk')")
        assert button.is_disabled()
        assert button.inner_text() == "正在处理…"
        assert submitted_ids(page) == ["12"]
    finally:
        page.close()


def test_crossing_checkout_keeps_unsaved_room_status(admin_client, browser):  # noqa: F811
    """用可控经过时间越过真实计划节点，有草稿时只提示并保留输入。"""
    page = browser.new_page(viewport={"width": 390, "height": 844})
    try:
        load_page(page, admin_client, "/employee/admin/operations", scripted=False)
        page.evaluate("""() => {
          window.elapsed = 0;
          Object.defineProperty(performance, 'now', {value: () => elapsed});
          window.ticks = [];
          window.setInterval = (fn) => { ticks.push(fn); return ticks.length; };
        }""")
        page.add_script_tag(content=ADMIN_SCRIPT)
        page.locator(".room-adjust > summary").first.click()
        draft = page.locator(".room-status-form select").first
        draft.select_option("maintenance")
        page.evaluate("() => { elapsed = 61000; ticks.forEach(fn => fn()); }")
        assert page.locator("[data-schedule-stale-hint]").is_visible()
        assert draft.input_value() == "maintenance"
        assert "退房待确认" in page.locator('.countdown[data-kind="checkout"]').inner_text()
        card = page.locator(".room-operation-card")
        assert card.get_attribute("aria-labelledby") == "room-title-101"
    finally:
        page.close()
