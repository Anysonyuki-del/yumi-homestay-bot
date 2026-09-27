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
from homestay_bot.services.conversation_service import ConversationService
from homestay_bot.services.emergency_service import EmergencyService
from homestay_bot.services.message_service import IncomingMessage, MessageService
from tests.unit.test_conversation_service import AssistantStub, WeComStub


@pytest.mark.asyncio
async def test_guest_confirmation_and_reschedule_reach_final_conversation() -> None:
    """先展示本人订单再接受确认；次轮前发生改期也必须重新确认。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add_all(
            [Customer(id=1, display_name="合成客人"), PropertyProfile(id=1, title="合成201房")]
        )
        await session.flush()
        conversation = Conversation(
            customer_id=1, open_kfid="test-kf", external_userid="test-guest", language=Language.ZH
        )
        order = StayOrder(
            customer_id=1,
            property_id=1,
            hostex_reservation_code="synthetic-only",
            stay_code="synthetic-only",
            check_in_date=today,
            check_out_date=today + timedelta(days=2),
            status="confirmed",
        )
        session.add_all([conversation, order])
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

        await receive(1, "办理入住")
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
        await receive(2, "对，日期房间都正确")
        assert conversation.stay_confirmation["status"] == "confirmed"
        assert "已确认" in wecom.guest_messages[-1]
        order.check_out_date += timedelta(days=1)
        await session.commit()
        assistant.decision = AssistantDecision(
            reply_text="请核对本次住宿。", language=Language.ZH, intent="checkin", confidence=1
        )
        await receive(3, "我的房间几点退房")
        assert assistant.last_kwargs["customer_context"].confirmed_stay is None
        assert conversation.stay_confirmation["status"] == "pending"
        assert order.check_out_date.isoformat() in wecom.guest_messages[-1]
    await engine.dispose()
