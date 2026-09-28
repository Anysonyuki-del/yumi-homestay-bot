"""合成订单驱动真实会话、仓储和最终正文，禁止真实外部调用。"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import Language, MessageOrigin
from homestay_bot.domain.models import Base, Conversation, Customer, PropertyProfile, StayOrder
from homestay_bot.integrations.deepseek_client import AssistantDecision
from homestay_bot.repositories.context import SQLAlchemyContextRepository
from homestay_bot.repositories.conversations import (
    SQLAlchemyConversationRepository,
    SQLAlchemyMessageRepository,
)
from homestay_bot.repositories.operations import SQLAlchemyOperationsRepository
from homestay_bot.services.conversation_service import ConversationService
from homestay_bot.services.emergency_service import EmergencyService
from homestay_bot.services.message_service import IncomingMessage, MessageService
from tests.unit.test_conversation_service import AssistantStub, WeComStub


async def _synthetic_stay_service(session, orders_count: int):
    """合成客人、房间、订单与真实仓储装配的会话服务；只用虚构数据。"""
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    session.add_all([
        Customer(id=1, display_name="合成客人"),
        PropertyProfile(id=1, title="合成201房", address_hint="合成小区2栋2层"),
        PropertyProfile(id=2, title="合成302房"),
    ])
    await session.flush()
    conversation = Conversation(
        customer_id=1, open_kfid="test-kf", external_userid="test-guest", language=Language.ZH
    )
    orders = [
        StayOrder(
            customer_id=1,
            property_id=index + 1,
            hostex_reservation_code=f"synthetic-{index}",
            stay_code=f"synthetic-{index}",
            check_in_date=today + timedelta(days=index * 5),
            check_out_date=today + timedelta(days=index * 5 + 2),
            status="confirmed",
        )
        for index in range(orders_count)
    ]
    session.add_all([conversation, *orders])
    await session.flush()
    assistant = AssistantStub(
        decision=AssistantDecision(
            reply_text="入住时间请以已确认资料为准。",
            language=Language.ZH,
            intent="checkin",
            confidence=1,
        )
    )
    wecom = WeComStub()
    service = ConversationService(
        conversations=SQLAlchemyConversationRepository(session),
        messages=MessageService(SQLAlchemyMessageRepository(session)),
        customer_context=SQLAlchemyContextRepository(session),
        assistant=assistant,
        emergency_service=EmergencyService(),
        wecom=wecom,
        agent_id=1,
        duty_employee_userids=["test-staff"],
        audit_events=SQLAlchemyOperationsRepository(session),
        savepoint_factory=session.begin_nested,
    )

    async def receive(number: int, text: str) -> None:
        """经真实入站去重与最终出站执行一轮。"""
        await service.handle_message(
            IncomingMessage(
                msgid=f"synthetic-{number}",
                open_kfid="test-kf",
                external_userid="test-guest",
                origin=MessageOrigin.GUEST,
                msgtype="text",
                content=text,
                sent_at=datetime.now(UTC) + timedelta(seconds=number),
            )
        )
        await session.commit()

    return conversation, orders, assistant, wecom, receive


@pytest.mark.asyncio
async def test_single_current_order_is_used_without_confirmation_and_welcomed_once() -> None:
    """名下唯一当前订单直接识别房间，不再请客人确认；欢迎消息同一订单只发一次（Spec F1/D2）。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        conversation, orders, assistant, wecom, receive = await _synthetic_stay_service(session, 1)
        await receive(1, "地址在哪")
        context = assistant.last_kwargs["customer_context"]
        assert context.confirmed_stay is None
        assert context.resolved_stay["property_id"] == 1
        assert context.resolved_stay["order_id"] == orders[0].id
        assert not (conversation.stay_confirmation or {}).get("status")
        first = wecom.guest_messages[-1]
        assert first.startswith("欢迎入住合成201房！")
        assert "地址与楼层：合成小区2栋2层" in first
        assert "请确认" not in first
        await receive(2, "几点退房")
        assert "欢迎入住" not in wecom.guest_messages[-1]
    await engine.dispose()


@pytest.mark.asyncio
async def test_guest_confirmation_and_reschedule_reach_final_conversation() -> None:
    """多张当前订单时先选择、再确认；次轮前发生改期也必须重新确认。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        conversation, orders, assistant, wecom, receive = await _synthetic_stay_service(session, 2)
        order = orders[0]
        await receive(1, "办理入住")
        assert "请先选择" in wecom.guest_messages[-1]
        assert assistant.last_kwargs["customer_context"].resolved_stay is None
        assistant.decision = AssistantDecision(
            reply_text="好的。", language=Language.ZH, intent="checkin", confidence=1,
            stay_confirmation_intent="select", stay_order_id=order.id,
        )
        await receive(2, "第一个")
        assert conversation.stay_confirmation["status"] == "pending"
        assert "合成201房" in wecom.guest_messages[-1]
        assert "请确认" in wecom.guest_messages[-1]
        assistant.decision = AssistantDecision(
            reply_text="收到。",
            language=Language.ZH,
            intent="confirm",
            confidence=1,
            stay_confirmation_intent="confirm",
        )
        await receive(3, "对，日期房间都正确")
        assert conversation.stay_confirmation["status"] == "confirmed"
        assert "已确认" in wecom.guest_messages[-1]
        # 刚确认的住宿本轮就发欢迎消息。
        assert "欢迎入住合成201房！" in wecom.guest_messages[-1]
        order.check_out_date += timedelta(days=1)
        await session.commit()
        assistant.decision = AssistantDecision(
            reply_text="请核对本次住宿。", language=Language.ZH, intent="checkin", confidence=1
        )
        await receive(4, "我的房间几点退房")
        assert assistant.last_kwargs["customer_context"].confirmed_stay is None
        # 改期让已确认住宿失效；名下仍有多张订单，重新请客人选择，列出改期后的日期。
        assert assistant.last_kwargs["customer_context"].resolved_stay is None
        assert not (conversation.stay_confirmation or {}).get("status")
        assert "请先选择" in wecom.guest_messages[-1]
        assert order.check_out_date.isoformat() in wecom.guest_messages[-1]
    await engine.dispose()
