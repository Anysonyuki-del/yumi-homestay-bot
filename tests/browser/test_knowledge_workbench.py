"""知识库工作区浏览器验收（2026-11-25 知识库重构 Spec §5.2）。

真实 FastAPI 路由与 `SessionKnowledgeAdminService`、临时 SQLite、私有图片目录，经 uvicorn
同源加载本站模板、样式与脚本，并套用生产同一份 `AdminNoStoreMiddleware`（含 EMPLOYEE_CSP）。
浏览器只访问本地临时服务，外网请求一律阻断；只用合成数据。
"""

import asyncio
import io
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import uvicorn
from admin_auth_helpers import configure_admin_auth, login_admin
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles

from homestay_bot.application import SessionKnowledgeAdminService
from homestay_bot.db import create_engine, create_session_factory
from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.domain.errors import OperationRefused
from homestay_bot.domain.models import (
    Base,
    Employee,
    KnowledgeEntry,
    KnowledgeImage,
    PropertyProfile,
)
from homestay_bot.middleware import AdminNoStoreMiddleware
from homestay_bot.routes.employee_auth import router as employee_auth_router
from homestay_bot.routes.knowledge import router as knowledge_router
from homestay_bot.routes.knowledge_referrals import router as referrals_router
from homestay_bot.routes.page_errors import handle_operation_refused
from homestay_bot.services.private_file_storage import PrivateFileStorage
from tests.browser.test_admin_interactions import (
    browser,  # noqa: F401
    playwright_runtime,  # noqa: F401
)

STATIC_ROOT = Path(__file__).resolve().parents[2] / "src/homestay_bot/static"
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 17 + b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
_QA = {"question_en": "Synthetic question?", "answer_en": "Synthetic answer."}
# 浏览器里收集 CSP 违规；页面脚本被 CSP 拦下时这里会记一条。
_CSP_PROBE = """
  window.__cspViolations = [];
  document.addEventListener('securitypolicyviolation', (event) => {
    window.__cspViolations.push(event.violatedDirective + ' ' + event.blockedURI);
  });
"""


def _in_thread(coroutine):
    """Playwright 同步接口占用了主线程的事件循环，异步调用放到独立线程里跑完。"""
    with ThreadPoolExecutor(max_workers=1) as worker:
        return worker.submit(asyncio.run, coroutine).result()


def _seed(db_path: Path, storage: PrivateFileStorage) -> dict[str, int]:
    """合成房间与四个范围的知识；房间知识带满 4 张真实可读的配图。"""
    files = [
        _in_thread(storage.save_image(io.BytesIO(PNG), "image/png", 10_000)).file_id
        for _ in range(4)
    ]
    engine = create_sync_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            Employee(id=1, wecom_userid="synthetic-admin", name="合成管理员",
                     role=EmployeeRole.ADMIN, is_active=True),
            PropertyProfile(id=1, room_number="201", title="合成201房", room_type="大床房",
                            district="合成区", address_hint="合成路 1 号",
                            parking_instructions="地下一层"),
            PropertyProfile(id=2, room_number=None, title="合成小院"),
            PropertyProfile(id=3, room_number="203", title="合成停用房", is_active=False),
        ])
        session.flush()
        entries = {
            "global": KnowledgeEntry(scope="global", category="停车", question_zh="停哪里？",
                                     answer_zh="地下一层。", keywords=[], **_QA),
            "room": KnowledgeEntry(scope="property", property_id=1, category="早餐",
                                   question_zh="早餐几点？", answer_zh="七点到九点。",
                                   keywords=[], **_QA),
            "public": KnowledgeEntry(scope="public", category="交通", question_zh="最近的地铁？",
                                     answer_zh="步行五分钟。", keywords=[], **_QA),
            "unreviewed": KnowledgeEntry(scope="unreviewed", category="设施",
                                         question_zh="洗衣机在哪？", answer_zh="一楼。",
                                         keywords=[], **_QA),
        }
        session.add_all(entries.values())
        session.flush()
        session.add_all([
            KnowledgeImage(knowledge_entry_id=entries["room"].id, file_id=file_id,
                           content_type="image/png", size=len(PNG), sort_order=order,
                           created_by=1)
            for order, file_id in enumerate(files, start=1)
        ])
        session.commit()
        ids = {key: entry.id for key, entry in entries.items()}
    engine.dispose()
    return ids


