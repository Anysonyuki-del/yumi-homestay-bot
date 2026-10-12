"""资料核实后台视图（修复 Spec 2026-10-08 fix2 §2.6(5)）：真实路由、真实服务与临时 SQLite。

列表、详情、点选范围入库、关闭、无脚本直接提交被服务端拒绝、失败保留输入、手机宽度。
"""

import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import pytest
from admin_auth_helpers import configure_admin_auth, login_admin
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from starlette.middleware.sessions import SessionMiddleware

from homestay_bot.application import SessionKnowledgeAdminService
from homestay_bot.db import create_engine, create_session_factory
from homestay_bot.domain.enums import EmployeeRole, KnowledgeReferralStatus, MessageOrigin
from homestay_bot.domain.errors import OperationRefused
from homestay_bot.domain.models import (
    Base,
    Conversation,
    Customer,
    Employee,
    Job,
    KnowledgeEntry,
    KnowledgeReferral,
    Message,
    PropertyProfile,
)
from homestay_bot.routes.employee_auth import router as employee_auth_router
from homestay_bot.routes.knowledge import router as knowledge_router
from homestay_bot.routes.knowledge_referrals import router as referrals_router
from homestay_bot.routes.page_errors import handle_operation_refused
from tests.browser.test_admin_interactions import (
    ADMIN_CSS,
    browser,  # noqa: F401
    playwright_runtime,  # noqa: F401
)

_DRAFT = {
    "guest_only": True, "category": "设施", "question_zh": "健身房几点开门？",
    "answer_zh": "健身房每天早上七点开门。", "question_en": "When does the gym open?",
    "answer_en": "The gym opens at 7 a.m.", "keywords": ["健身房"],
    "source_quotes": [{"text": "健身房每天早上七点开门", "message_id": 3}],
    "used_message_ids": [3], "truncated": False, "model_drafted": True,
}


@pytest.fixture
def referral_client(tmp_path, request):
    """一条待处理（有管家回复）与一条已整理的转交。"""
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'referrals.db'}")
    factory = create_session_factory(engine)

    async def seed():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            customer = Customer(display_name="合成客人")
            session.add_all([
                customer, PropertyProfile(id=401, room_number="401", title="江景房"),
                # 审计与知识的操作人外键指向员工表：测试管理员登录后的员工编号为 1。
                Employee(id=1, wecom_userid="admin", name="合成管理员", role=EmployeeRole.ADMIN),
            ])
            await session.flush()
            conversation = Conversation(open_kfid="kf", external_userid="guest",
                                        customer_id=customer.id)
            # 第二条转交在另一位客人的会话里，转交之后没有管家回复。
            quiet = Conversation(open_kfid="kf", external_userid="quiet-guest",
                                 customer_id=customer.id)
            session.add_all([conversation, quiet])
            await session.flush()
            now = datetime.now(UTC)
            session.add_all([
                Message(conversation_id=conversation.id, external_message_id="g-1",
                        origin=MessageOrigin.GUEST, message_type="text", content="健身房几点开门",
                        sent_at=now - timedelta(minutes=5)),
                Message(conversation_id=quiet.id, external_message_id="g-2",
                        origin=MessageOrigin.GUEST, message_type="text", content="401能看江景吗",
                        sent_at=now - timedelta(minutes=4)),
            ])
            await session.flush()
            session.add_all([
                KnowledgeReferral(
                    conversation_id=conversation.id, source_message_id="g-1", unit_id="1:0-7",
                    plan_item_id=1, question="健身房几点开门", canonical_question="健身房开放时间",
                    normalized_question="健身房开放时间", category="设施",
                    status=KnowledgeReferralStatus.DRAFTED, generation=1,
                    source_message_ids=[3], draft=_DRAFT,
                ),
                KnowledgeReferral(
                    conversation_id=quiet.id, source_message_id="g-2", unit_id="1:0-8",
                    plan_item_id=1, question="401能看江景吗", canonical_question="401是否江景",
                    normalized_question="401是否江景", category="房间", property_id=401,
                ),
            ])
            await session.flush()
            session.add(Message(
                conversation_id=conversation.id, external_message_id="s-1",
                origin=MessageOrigin.SERVICER, message_type="text",
                content="健身房每天早上七点开门", sent_at=now + timedelta(minutes=1),
            ))
            scenario = getattr(request, "param", None)
            if scenario in {"terminal", "open"}:
                # 最旧开放记录必须能被真实分页找到，不能只核列表计数。
                for index in range(100):
                    session.add(KnowledgeReferral(
                        conversation_id=quiet.id, source_message_id="g-2",
                        unit_id=f"extra-{index}", plan_item_id=1,
                        question=f"合成问题 {index}", canonical_question="合成问题",
                        normalized_question="合成问题", category="设施",
                        status=(KnowledgeReferralStatus.CLOSED if scenario == "terminal"
                                else KnowledgeReferralStatus.PENDING),
                        created_at=now + timedelta(minutes=index + 2),
                    ))
                # 只留下编号 1 作为最旧开放；open 场景总共 101 条开放。
                second = await session.get(KnowledgeReferral, 2)
                second.status = KnowledgeReferralStatus.CLOSED
            if scenario == "placeholder":
                session.add(KnowledgeEntry(
                    category="交通", question_zh="停车费", answer_zh="【待管理员确认】",
                    question_en="Parking fee?", answer_en="Pending review.",
                    keywords=["停车"], scope="global", is_enabled=False,
                ))
            await session.commit()
        await engine.dispose()

    async def count(model, **where):
        async with factory() as session:
            statement = select(func.count()).select_from(model)
            for key, value in where.items():
                statement = statement.where(getattr(model, key) == value)
            result = await session.scalar(statement)
        await engine.dispose()
        return result

    async def referral(referral_id: int):
        async with factory() as session:
            found = await session.get(KnowledgeReferral, referral_id)
        await engine.dispose()
        return found

    with ThreadPoolExecutor(max_workers=1) as worker:
        try:
            worker.submit(asyncio.run, seed()).result()
            app = FastAPI()
            app.add_middleware(SessionMiddleware, secret_key="synthetic-session")
            app.add_exception_handler(OperationRefused, handle_operation_refused)
            app.include_router(employee_auth_router)
            app.include_router(knowledge_router)
            app.include_router(referrals_router)
            configure_admin_auth(app, EmployeeRole.ADMIN)
            app.state.knowledge_admin_service = SessionKnowledgeAdminService(factory)
            with TestClient(app) as client:
                login_admin(client, next_path="/employee/knowledge-referrals")
                yield client, (
                    lambda model, **where: worker.submit(asyncio.run, count(model, **where))
                    .result()
                ), (lambda rid: worker.submit(asyncio.run, referral(rid)).result())
        finally:
            worker.submit(asyncio.run, engine.dispose()).result()


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def _convert_form(token: str, **overrides: str) -> dict[str, str]:
    form = {
        "csrf_token": token, "generation": "1", "category": "设施",
        "question_zh": "健身房几点开门？", "answer_zh": "健身房每天早上七点开门（已核）。",
        "question_en": "When does the gym open?", "answer_en": "The gym opens at 7 a.m.",
        "keywords": "健身房", "scope_choice": "global", "property_id": "", "reviewed": "1",
    }
    form.update(overrides)
    return {key: value for key, value in form.items() if value is not None}


