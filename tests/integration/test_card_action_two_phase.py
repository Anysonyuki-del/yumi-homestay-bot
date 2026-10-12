"""卡片按钮两阶段（修复 Spec 2026-10-08 fix2 §2.6(2)，V7-R3、V8-R3、V9-R2）。

真实 `HumanSessionService` 与真实 `_handoff_card_for`，平台为内存替身：动作前取快照，取数的
数据库错误在调用平台接入之前抛出；动作后只用快照组卡，组卡或更新失败不影响已完成的人工动作。
"""

from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.application import _handoff_card_for, _notify_conversation_release
from homestay_bot.domain.enums import ConversationMode, KnowledgeReferralStatus
from homestay_bot.domain.models import AuditLog, Base, Conversation, Customer, KnowledgeReferral
from homestay_bot.repositories.customers import SQLAlchemyCustomerRepository
from homestay_bot.repositories.operations import SQLAlchemyOperationsRepository
from homestay_bot.services.conversation_service import resolve_notification_identity
from homestay_bot.services.handoff_card import closed_referral_card
from homestay_bot.services.human_session import HumanSessionService


class _Kf:
    """平台替身：记录接入、状态与卡片更新；可让卡片更新失败。"""

    def __init__(self, *, fail_update: bool = False) -> None:
        self.transfers: list[str] = []
        self.cards: list[dict[str, Any]] = []
        self.fail_update = fail_update
        self.state = 1

    async def get_service_state(self, open_kfid: str, external_userid: str):
        return self.state, ""

    async def transfer_service_state(self, open_kfid, external_userid, userid) -> str:
        self.transfers.append(userid)
        self.state = 3
        return "code-accept"

    async def end_service_state(self, open_kfid, external_userid) -> str:
        self.state = 4
        return "code-end"

    async def send_event_text(self, code: str, content: str) -> str:
        return "evt-1"

    async def update_template_card(self, **kwargs: Any) -> None:
        if self.fail_update:
            raise RuntimeError("card update down")
        self.cards.append(kwargs["template_card"])


