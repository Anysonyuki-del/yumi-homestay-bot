"""真实路由、会话门面与临时 SQLite 验证批量操作和未保存房态。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

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
    SessionAdminCsrfService,
    SessionAdminDashboardService,
    SessionAdminOperationsService,
    SessionApprovalPageService,
    SessionCustomerAdminService,
    SessionPropertyAdminService,
    SessionTaskPageService,
)
from homestay_bot.db import create_engine, create_session_factory
from homestay_bot.domain.enums import (
    ApprovalStatus,
    BusinessTaskStatus,
    BusinessTaskType,
    EmployeeRole,
)
from homestay_bot.domain.errors import OperationRefused
from homestay_bot.domain.models import (
    AdminCredential,
    Base,
    BookingApproval,
    BusinessTask,
    Conversation,
    Customer,
    CustomerTag,
    CustomerTagLink,
    Employee,
    PropertyProfile,
    StayOrder,
)
from homestay_bot.routes.admin import router as admin_router
from homestay_bot.routes.approvals import router as approvals_router
from homestay_bot.routes.customers import router as customers_router
from homestay_bot.routes.employee_auth import router as employee_auth_router
from homestay_bot.routes.page_errors import handle_operation_refused
from homestay_bot.routes.properties import router as properties_router
from homestay_bot.routes.tasks import router as tasks_router
from homestay_bot.services.approval_sensitive_data import ApprovalSensitiveData
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
            # 真实 CSRF 仓储外键绑定管理员凭证；登录仍由显式测试认证隔离。
            session.add(AdminCredential(id=1, employee_id=1, username="synthetic-admin",
                                        password_hash="synthetic-unused"))
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
            # 总览也使用真实只读聚合，主题同源验收不能总落到缺服务的安全空态。
            app.state.admin_dashboard_service = SessionAdminDashboardService(factory)
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


def test_summary_conflict_keeps_draft_and_requires_explicit_retry(admin_client, browser):  # noqa: F811
    """真实 SQLite 并发版本拒绝保留原稿，新令牌明确重试，第二次冲突仍不覆盖。"""
    page = browser.new_page()
    # 摘要专项使用真实 nonce 仓储，避免认证替身掩盖失败后令牌消费问题。
    admin_client.app.state.admin_csrf_service = SessionAdminCsrfService(
        admin_client.app.state.customer_admin_service._factory,
    )
    path = "/employee/customers/1?tab=memory"

    def form_from(response):
        """从实际响应取版本和客户族令牌。"""
        page.set_content(response.text)
        form = page.locator('.memory-edit form')
        return {"csrf_token": form.locator('[name=csrf_token]').input_value(),
                "expected_version": form.locator('[name=expected_version]').input_value()}

    def save(fields, short, long=""):
        """本地真实路由保存合成摘要，不调用模型。"""
        return admin_client.post("/employee/customers/1/summary",
            data={**fields, "short_summary": short, "long_summary": long},
            headers={"Accept": "text/html"}, follow_redirects=False)

    try:
        old = form_from(admin_client.get(path))
        other = form_from(admin_client.get(path))
        assert save(other, "最新要点一").status_code == 303
        draft = "\n  草稿 <script>alert(1)</script>\n保留空白  "
        conflict = save(old, draft, "\n  历史草稿  ")
        assert conflict.status_code == 409
        retry = form_from(conflict)
        assert page.locator('.memory-edit').get_attribute('open') is not None
        assert page.locator('[name=short_summary]').input_value() == draft
        assert page.locator('[name=long_summary]').input_value() == "\n  历史草稿  "
        assert page.locator('#handover').inner_text().count("最新要点一") == 1
        assert "草稿尚未保存" in page.locator('[role=alert]').inner_text()
        assert all("alert(1)" not in code for code in page.locator("script").all_text_contents())
        assert retry["csrf_token"] != old["csrf_token"]
        assert retry["expected_version"] == "1"
        replay = save(old, "令牌重放")
        assert replay.status_code == 409
        assert replay.json()["detail"] == "表单令牌无效或已使用"
        newer = form_from(admin_client.get(path))
        assert save(newer, "最新要点二").status_code == 303
        second_conflict = save(retry, draft)
        assert second_conflict.status_code == 409
        final_form = form_from(second_conflict)
        assert "最新要点二" in page.locator('#handover').inner_text()
        assert page.locator('[name=short_summary]').input_value() == draft
        assert final_form["expected_version"] == "2"
        assert save(final_form, "人工核对后保存").status_code == 303
        stored = admin_client.get(path)
        form_from(stored)
        assert "人工核对后保存" in page.locator('#handover').inner_text()
        assert page.locator('[name=expected_version]').input_value() == "3"
        current_form = form_from(stored)
        json_conflict = admin_client.post("/employee/customers/1/summary",
            data={**current_form, "expected_version": 0, "short_summary": "不能覆盖"},
            headers={"Accept": "application/json"}, follow_redirects=False)
        assert json_conflict.status_code == 409
        assert "detail" in json_conflict.json() and "summary_draft" not in json_conflict.json()
        assert "人工核对后保存" in admin_client.get(path).text
    finally:
        page.close()


@pytest.mark.parametrize("width", [320, 1280])
def test_customer_and_property_lists_keep_operating_details(admin_client, browser, tmp_path, width):  # noqa: F811
    """真实投影的日期、标签及房号房型在两种布局都完整可读，空值不编造。"""
    factory = admin_client.app.state.customer_admin_service._factory
    arrive = date.today() + timedelta(days=5)
    tags = ("SYNTHETIC_" * 6, "老客户", "携带宠物")

    async def seed_details():
        """合成长运营字段、下一次入住和没有资料的对照记录。"""
        async with factory() as session:
            room = await session.get(PropertyProfile, 101)
            room.room_type = "庭院家庭套房" * 8
            room.title = "庭院家庭套房合成房间" * 8
            session.add_all([PropertyProfile(id=202, title="空资料房间", is_active=True),
                             Customer(id=2, display_name="空资料客户")])
            for tag_id, name in enumerate(tags, 1):
                session.add(CustomerTag(id=tag_id, name=name))
            await session.flush()
            session.add_all([CustomerTagLink(customer_id=1, tag_id=i) for i in (1, 2, 3)])
            session.add(StayOrder(
                hostex_reservation_code="future-synthetic", stay_code="future-synthetic",
                property_id=101, customer_id=1, status="confirmed", check_in_date=arrive,
                check_out_date=arrive + timedelta(days=2)))
            await session.commit()

    with ThreadPoolExecutor(max_workers=1) as worker:
        worker.submit(asyncio.run, seed_details()).result()
        cards = worker.submit(
            asyncio.run, admin_client.app.state.customer_admin_service.list_customers(
                None, Employee(id=1, role=EmployeeRole.ADMIN, is_active=True), offset=0, limit=50
            ),
        ).result()
    customer = next(card for card in cards if card.id == 1)
    assert customer.stay_date_label and set(customer.tag_names) == set(tags)
    app = admin_client.app
    app.include_router(properties_router)
    app.state.property_admin_service = SessionPropertyAdminService(
        factory, SensitiveDataCipher(Fernet.generate_key().decode()),
        PrivateFileStorage(tmp_path / "property-files"), 1024)
    page = browser.new_page(viewport={"width": width, "height": 900})
    try:
        for path in ("/employee/customers", "/employee/properties"):
            # set_content 不重置全局脚本；切页先清空执行环境，避免第二页增强脚本重声明。
            page.goto("about:blank")
            load_page(page, admin_client, path)
            container = page.locator('.mobile-card-list' if width == 320 else '.responsive-table')
            assert container.is_visible()
            text = container.inner_text()
            if "customers" in path:
                assert customer.stay_date_label in text
                assert all(tag in text for tag in tags)
                assert "空资料客户" in text and "暂无入住记录" in text
            else:
                assert "101 · " + "庭院家庭套房" * 8 in text
                assert "下次入住" in text and str(arrive.day) + "日" in text
                assert "房号待补充 · 房型待补充" in text
            assert page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
            )
            preview = Path("/tmp/yumi-frontend-audit-fixes")
            preview.mkdir(exist_ok=True)
            page.screenshot(path=str(preview / f"{path.rsplit('/', 1)[-1]}-{width}.png"),
                            animations="disabled", full_page=True)
    finally:
        page.close()


@pytest.mark.parametrize("entry", ["click", "requestSubmit"])
def test_assign_requires_employee_before_confirmation(admin_client, browser, entry):  # noqa: F811
    """未选员工不弹确认、不提交、不丢选择；补选员工后恢复确认，取消不依赖员工。"""
    page = browser.new_page()
    try:
        posts = load_page(page, admin_client, "/employee/tasks")
        form = page.locator('[data-default-action]')
        form.locator('.responsive-table input[value="12"]').check()
        if entry == "click":
            form.locator('[data-requires=assign]').click()
        else:
            form.evaluate("f => f.requestSubmit(f.querySelector('[data-requires=assign]'))")
        assert page.evaluate("window.dialogs") == [] and posts == []
        assert form.get_attribute('data-submitting') is None
        assert submitted_ids(page) == ["12"]
        assert "选择" in form.locator('[data-bulk-hint=assign]').inner_text()
        assert form.locator('#bulk-assign-employee').evaluate(
            "node => document.activeElement === node"
        )
        form.locator('#bulk-assign-employee').select_option("2")
        assert form.locator('[data-bulk-hint=assign]').inner_text() == ""
        form.locator('[data-requires=assign]').click()
        assert "合成员工" in page.evaluate("window.dialogs[0]")
        form.locator('#bulk-assign-employee').select_option("")
        form.locator('[data-requires=cancel]').click()
        assert len(page.evaluate("window.dialogs")) == 2
        assert "取消" in page.evaluate("window.dialogs[1]")
    finally:
        page.close()


@pytest.mark.parametrize("theme", ["classic", "warm"])
def test_approval_price_groups_remain_readable_on_phone(admin_client, browser, theme):  # noqa: F811
    """两主题真实审批页保留房价归属、直订默认说明与空房提示，不撑破手机。"""
    from homestay_bot.integrations.hostex_client import IncomeMethod, ListingCalendarDay, Property

    factory = admin_client.app.state.customer_admin_service._factory
    sensitive = ApprovalSensitiveData(SensitiveDataCipher(Fernet.generate_key().decode()))
    day = date.today()
    long_id = "UNMATCHED_CHANNEL_" * 7

    async def seed_approval():
        """本地合成审批，所有客人资料均为测试内容。"""
        async with factory() as session:
            conversation = Conversation(open_kfid="synthetic", external_userid="synthetic")
            session.add(conversation)
            await session.flush()
            approval = BookingApproval(id=1, approval_code="SYNTHETIC-PRICE",
                conversation_id=conversation.id, status=ApprovalStatus.PENDING,
                check_in_date=day, check_out_date=day + timedelta(days=1),
                number_of_guests=2, room_type_preference="合成房型")
            sensitive.write(approval, guest_name="合成客人", guest_mobile="13800138000",
                            special_requests=None)
            session.add(approval)
            await session.commit()

    class Catalog:
        async def list_properties(self):
            """两间有参考价，一间无渠道。"""
            return [Property(id=i, title=title, channels=channels) for i, title, channels in [
                (101, "庭院房间", [{"channel_type": "booking_site", "listing_id": "same"}]),
                (201, "江景房间", [{"channel_type": "airbnb", "listing_id": "same",
                                       "currency": "USD"}]),
                (301, "空房间", [])]]

        async def list_reference_prices(self, start_date, end_date):
            """按真实渠道模型返回同日多房及长编号的未知归属。"""
            return [ListingCalendarDay(channel_type=c, listing_id=i, date=start_date,
                price=p, inventory=1) for c, i, p in [
                ("booking_site", "same", 300), ("airbnb", "same", 100),
                ("airbnb", long_id, 80)]]

        async def list_income_methods(self):
            """收入方式不涉及真实收款。"""
            return [IncomeMethod(id=1, name="合成收款方式")]

    class Registry:
        @asynccontextmanager
        async def acquire(self):
            """只替换真实外部客户端，审批会话门面与装配保持正式路径。"""
            yield SimpleNamespace(hostex=Catalog())

    with ThreadPoolExecutor(max_workers=1) as worker:
        worker.submit(asyncio.run, seed_approval()).result()
    admin_client.app.include_router(approvals_router)
    admin_client.app.state.approval_page_service = SessionApprovalPageService(
        factory=factory, registry=Registry(), sensitive_data=sensitive)
    page = browser.new_page(viewport={"width": 320, "height": 900})
    try:
        load_page(page, admin_client, "/employee/approvals/1")
        # 这里只检查币种说明的两主题布局；切换与存储由既有同源主题测试覆盖。
        page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
        section = page.locator('section').filter(
            has=page.get_by_role('heading', name="渠道日历参考价")
        )
        assert "庭院房间" in section.inner_text()
        default_price = section.get_by_text("¥300.0（直订默认人民币）", exact=True)
        assert default_price.is_visible()
        assert default_price.evaluate("""node => node.getBoundingClientRect().height
            <= parseFloat(getComputedStyle(node).lineHeight) * 2""")
        assert "江景房间" in section.inner_text() and "USD 100" in section.inner_text()
        assert "空房间" in section.inner_text() and "暂无参考价" in section.inner_text()
        assert section.get_by_text(long_id, exact=False).is_visible()
        unknown_price = section.get_by_text("币种未确认 80.0", exact=True)
        assert unknown_price.evaluate("""node => node.getBoundingClientRect().height
            <= parseFloat(getComputedStyle(node).lineHeight) * 2""")
        assert page.locator('[name=property_id]').input_value() == ""
        assert page.locator('[name=final_rate_amount]').input_value() == ""
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
        preview = Path("/tmp/yumi-frontend-audit-fixes")
        preview.mkdir(exist_ok=True)
        page.screenshot(path=str(preview / f"approval-currency-{theme}-320.png"),
                        full_page=True, animations="disabled")
    finally:
        page.close()


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
