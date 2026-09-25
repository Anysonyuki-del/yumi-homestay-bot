"""紧急情况后续的固定处置答复：按生产方式装配，覆盖接管审计读取与「紧急处置」知识。

用 SQLite 文件库；出站只登记到事务 outbox，不访问企业微信。
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.application import TransactionalOutboxWeCom
from homestay_bot.domain.enums import ConversationMode, MessageOrigin
from homestay_bot.domain.models import Base, Conversation, Job, KnowledgeEntry
from homestay_bot.repositories.conversations import (
    SQLAlchemyConversationRepository,
    SQLAlchemyMessageRepository,
)
from homestay_bot.repositories.knowledge import SQLAlchemyKnowledgeRepository
from homestay_bot.repositories.operations import SQLAlchemyOperationsRepository
from homestay_bot.services.conversation_service import ConversationService
from homestay_bot.services.emergency_service import EmergencyService
from homestay_bot.services.message_service import IncomingMessage, MessageService


class _NoModel:
    """紧急后续不得调用模型：被调用即让测试失败。"""

    async def respond(self, **_kwargs):
        """意外调用。"""
        raise AssertionError("紧急后续不应调用模型")


async def _factory(tmp_path):
    """建库并写入一条启用的「紧急处置」审核知识（虚构）。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'emergency.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            KnowledgeEntry(
                category="紧急处置",
                question_zh="闻到燃气味怎么办？",
                answer_zh="燃气总阀在一楼厨房门后，请立即开窗通风并到院子等候。",
                question_en="What if I smell gas?",
                answer_en="Open the windows and wait in the courtyard.",
                keywords=["gas", "燃气"],
                is_enabled=True,
            )
        )
        await session.commit()
    return factory


async def _handle(factory, msgid: str, content: str) -> None:
    """按生产装配处理一条客人消息，业务与出站登记同一事务提交。"""
    async with factory() as session:
        service = ConversationService(
            conversations=SQLAlchemyConversationRepository(session),
            messages=MessageService(SQLAlchemyMessageRepository(session)),
            assistant=_NoModel(),
            emergency_service=EmergencyService(),
            wecom=TransactionalOutboxWeCom(
                session, source_message_id=msgid, source_guest_message_id=msgid
            ),
            agent_id=1,
            duty_employee_userids=["duty-1"],
            audit_events=SQLAlchemyOperationsRepository(session),
            emergency_knowledge=SQLAlchemyKnowledgeRepository(session),
        )
        await service.handle_message(
            IncomingMessage(
                msgid=msgid,
                open_kfid="wk-em",
                external_userid="wm-em",
                origin=MessageOrigin.GUEST,
                msgtype="text",
                content=content,
                sent_at=datetime.now(UTC),
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_follow_up_uses_reviewed_emergency_knowledge_in_production_wiring(tmp_path) -> None:
    """燃气紧急后问「我们现在该怎么办」：取审核知识原文作答、带过时豁免、再次通知员工。"""
    factory = await _factory(tmp_path)

    await _handle(factory, "gas-1", "房间里燃气味好重")
    await _handle(factory, "gas-2", "我们现在该怎么办")

    async with factory() as session:
        conversation = await session.scalar(select(Conversation))
        jobs = list(await session.scalars(select(Job).order_by(Job.id)))
    assert conversation is not None and conversation.mode is ConversationMode.HUMAN_ACTIVE
    guest = [job.payload for job in jobs if job.job_type == "wecom_send_text"]
    internal = [job.payload for job in jobs if job.job_type == "wecom_send_internal_text"]
    assert "燃气总阀在一楼厨房门后" in guest[-1]["content"]
    assert guest[-1]["stale_exempt"] is True
    assert len(internal) == 2
    assert "紧急情况后续" in internal[-1]["content"]
