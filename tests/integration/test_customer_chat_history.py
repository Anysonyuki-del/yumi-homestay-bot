"""客户档案「对话记录」页签（2026-09-29）：真实客户服务、SQLite 与页面渲染，只用合成数据。

员工接手转人工时要能在后台快速看完客人的历史对话：按时间顺序、带武汉时间和日期分隔，
区分客人、机器人和人工客服，正文转义显示，分页不漏不重，只有管理员能看。
"""

import asyncio
import re
from datetime import UTC, datetime, timedelta

from admin_auth_helpers import configure_admin_auth
from cryptography.fernet import Fernet
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.middleware.sessions import SessionMiddleware

from homestay_bot.application import SessionCustomerAdminService
from homestay_bot.domain.enums import ConversationMode, EmployeeRole, Language, MessageOrigin
from homestay_bot.domain.models import Base, Conversation, Customer, Message
from homestay_bot.routes.customers import router as customers_router
from homestay_bot.services.sensitive_data import SensitiveDataCipher


def _world(tmp_path, extra_messages: int = 0):
    """合成客户 7：一个转人工中的会话，跨两天的客人、机器人、人工与图片消息。

    另有客户 8 的一条消息，用来确认不会串到客户 7 的页面。
    """
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}", poolclass=NullPool
    )
    # 武汉时间 9 月 28 日 23:59 与 9 月 29 日 08:00（库里存 UTC）。
    day_one = datetime(2026, 9, 28, 15, 59, tzinfo=UTC)
    day_two = datetime(2026, 9, 29, 0, 0, tzinfo=UTC)

    async def setup() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine)() as session:
            session.add_all([Customer(id=7, display_name="合成客人"),
                             Customer(id=8, display_name="别的客人")])
            await session.flush()
            session.add_all([
                Conversation(id=1, customer_id=7, open_kfid="kf", external_userid="wm-7",
                             language=Language.ZH, mode=ConversationMode.HUMAN_ACTIVE),
                Conversation(id=2, customer_id=8, open_kfid="kf", external_userid="wm-8",
                             language=Language.ZH),
            ])
            await session.flush()

            def message(msgid, origin, text, at, *, kind="text", metadata=None):
                return Message(conversation_id=1, external_message_id=msgid, origin=origin,
                               message_type=kind, content=text, sent_at=at,
                               message_metadata=metadata or {})

            session.add_all([
                message("g-1", MessageOrigin.GUEST, "开车停哪里<script>", day_one),
                message("b-1", MessageOrigin.BOT, "停地下一层A区。", day_one),
                message("b-2", MessageOrigin.BOT, "[图片]", day_one, kind="image",
                        metadata={"image_file_id": "0" * 32 + ".jpg"}),
                message("g-2", MessageOrigin.GUEST, "转人工", day_two),
                message("s-1", MessageOrigin.SERVICER, "您好，我是管家。", day_two,
                        metadata={"delivery_status": "failed"}),
                message("g-3", MessageOrigin.GUEST, None, day_two,
                        metadata={"cleared_by": "test_data_reset"}),
                Message(conversation_id=2, external_message_id="other-1",
                        origin=MessageOrigin.GUEST, message_type="text",
                        content="别人的消息", sent_at=day_two, message_metadata={}),
            ])
            for index in range(extra_messages):
                session.add(message(f"x-{index}", MessageOrigin.GUEST, f"追加消息{index:03d}",
                                    day_two + timedelta(minutes=index + 1)))
            await session.commit()

    asyncio.run(setup())
    service = SessionCustomerAdminService(
        async_sessionmaker(engine, expire_on_commit=False),
        SensitiveDataCipher(Fernet.generate_key().decode("ascii")),
    )
    return engine, service


def _client(role: EmployeeRole, service) -> TestClient:
    """带测试登录入口的客户页面应用，装配真实客户服务。"""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="chat-test-secret")
    app.include_router(customers_router)
    configure_admin_auth(app, role)
    app.state.customer_admin_service = service

    @app.post("/test/login")
    async def test_login(request: Request) -> dict[str, bool]:
        request.session["employee_id"] = 1 if role is EmployeeRole.ADMIN else 2
        request.session["employee_role"] = role.value
        request.session["admin_id"] = 1
        request.session["admin_session_version"] = 1
        request.session["last_activity_at"] = datetime.now(UTC).isoformat()
        return {"ok": True}

    client = TestClient(app)
    client.post("/test/login")
    return client


def test_chat_tab_shows_the_whole_timeline_in_wuhan_time(tmp_path) -> None:
    """按时间正序显示发送方、武汉时间与日期分隔；正文转义；清空、图片、失败都有标注。"""
    engine, service = _world(tmp_path)
    page = _client(EmployeeRole.ADMIN, service).get("/employee/customers/7?tab=chat")

    assert page.status_code == 200
    text = page.text
    assert 'href="/employee/customers/7?tab=chat" aria-current="page"' in text
    order = [text.index(item) for item in (
        "2026年9月28日", "开车停哪里&lt;script&gt;", "停地下一层A区。",
        "[图片：知识配图或欢迎图片]",
        "2026年9月29日", "转人工", "您好，我是管家。", "（内容已清空）",
    )]
    assert order == sorted(order)
    assert "<script>" not in text.split("对话记录", 1)[1]
    assert "23:59:00" in text and "08:00:00" in text
    assert text.count('class="chat-day"') == 2
    assert "YuMi 机器人" in text and "人工客服" in text and "发送失败" in text
    # 转人工中的会话可以就地交还机器人；别的客户的消息不会出现。
    assert "/employee/customers/7/conversations/1/release" in text
    assert "别人的消息" not in text
    asyncio.run(engine.dispose())


def test_chat_tab_pages_backwards_without_gaps_or_repeats(tmp_path) -> None:
    """超过一页时最新一页在前；「查看更早消息」接着往前翻，两页合起来不漏不重。"""
    engine, service = _world(tmp_path, extra_messages=120)
    client = _client(EmployeeRole.ADMIN, service)

    latest = client.get("/employee/customers/7?tab=chat").text
    link = re.search(
        r'href="(/employee/customers/7\?tab=chat&(?:amp;)?before_message_id=\d+)#chat"', latest
    )
    assert link is not None and "回到最新" not in latest
    older = client.get(link.group(1).replace("&amp;", "&")).text

    def shown(html: str) -> list[str]:
        return re.findall(r"追加消息\d{3}|开车停哪里|停地下一层A区。|您好，我是管家。", html)

    newest, earlier = shown(latest), shown(older)
    assert len(latest.split('class="chat-message ')) - 1 == 100
    assert newest[-1] == "追加消息119"
    assert not set(newest) & set(earlier)
    assert earlier[0] == "开车停哪里" and "回到最新" in older
    assert len(newest) + len(earlier) == 123  # 120 条追加 + 3 条文字正文
    asyncio.run(engine.dispose())


def test_staff_cannot_open_the_chat_tab(tmp_path) -> None:
    """客户档案只有管理员能看，对话记录同样如此。"""
    engine, service = _world(tmp_path)
    response = _client(EmployeeRole.STAFF, service).get("/employee/customers/7?tab=chat")
    assert response.status_code == 403
    assert "开车停哪里" not in response.text
    asyncio.run(engine.dispose())