def _render(browser, html: str, width: int = 390):  # noqa: F811
    """载入真实响应与样式，阻止额外请求。"""
    page = browser.new_page(viewport={"width": width, "height": 844})
    page.route("**/*", lambda route: route.abort())
    page.set_content(html)
    page.add_style_tag(content=ADMIN_CSS)
    return page


def test_list_shows_open_count_and_reply_count_on_phone(referral_client, browser) -> None:  # noqa: F811
    """列表显示待处理数量与「管家已回复 N 条」；手机宽度无横向滚动。"""
    client, _, _ = referral_client
    response = client.get("/employee/knowledge-referrals")
    assert response.status_code == 200
    page = _render(browser, response.text)
    try:
        body = page.locator("body").inner_text()
        assert "2 条待处理" in body
        assert "管家已回复 1 条" in body and "管家已回复 0 条" in body
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        page.close()


def test_detail_shows_sources_side_by_side_and_scope_has_no_default(
    referral_client, browser  # noqa: F811
) -> None:
    """详情并列展示草稿、依据片段与「只对这位客人成立」提示；范围单选没有默认值、必选。"""
    client, _, _ = referral_client
    page = _render(browser, client.get("/employee/knowledge-referrals/1").text)
    try:
        body = page.locator("body").inner_text()
        assert "健身房每天早上七点开门" in body and "可能只对这位客人" in body
        assert "模型已整理，待管理员确认" in body
        radios = page.locator('input[name="scope_choice"]')
        assert radios.count() == 2
        assert page.locator('input[name="scope_choice"]:checked').count() == 0
        assert radios.first.get_attribute("required") is not None
        assert page.locator('input[name="reviewed"]').get_attribute("required") is not None
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        page.close()


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"reviewed": None}, "已核对"),
        ({"scope_choice": None}, "仅此房间"),
        ({"scope_choice": "property"}, "选定房间"),
    ],
)
def test_direct_post_without_required_choices_is_rejected_and_keeps_input(
    referral_client, browser, overrides, error  # noqa: F811
) -> None:
    """不经页面脚本直接提交：未勾选、未选范围、仅此房间未选房间都被服务端拒绝；保留输入。"""
    client, count, _ = referral_client
    token = _csrf(client.get("/employee/knowledge-referrals/1").text)
    response = client.post(
        "/employee/knowledge-referrals/1/convert",
        data=_convert_form(token, **overrides),
        headers={"accept": "text/html"},
    )
    assert response.status_code == 422
    assert count(KnowledgeEntry) == 0
    page = _render(browser, response.text)
    try:
        assert error in page.get_by_role("alert").first.inner_text()
        assert page.locator('textarea[name="answer_zh"]').input_value() == (
            "健身房每天早上七点开门（已核）。"
        )
    finally:
        page.close()