@pytest.fixture
def kb_site(tmp_path):
    """同源真实服务：登录后的管理员 Cookie、条目编号与读库函数。"""
    db_path = tmp_path / "kb.db"
    storage = PrivateFileStorage(tmp_path / "private")
    ids = _seed(db_path, storage)
    engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
    factory = create_session_factory(engine)
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="synthetic-session")
    app.add_middleware(AdminNoStoreMiddleware)
    app.add_exception_handler(OperationRefused, handle_operation_refused)
    app.include_router(employee_auth_router)
    app.include_router(knowledge_router)
    app.include_router(referrals_router)
    app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")
    configure_admin_auth(app, EmployeeRole.ADMIN)
    app.state.knowledge_admin_service = SessionKnowledgeAdminService(factory, storage)

    sync_engine = create_sync_engine(f"sqlite:///{db_path}")

    def read(statement):
        """同步读库，供断言使用。"""
        with Session(sync_engine) as session:
            return list(session.scalars(statement))

    client = TestClient(app)
    login_admin(client, next_path="/employee/knowledge")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off", ws="none"))
        worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]})
        worker.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and worker.is_alive() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert server.started, "临时同源服务未能在期限内启动"
            cookies = [
                {"name": key, "value": value, "url": origin}
                for key, value in client.cookies.items()
            ]
            yield origin, cookies, ids, read
        finally:
            server.should_exit = True
            worker.join(timeout=5)
            sync_engine.dispose()
            _in_thread(engine.dispose())


class _Probe:
    """一个隔离浏览器上下文：记录控制台错误、页面异常与 CSP 违规，阻断外网。"""

    def __init__(self, instance, site, *, width: int = 1440, java_script_enabled: bool = True):
        origin, cookies, _, _ = site
        self.origin = origin
        self.context = instance.new_context(
            viewport={"width": width, "height": 900},
            java_script_enabled=java_script_enabled,
        )
        self.context.add_cookies(cookies)
        self.context.add_init_script(_CSP_PROBE)
        self.errors: list[str] = []
        self.context.route(
            "**/*",
            lambda route: route.continue_()
            if route.request.url.startswith(origin + "/")
            else route.abort(),
        )
        self.page = self.context.new_page()
        self.page.on("console", lambda message: message.type == "error"
                     and self.errors.append(message.text))
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        # 确认弹窗走页内 <dialog>；这里兜底处理原生弹框，避免测试卡住。
        self.page.on("dialog", lambda dialog: dialog.accept())

    def goto(self, path: str) -> None:
        """打开本站页面并等待加载完成。"""
        self.page.goto(self.origin + path)

    def assert_clean(self) -> None:
        """零水平溢出、零控制台错误、零 CSP 违规、只有一个可见 h1。"""
        page = self.page
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        ), page.url
        assert self.errors == [], (page.url, self.errors)
        violations = page.evaluate("window.__cspViolations || []")
        assert violations == [], (page.url, violations)
        assert page.locator("h1:visible").count() == 1, page.url

    def close(self) -> None:
        """释放上下文。"""
        self.context.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_every_workspace_page_fits_without_errors_or_csp_violations(
    kb_site, browser, width  # noqa: F811
) -> None:
    """五个分区、房间工作台、新建页、详情页与资料核实页在两种宽度下都干净。"""
    _, _, ids, _ = kb_site
    probe = _Probe(browser, kb_site, width=width)
    try:
        for path in (
            "/employee/knowledge",
            "/employee/knowledge?section=rooms",
            "/employee/knowledge?section=rooms&room=1",
            "/employee/knowledge?section=public",
            "/employee/knowledge?section=unreviewed",
            "/employee/knowledge?view=candidates",
            "/employee/knowledge/new?scope=property&property_id=1",
            f"/employee/knowledge/{ids['room']}",
            "/employee/knowledge-referrals",
        ):
            probe.goto(path)
            probe.assert_clean()
    finally:
        probe.close()


