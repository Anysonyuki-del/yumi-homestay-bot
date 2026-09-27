"""复用已受限的本机测试库，验证请求与 outbox 事务及并发幂等。"""

import asyncio

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from homestay_bot.application import TransactionalOutboxWeCom
from homestay_bot.domain.enums import BusinessTaskType
from homestay_bot.domain.models import BusinessTask, Customer, Job
from homestay_bot.repositories.operations import SQLAlchemyOperationsRepository
from tests.integration.test_retention_postgresql import pg_engine  # noqa: F401


@pytest.mark.asyncio
async def test_guest_request_and_reply_rollback_together(pg_engine) -> None:  # noqa: F811
    """事务未提交时，任务与两类通知均不可遗留成功记录。"""
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with factory() as session:
        customer = Customer(display_name="合成事务客人")
        session.add(customer)
        await session.flush()
        await SQLAlchemyOperationsRepository(session).create_pending_confirmation(
            customer_id=customer.id,
            source_message_id="synthetic-rollback",
            task_type=BusinessTaskType.SPECIAL_SERVICE,
            description="合成请求",
        )
        sender = TransactionalOutboxWeCom(session, source_message_id="synthetic-rollback")
        await sender.send_text("synthetic-kf", "synthetic-guest", "已登记，尚待确认。")
        await session.rollback()
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(BusinessTask)) == 0
        assert await session.scalar(select(func.count()).select_from(Job)) == 0


@pytest.mark.asyncio
async def test_concurrent_guest_request_reuses_source_key(pg_engine) -> None:  # noqa: F811
    """两个事务处理同一来源时等待唯一键结果，不创建重复任务。"""
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with factory() as session:
        owner = Customer(display_name="合成幂等客人")
        session.add(owner)
        await session.commit()
        owner_id = owner.id
    async with factory() as first, factory() as second:
        fields = dict(
            customer_id=owner_id,
            source_message_id="synthetic-race",
            task_type=BusinessTaskType.SPECIAL_SERVICE,
            description="合成请求",
        )
        task = await SQLAlchemyOperationsRepository(first).create_pending_confirmation(**fields)
        other = asyncio.create_task(
            SQLAlchemyOperationsRepository(second).create_pending_confirmation(**fields)
        )
        try:
            await first.commit()
            repeated = await asyncio.wait_for(other, timeout=6)
            await second.commit()
            assert repeated.id == task.id
        finally:
            if not other.done():
                other.cancel()
                await asyncio.gather(other, return_exceptions=True)
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(BusinessTask)) == 1


@pytest.mark.asyncio
async def test_release_and_staff_message_serialize_on_conversation(pg_engine):  # noqa: F811
    """员工等交还锁后必须刷新旧 ORM 模式，再接管；不能被自动交还吞掉。"""
    from datetime import UTC, datetime, timedelta

    from homestay_bot.domain.enums import ConversationMode, MessageOrigin
    from homestay_bot.domain.models import AuditLog, Conversation, Message
    from homestay_bot.repositories.conversations import SQLAlchemyConversationRepository

    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with factory() as session:
        conversation = Conversation(
            open_kfid="synthetic", external_userid="synthetic", mode=ConversationMode.HUMAN_ACTIVE
        )
        session.add(conversation)
        await session.flush()
        key = conversation.id
        session.add(
            AuditLog(
                action="conversation_handoff",
                target_type="conversation",
                target_id=str(key),
                details={"reason": "manual_request_or_media"},
                created_at=now - timedelta(hours=1),
            )
        )
        await session.commit()
    async with factory() as release, factory() as staff:
        old = await staff.get(Conversation, key)
        assert old.mode is ConversationMode.HUMAN_ACTIVE
        assert await SQLAlchemyOperationsRepository(release).release_conversation(
            key,
            now=now,
            automatic=True,
        )
        lock = asyncio.create_task(SQLAlchemyConversationRepository(staff).lock_activity(key))
        await asyncio.sleep(0.05)
        assert not lock.done()
        await release.commit()
        await asyncio.wait_for(lock, 5)
        assert old.mode is ConversationMode.BOT_ACTIVE
        old.mode = ConversationMode.HUMAN_ACTIVE
        staff.add(
            Message(
                conversation_id=key,
                external_message_id="synthetic-staff",
                origin=MessageOrigin.SERVICER,
                message_type="text",
                content="合成员工接待",
                sent_at=now,
            )
        )
        await SQLAlchemyOperationsRepository(staff).record_handoff(
            conversation_id=key,
            customer_id=None,
            reason="servicer_reply",
        )
        await staff.commit()
    async with factory() as session:
        assert not await SQLAlchemyOperationsRepository(session).release_conversation(
            key,
            now=now,
            automatic=True,
        )
