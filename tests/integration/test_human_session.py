"""管家接入 / 交还企业微信原生人工会话的状态流转；企业微信接口用内存桩，不访问外部服务。"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import ConversationMode, MessageOrigin
from homestay_bot.domain.models import AuditLog, Base, Conversation, Job, Message
from homestay_bot.integrations.wecom.api_client import WeComApiError
from homestay_bot.repositories.operations import (
    KF_SESSION_END_JOB_TYPE,
    SQLAlchemyOperationsRepository,
)
from homestay_bot.services.handoff_card import ACCEPTED_TEXT, ENDED_TEXT, HandoffCard
from homestay_bot.services.human_session import HumanSessionService


class KfStub:
    """按实测规则模拟会话状态：转人工进 3，结束进 4；每次状态变更给一个 msg_code。"""

    def __init__(self, state: int = 1, servicer: str = "", reject: int | None = None) -> None:
        self.state, self.servicer, self.reject = state, servicer, reject
        self.event_texts: list[tuple[str, str]] = []
        self.cards: list[dict[str, Any]] = []

    async def get_service_state(self, open_kfid: str, external_userid: str) -> tuple[int, str]:
        return self.state, self.servicer

    async def transfer_service_state(
        self, open_kfid: str, external_userid: str, userid: str
    ) -> str:
        if self.reject is not None:
            raise WeComApiError(self.reject, "not servicer")
        self.state, self.servicer = 3, userid
        return "code-accept"

    async def end_service_state(self, open_kfid: str, external_userid: str) -> str:
        self.state = 4
        return "code-end"

    async def send_event_text(self, code: str, content: str) -> str:
        self.event_texts.append((code, content))
        return f"evt-{len(self.event_texts)}"

    async def update_template_card(self, **kwargs: Any) -> None:
        self.cards.append(kwargs)


async def _card(conversation: Conversation) -> HandoffCard:
    """更新卡片用的固定内容。"""
    return HandoffCard(
        conversation_id=conversation.id,
        reason="需要人工跟进：价格协商",
        guest="客人：测试",
        link="https://example.invalid/c",
    )


async def _setup(tmp_path, *, mode=ConversationMode.HUMAN_ACTIVE):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'human.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session = factory()
    conversation = Conversation(open_kfid="wk", external_userid="wm", mode=mode)
    session.add(conversation)
    await session.flush()
    await SQLAlchemyOperationsRepository(session).record_handoff(
        conversation_id=conversation.id, customer_id=None, reason="price"
    )
    return engine, session, conversation


def _click(key: str, userid: str = "duty-1") -> dict[str, str]:
    return {
        "userid": userid,
        "key": key,
        "task_id": "handoff-1",
        "response_code": "rc",
        "agent_id": "1000002",
    }


async def test_accept_then_card_release_ends_native_session_once(tmp_path):
    """接入：转给点按钮的管家、客人收到接管提示、卡片换成交还按钮、机器人进入静默；
    交还：切回机器人并登记一次结束任务，任务结束原生会话并发结束语；重复执行无副作用。"""
    engine, session, conversation = await _setup(tmp_path)
    kf = KfStub()
    service = HumanSessionService(
        session, kf, agent_id=1000002, duty_userids=("duty-1",), card_for=_card
    )
    operations = SQLAlchemyOperationsRepository(session)

    await service.handle_card_action(_click(f"accept:{conversation.id}"))

    assert (kf.state, kf.servicer) == (3, "duty-1")
    assert kf.event_texts == [("code-accept", ACCEPTED_TEXT)]
    assert await operations.native_session_active(conversation.id)
    # 接入沿用原接管原因，客诉 / 紧急情况接入后仍不会被自动交还。
    assert await operations.latest_handoff_reason(conversation.id) == "price"
    accepted_card = kf.cards[-1]["template_card"]
    assert accepted_card["button_list"][0]["key"] == f"release:{conversation.id}"
    assert "replace_text" not in accepted_card
    recorded = await session.scalar(select(Message).where(Message.external_message_id == "evt-1"))
    assert recorded is not None and recorded.origin is MessageOrigin.BOT

    await service.handle_card_action(_click(f"release:{conversation.id}"))

    assert conversation.mode is ConversationMode.BOT_ACTIVE
    assert not await operations.native_session_active(conversation.id)
    assert kf.cards[-1]["template_card"]["replace_text"] == "已交还 AI 助手"
    jobs = (await session.scalars(select(Job).where(Job.job_type == KF_SESSION_END_JOB_TYPE))).all()
    assert len(jobs) == 1
    await service.end_native_session(conversation.id)
    await service.end_native_session(conversation.id)
    assert kf.state == 4
    assert kf.event_texts[1:] == [("code-end", ENDED_TEXT)]
    await session.close()
    await engine.dispose()


async def test_accept_refused_or_taken_does_not_switch_session(tmp_path):
    """非值班员工的点击不做任何事；平台拒绝或已被别人接入时只更新卡片说明。"""
    engine, session, conversation = await _setup(tmp_path)
    operations = SQLAlchemyOperationsRepository(session)

    rejecting = KfStub(reject=95000)
    service = HumanSessionService(
        session, rejecting, agent_id=1000002, duty_userids=("duty-1",), card_for=_card
    )
    await service.handle_card_action(_click(f"accept:{conversation.id}", userid="outsider"))
    assert rejecting.cards == []
    await service.handle_card_action(_click(f"accept:{conversation.id}"))
    assert rejecting.cards[-1]["template_card"]["replace_text"] == "接入失败"
    assert not await operations.native_session_active(conversation.id)

    taken = KfStub(state=3, servicer="duty-2")
    service = HumanSessionService(
        session, taken, agent_id=1000002, duty_userids=("duty-1",), card_for=_card
    )
    await service.handle_card_action(_click(f"accept:{conversation.id}"))
    assert taken.cards[-1]["template_card"]["replace_text"] == "已有管家接入"
    assert taken.event_texts == []
    await session.close()
    await engine.dispose()


async def test_servicer_end_releases_and_skips_when_system_already_released(tmp_path):
    """管家点「结束聊天」：交还机器人并凭事件 msg_code 发结束语；本系统先结束的不重复发。"""
    engine, session, conversation = await _setup(tmp_path)
    kf = KfStub(state=3, servicer="duty-1")
    await SQLAlchemyOperationsRepository(session).accept_handoff(
        conversation.id, servicer_userid="duty-1", now=datetime.now(UTC)
    )
    service = HumanSessionService(session, kf, agent_id=1000002, duty_userids=("duty-1",))

    await service.on_servicer_ended("wk", "wm", "code-native")
    await service.on_servicer_ended("wk", "wm", "code-native-again")

    assert conversation.mode is ConversationMode.BOT_ACTIVE
    assert kf.event_texts == [("code-native", ENDED_TEXT)]
    release = await session.scalar(
        select(AuditLog).where(AuditLog.action == "conversation_release")
    )
    assert release.details["reason"] == "servicer_end"
    await session.close()
    await engine.dispose()