def test_tabs_count_search_hits_and_link_across_sections(kb_site, browser) -> None:  # noqa: F811
    """搜索时各页签显示命中数；当前分区零命中时，空状态链接直达有命中的分区。"""
    probe = _Probe(browser, kb_site)
    page = probe.page
    try:
        probe.goto("/employee/knowledge?query=早餐")
        tabs = page.locator("nav.kb-tabs")
        assert tabs.get_by_role("link", name="全店通用 0 条").count() == 1
        assert tabs.get_by_role("link", name="按房间管理 1 条").count() == 1
        page.get_by_role("link", name="按房间管理中有 1 条匹配 → 去查看").click()
        page.wait_for_url("**section=rooms**")
        assert "query=%E6%97%A9%E9%A4%90" in page.url
        assert page.locator("#knowledge-entries").get_by_text("早餐几点？").count() == 1
        # 存量待审核条目的提醒条指向集中定界视图。
        probe.goto("/employee/knowledge")
        page.get_by_role("link", name="去集中定界").click()
        page.wait_for_url("**section=unreviewed**")
        assert page.get_by_text("洗衣机在哪？").count() == 1
        probe.assert_clean()
    finally:
        probe.close()


def test_room_directory_counts_and_resident_property_card(kb_site, browser) -> None:  # noqa: F811
    """房间目录显示条数与停用标识；选中单房后显示只读常驻资料卡与上下文新建按钮。"""
    probe = _Probe(browser, kb_site)
    page = probe.page
    try:
        probe.goto("/employee/knowledge?section=rooms")
        directory = page.get_by_role("navigation", name="房间目录")
        assert "全部房间知识" in directory.inner_text()
        assert directory.get_by_role("link", name="201 合成201房 1 条").count() == 1
        # 房号为空时只显示房名；停用房间带标识、0 条。
        assert directory.get_by_role("link", name="合成小院 0 条").count() == 1
        assert directory.get_by_role("link", name="203 合成停用房 已停用 0 条").count() == 1

        directory.get_by_role("link", name="201 合成201房 1 条").click()
        page.wait_for_url("**room=1**")
        card = page.locator(".kb-property-card")
        for fact in ("201 · 合成201房", "大床房", "合成区", "合成路 1 号", "地下一层"):
            assert fact in card.inner_text()
        new_button = page.get_by_role("link", name="+ 为【201 · 合成201房】新增知识")
        assert new_button.count() == 1
        assert page.locator(".kb-entry").get_by_text("配图 4 张").count() == 1
        new_button.click()
        page.wait_for_url("**/employee/knowledge/new?**")
        assert page.locator('input[name="property_ids"][value="1"]').is_checked()
    finally:
        probe.close()