@pytest.fixture
async def setup(tmp_path):
    """带客户（有 CRM 备注读取）的人工会话与一条资料核实转交。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cards.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    customer = Customer(display_name="合成客人")
    session.add(customer)
    await session.flush()
    conversation = Conversation(
        open_kfid="wk", external_userid="wm", customer_id=customer.id,
        mode=ConversationMode.HUMAN_ACTIVE,
    )
    session.add(conversation)
    await session.flush()
    await SQLAlchemyOperationsRepository(session).record_handoff(
        conversation_id=conversation.id, customer_id=customer.id, reason="price"
    )
    session.add(KnowledgeReferral(
        conversation_id=conversation.id, source_message_id="g-1", unit_id="1:0-7",
        plan_item_id=1, question="健身房几点开门", normalized_question="健身房开放时间",
    ))
    await session.commit()
    yield session, conversation
    await session.close()
    await engine.dispose()


def _service(session, kf) -> HumanSessionService:
    """与生产装配相同的 card_for 与固定关闭卡片。"""
    return HumanSessionService(
        session, kf, agent_id=1000002, duty_userids=("duty-1",),
        card_for=lambda conversation, task_id: _handoff_card_for(
            session, conversation, task_id, identity_resolver=None,
            public_base_url="https://yumi.test",
        ),
        closed_card_for=lambda conversation: closed_referral_card(
            conversation_id=conversation.id, link="https://yumi.test/c"
        ),
    )


def _click(action: str, conversation_id: int, task_id: str) -> dict[str, str]:
    return {
        "userid": "duty-1", "key": f"{action}:{conversation_id}", "task_id": task_id,
        "response_code": "rc", "agent_id": "1000002",
    }


async def _accept_audits(session, conversation_id: int) -> int:
    """接入审计条数。"""
    return await session.scalar(
        select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "conversation_handoff_accepted",
            AuditLog.target_id == str(conversation_id),
        )
    ) or 0


def _break_crm_note(monkeypatch, error: Exception) -> None:
    """在 CRM 备注的数据库读取处注入故障（不是把整个 card_for 换成抛错替身）。"""

    async def broken(self, customer_id: int):
        raise error

    monkeypatch.setattr(
        SQLAlchemyCustomerRepository, "get_customer_notification_note", broken
    )


@pytest.mark.parametrize("task_id", ["handoff-1", "referral-1"])
@pytest.mark.asyncio
async def test_snapshot_database_error_happens_before_platform_accept(
    setup, monkeypatch, task_id
) -> None:
    """普通卡片：嵌套取数的数据库错误真正传播，平台接入调用为 0。资料核实卡片不读 CRM。"""
    session, conversation = setup
    kf = _Kf()
    _break_crm_note(monkeypatch, OperationalError("select", {}, Exception("db down")))
    if task_id == "handoff-1":
        with pytest.raises(OperationalError):
            await _service(session, kf).handle_card_action(
                _click("accept", conversation.id, task_id)
            )
        assert kf.transfers == []
    else:
        await _service(session, kf).handle_card_action(_click("accept", conversation.id, task_id))
        assert kf.transfers == ["duty-1"]


@pytest.mark.asyncio
async def test_non_database_name_failure_still_falls_back(setup, monkeypatch) -> None:
    """非数据库的备注故障仍安全兜底：接入照常，卡片显示客人名称。"""
    session, conversation = setup
    kf = _Kf()
    _break_crm_note(monkeypatch, RuntimeError("crm parse"))
    await _service(session, kf).handle_card_action(_click("accept", conversation.id, "handoff-1"))
    assert kf.transfers == ["duty-1"]
    assert kf.cards and kf.cards[-1]["main_title"]["desc"] == "客人：客人"


@pytest.mark.asyncio
async def test_database_error_propagates_from_all_identity_callers(setup, monkeypatch) -> None:
    """共用取名函数的三处生产调用方都让数据库错误向上传播。"""
    session, conversation = setup
    _break_crm_note(monkeypatch, OperationalError("select", {}, Exception("db down")))
    with pytest.raises(OperationalError):
        await resolve_notification_identity(
            conversation, identity_resolver=None,
            customer_notification=SQLAlchemyCustomerRepository(session),
        )
    with pytest.raises(OperationalError):
        await _handoff_card_for(
            session, conversation, None, identity_resolver=None, public_base_url="https://y"
        )
    from datetime import UTC, datetime

    with pytest.raises(OperationalError):
        await _notify_conversation_release(
            session, registry=_Registry(), public_base_url="https://y",
            conversation_id=conversation.id, now=datetime.now(UTC),
        )


class _Registry:
    """自动交还通知读取的运行时客户端（不调用外部接口的替身）。"""

    def acquire(self):
        from contextlib import asynccontextmanager
        from types import SimpleNamespace

        @asynccontextmanager
        async def bundle():
            yield SimpleNamespace(wecom=None, agent_id=1000002, duty_userids=("duty-1",))

        return bundle()


@pytest.mark.parametrize("task_id", ["handoff-1", "referral-1"])
@pytest.mark.asyncio
async def test_card_update_failure_keeps_completed_action(setup, task_id) -> None:
    """组卡或更新失败只记录错误：接入与本地审计各一次，不抛出、不重做。"""
    session, conversation = setup
    kf = _Kf(fail_update=True)
    await _service(session, kf).handle_card_action(_click("accept", conversation.id, task_id))
    assert kf.transfers == ["duty-1"]
    assert await SQLAlchemyOperationsRepository(session).native_session_active(conversation.id)


@pytest.mark.parametrize(
    "mutate",
    ["cleared", "missing", "other_conversation"],
)
@pytest.mark.asyncio
async def test_invalid_referral_blocks_accept_but_not_release(setup, mutate) -> None:
    """转交清空、缺失或会话不符：不执行接入，只显示固定关闭卡片；交还照常执行。"""
    session, conversation = setup
    referral = await session.get(KnowledgeReferral, 1)
    task_id = "referral-1"
    if mutate == "cleared":
        referral.status = KnowledgeReferralStatus.CLEARED
        referral.question = None
    elif mutate == "missing":
        task_id = "referral-404"
    else:
        other = Conversation(open_kfid="wk", external_userid="other")
        session.add(other)
        await session.flush()
        referral.conversation_id = other.id
    await session.commit()
    kf = _Kf()
    service = _service(session, kf)

    await service.handle_card_action(_click("accept", conversation.id, task_id))
    assert kf.transfers == []
    [closed] = kf.cards
    assert closed["replace_text"] == "已失效" and "健身房" not in str(closed)

    await service.handle_card_action(_click("release", conversation.id, task_id))
    assert conversation.mode is ConversationMode.BOT_ACTIVE
    assert kf.cards[-1]["replace_text"] == "已交还 AI 助手"


@pytest.mark.parametrize(
    "status", [KnowledgeReferralStatus.CONVERTED, KnowledgeReferralStatus.CLOSED]
)
@pytest.mark.asyncio
async def test_release_works_after_referral_is_converted_or_closed(setup, status) -> None:
    """入库或关闭后仍能交还，卡片显示固定内容、不含旧原问。"""
    session, conversation = setup
    (await session.get(KnowledgeReferral, 1)).status = status
    await session.commit()
    kf = _Kf()
    await _service(session, kf).handle_card_action(
        _click("release", conversation.id, "referral-1")
    )
    assert conversation.mode is ConversationMode.BOT_ACTIVE
    assert "健身房" not in str(kf.cards[-1])


@pytest.mark.asyncio
async def test_ordinary_card_content_unchanged_by_prefetch(setup) -> None:
    """对照：普通卡片接入后的卡片与原先一致（原因、客人行、按钮）。"""
    session, conversation = setup
    kf = _Kf()
    await _service(session, kf).handle_card_action(_click("accept", conversation.id, "handoff-1"))
    card = kf.cards[-1]
    assert card["main_title"]["title"] == "需要人工跟进：价格协商"
    assert card["button_list"][0]["key"] == f"release:{conversation.id}"
    assert card["task_id"] == "handoff-1"


@pytest.mark.asyncio
async def test_employee_notification_propagates_crm_database_error(setup, monkeypatch) -> None:
    """第三处调用方（会话服务的员工通知）同样让 CRM 备注的数据库错误向上传播。"""
    from datetime import UTC, datetime

    from homestay_bot.domain.enums import MessageOrigin
    from homestay_bot.repositories.conversations import (
        SQLAlchemyConversationRepository,
        SQLAlchemyMessageRepository,
    )
    from homestay_bot.services.conversation_service import ConversationService
    from homestay_bot.services.emergency_service import EmergencyService
    from homestay_bot.services.message_service import IncomingMessage, MessageService

    session, conversation = setup
    _break_crm_note(monkeypatch, OperationalError("select", {}, Exception("db down")))
    service = ConversationService(
        conversations=SQLAlchemyConversationRepository(session),
        messages=MessageService(SQLAlchemyMessageRepository(session)),
        assistant=None,  # type: ignore[arg-type]
        emergency_service=EmergencyService(),
        wecom=None,  # type: ignore[arg-type]
        agent_id=1000002,
        duty_employee_userids=["duty-1"],
        customer_notification=SQLAlchemyCustomerRepository(session),
    )
    message = IncomingMessage(
        msgid="g-n", open_kfid="wk", external_userid="wm", origin=MessageOrigin.GUEST,
        msgtype="text", content="转人工", sent_at=datetime.now(UTC),
    )
    with pytest.raises(OperationalError):
        await service._notify_employee(conversation, message, "普通人工接管")