def test_convert_with_scope_creates_enabled_knowledge(referral_client) -> None:
    """点选全店并勾选已核对后入库：建启用知识，记录为已入库。"""
    client, count, referral = referral_client
    token = _csrf(client.get("/employee/knowledge-referrals/1").text)
    response = client.post(
        "/employee/knowledge-referrals/1/convert", data=_convert_form(token),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert count(KnowledgeEntry, is_enabled=True) == 1
    assert referral(1).status is KnowledgeReferralStatus.CONVERTED
    assert "已入库" in client.get("/employee/knowledge-referrals/1").text


def test_draft_and_close_actions(referral_client) -> None:
    """整理草稿：换代、冻结来源并入队；关闭：写入抑制，状态为已关闭。"""
    client, count, referral = referral_client
    token = _csrf(client.get("/employee/knowledge-referrals/2").text)
    response = client.post("/employee/knowledge-referrals/2/draft", data={"csrf_token": token},
                           follow_redirects=False)
    assert response.status_code == 303
    # 这条转交之后没有管家回复：直接记为没找到答案，不入队模型任务。
    assert referral(2).status is KnowledgeReferralStatus.NO_ANSWER
    assert count(Job, job_type="knowledge_referral_draft") == 0
    token = _csrf(client.get("/employee/knowledge-referrals/2").text)
    client.post("/employee/knowledge-referrals/2/close", data={"csrf_token": token})
    assert referral(2).status is KnowledgeReferralStatus.CLOSED
    detail = client.get("/employee/knowledge-referrals/2").text
    assert 'action="/employee/knowledge-referrals/2/close"' not in detail


@pytest.mark.parametrize("referral_client", ["terminal", "open"], indirect=True)
def test_oldest_open_record_can_be_found_and_opened(referral_client, browser) -> None:  # noqa: F811
    """浏览器经真实路由翻页和打开详情，覆盖终态挤压与 101 条开放记录。"""
    client, _, _ = referral_client
    page = browser.new_page(viewport={"width": 390, "height": 844})

    def serve(route):
        """仅将本地浏览器 HTTP 交给已登录的真实测试服务，不制造页面正文。"""
        url = urlsplit(route.request.url)
        response = client.get(url.path + (f"?{url.query}" if url.query else ""))
        route.fulfill(status=response.status_code, body=response.content,
                      content_type=response.headers.get("content-type", "text/plain"))

    page.route("http://local.test/**", serve)
    try:
        page.goto("http://local.test/employee/knowledge-referrals")
        oldest = page.locator('a[href="/employee/knowledge-referrals/1"]')
        for _ in range(2):
            if oldest.count():
                break
            page.get_by_role("link", name="下一页").click()
        assert oldest.count() == 1
        oldest.click()
        assert page.url.endswith("/employee/knowledge-referrals/1")
        assert "健身房每天早上七点开门" in page.locator("body").inner_text()
    finally:
        page.close()


@pytest.mark.parametrize("referral_client", ["placeholder"], indirect=True)
@pytest.mark.parametrize("accept", ["text/html", "application/json"])
def test_enable_refusal_preserves_source_and_consumes_csrf_once(referral_client, accept) -> None:
    """真实启用校验拒绝历史占位符：HTML 保留筛选锚点，API 422，旧令牌不能重放。"""
    client, count, _ = referral_client
    source = "/employee/knowledge?category=交通&page=1#knowledge-entries"
    token = _csrf(client.get("/employee/knowledge?category=交通").text)
    form = {"csrf_token": token, "return_to": source}
    response = client.post("/employee/knowledge/1/enable", data=form,
                           headers={"accept": accept}, follow_redirects=False)
    assert count(KnowledgeEntry, is_enabled=True) == 0
    if accept == "text/html":
        assert response.status_code == 303
        assert response.headers["location"] == source.replace("交通", "%E4%BA%A4%E9%80%9A")
        assert "待管理员确认" in client.get(response.headers["location"]).text
    else:
        assert response.status_code == 422 and "待管理员确认" in response.json()["detail"]
    replay = client.post("/employee/knowledge/1/enable", data=form,
                         headers={"accept": "application/json"})
    assert replay.status_code == 409