def test_new_page_scope_switch_and_multi_room_create(kb_site, browser) -> None:  # noqa: F811
    """归属三选一联动显隐房间区；全选只选在用房；多房一次提交建成各房一条。"""
    _, _, _, read = kb_site
    probe = _Probe(browser, kb_site, width=390)
    page = probe.page
    try:
        probe.goto("/employee/knowledge/new")
        rooms = page.locator(".kb-room-picker")
        assert page.locator('input[name="scope"][value="global"]').is_checked()
        assert not rooms.is_visible()
        page.get_by_text("指定房间", exact=True).click()
        assert rooms.is_visible()
        page.get_by_role("button", name="全选在用房").click()
        checked = page.locator('input[name="property_ids"]:checked')
        assert sorted(checked.evaluate_all("boxes => boxes.map(b => b.value)")) == ["1", "2"]
        # 切走后房间区隐藏且控件停用，不会随表单提交；切回来勾选仍在。
        page.get_by_text("店外周边", exact=True).click()
        assert not rooms.is_visible()
        assert page.locator('input[name="property_ids"][value="1"]').is_disabled()
        page.get_by_text("指定房间", exact=True).click()

        page.fill('input[name="category"]', "合成分类")
        page.fill('textarea[name="question_zh"]', "合成多房问题？")
        page.fill('textarea[name="answer_zh"]', "合成多房答案。")
        page.get_by_role("button", name="保存知识").click()
        page.wait_for_url("**section=rooms")
        assert "已为 2 间房各保存为停用草稿，补全英文后才能启用" in page.inner_text("body")
        created = read(
            select(KnowledgeEntry).where(KnowledgeEntry.question_zh == "合成多房问题？")
        )
        assert sorted((entry.property_id, entry.is_enabled) for entry in created) == [
            (1, False), (2, False),
        ]
        probe.assert_clean()
    finally:
        probe.close()


def test_new_page_without_javascript_shows_all_fields_and_server_rejects_mixups(
    kb_site, browser  # noqa: F811
) -> None:
    """无脚本时房间区与小程序勾选都可见可提交；选全店通用又勾了房间由服务端原地拒绝。"""
    _, _, _, read = kb_site
    probe = _Probe(browser, kb_site, java_script_enabled=False)
    page = probe.page
    try:
        probe.goto("/employee/knowledge/new")
        assert page.locator(".kb-room-picker").is_visible()
        assert not page.get_by_role("button", name="全选在用房").is_visible()
        page.locator('input[name="property_ids"][value="2"]').check()
        page.fill('input[name="category"]', "合成分类")
        page.fill('textarea[name="question_zh"]', "无脚本问题？")
        page.fill('textarea[name="answer_zh"]', "无脚本答案。")
        page.get_by_role("button", name="保存知识").click()
        page.wait_for_load_state()
        body = page.inner_text("body")
        assert "全店通用 / 店外周边不能指定房间" in body
        assert page.locator('textarea[name="question_zh"]').input_value() == "无脚本问题？"
        assert page.locator('input[name="property_ids"][value="2"]').is_checked()
        assert read(
            select(KnowledgeEntry).where(KnowledgeEntry.question_zh == "无脚本问题？")
        ) == []
    finally:
        probe.close()


def test_detail_page_toggles_in_place_and_guards_unsaved_edits(
    kb_site, browser  # noqa: F811
) -> None:
    """详情页直接停用：左侧有未保存修改时先确认；完成后回到状态区并显示提示。满 4 张无上传。"""
    _, _, ids, read = kb_site
    probe = _Probe(browser, kb_site)
    page = probe.page
    try:
        probe.goto(f"/employee/knowledge/{ids['room']}")
        assert page.locator('form[action$="/images/upload"]').count() == 0
        assert "每条最多 4 张" in page.locator("#knowledge-images").inner_text()

        page.fill('textarea[name="answer_zh"]', "还没保存的修改")
        page.get_by_role("button", name="立即停用").click()
        dialog = page.locator("[data-confirm-dialog]")
        assert dialog.is_visible()
        assert "停用" in dialog.locator("[data-confirm-message]").inner_text()
        dialog.locator("[data-confirm-ok]").click()
        assert "未保存" in dialog.locator("[data-confirm-message]").inner_text()
        dialog.locator("[data-confirm-ok]").click()

        page.wait_for_url(f"**/employee/knowledge/{ids['room']}#entry-status")
        assert f"已停用知识 #{ids['room']}" in page.inner_text("body")
        assert page.get_by_role("button", name="立即启用").is_visible()
        entry = read(select(KnowledgeEntry).where(KnowledgeEntry.id == ids["room"]))[0]
        assert (entry.is_enabled, entry.answer_zh) == (False, "七点到九点。")
        probe.assert_clean()
    finally:
        probe.close()
