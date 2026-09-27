"""人工交还的风险、时间、事务复核和审计回归；不访问外部服务。"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import ConversationMode, EmployeeRole, MessageOrigin
from homestay_bot.domain.models import AuditLog, Base, Conversation, Message
from homestay_bot.repositories.operations import SQLAlchemyOperationsRepository
from homestay_bot.services.task_lifecycle_service import TaskLifecycleService


async def test_idle_release_rechecks_risk_activity_and_keeps_handoff_audit(tmp_path):
    """批量先排高风险；新员工活动或接管阻止交还；手动可处理高风险。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'release.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with factory() as session:
        repo = SQLAlchemyOperationsRepository(session)
        conversations = []
        for index, reason in enumerate([
            "emergency:gas", "complaint:noise", "refund", "complaint", "agitated",
            "manual_request_or_media", "price", "servicer_reply", "assistant_unavailable",
        ]):
            conversation = Conversation(open_kfid="test", external_userid=str(index),
                                        mode=ConversationMode.HUMAN_ACTIVE)
            session.add(conversation)
            await session.flush()
            await repo.record_handoff(conversation_id=conversation.id, customer_id=None,
                                      reason=reason)
            audit = await session.scalar(select(AuditLog).where(
                AuditLog.target_id == str(conversation.id)))
            audit.created_at = now - timedelta(minutes=31)
            conversations.append(conversation)
        await session.flush()
        ids = await repo.list_idle_human_conversations(now=now, limit=1)
        assert ids == (conversations[5].id,)
        # 候选扫描后到锁内复核前，新员工发言必须阻止交还。
        session.add(Message(conversation_id=ids[0], external_message_id="staff",
                            origin=MessageOrigin.SERVICER, message_type="text",
                            sent_at=now - timedelta(minutes=4)))
        await session.flush()
        assert not await repo.release_conversation(ids[0], now=now, automatic=True)
        await repo.record_handoff(conversation_id=conversations[6].id, customer_id=None,
                                  reason="refund")
        assert not await repo.release_conversation(conversations[6].id, now=now, automatic=True)
        await session.commit()
        # 通知登记失败必须让同事务交还回滚，不能悄悄恢复机器人而漏通知。
        with pytest.raises(RuntimeError, match="outbox failed"):
            await TaskLifecycleService(
                repo, release_repository=repo,
                release_notifier=AsyncMock(side_effect=RuntimeError("outbox failed")),
            ).release_idle_conversations(now=now)
        await session.rollback()
        conversations = list((await session.scalars(
            select(Conversation).order_by(Conversation.id)
        )).all())
        assert conversations[7].mode == ConversationMode.HUMAN_ACTIVE
        assert await session.scalar(select(AuditLog.id).where(
            AuditLog.action == "conversation_release")) is None
        # 同一事务入队通知，只为成功交还发一次；不会消费旧接管审计。
        notify = AsyncMock()
        released = await TaskLifecycleService(
            repo, release_repository=repo, release_notifier=notify
        ).release_idle_conversations(now=now)
        assert released == 2
        assert notify.await_count == 2
        assert not await repo.release_conversation(conversations[7].id, now=now, automatic=True)
        assert not await repo.release_conversation(conversations[0].id, now=now, customer_id=999)
        assert await repo.release_conversation(conversations[0].id, now=now,
                                               actor_employee_id=42)
        audit = await session.scalar(select(AuditLog).where(
            AuditLog.action == "conversation_release",
            AuditLog.target_id == str(conversations[0].id)))
        assert audit.actor_employee_id == 42
        assert audit.details["reason"] == "employee"
        auto = await session.scalar(select(AuditLog).where(
            AuditLog.action == "conversation_release",
            AuditLog.target_id == str(conversations[7].id)))
        assert auto.details["reason"] == "auto_idle_5m"
        assert audit.details["handoff_id"] == 1
        assert conversations[0].mode == ConversationMode.BOT_ACTIVE
    await engine.dispose()


def test_release_route_requires_admin_customer_csrf_and_rejects_replay():
    """沿用 CRM 权限及客户绑定的一次性 CSRF，不允许跨客户和重复提交。"""
    from test_customer_routes import build_client, detail_csrf, login

    client, service = build_client(EmployeeRole.ADMIN)
    service.release_conversation = AsyncMock()
    login(client)
    token = detail_csrf(client)
    path = "/employee/customers/7/conversations/1/release"
    assert client.post(path, data={"csrf_token": "forged"}).status_code == 409
    assert client.post(path.replace("/7/", "/8/"), data={"csrf_token": token}).status_code == 409
    response = client.post(path, data={"csrf_token": token}, follow_redirects=False)
    assert response.status_code == 303
    assert service.release_conversation.await_count == 1
    assert client.post(path, data={"csrf_token": token}).status_code == 409
    other, _ = build_client(EmployeeRole.STAFF)
    login(other)
    assert other.post(path, data={"csrf_token": token}).status_code == 403


async def test_idle_release_threshold_is_five_minutes(tmp_path):
    """用户 2026-09-28 决定：低风险接管空闲满 5 分钟交还；不到 5 分钟不交还。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'threshold.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with factory() as session:
        repo = SQLAlchemyOperationsRepository(session)
        ids = []
        for index, minutes in enumerate((4, 6)):
            conversation = Conversation(open_kfid="test", external_userid=f"t{index}",
                                        mode=ConversationMode.HUMAN_ACTIVE)
            session.add(conversation)
            await session.flush()
            await repo.record_handoff(conversation_id=conversation.id, customer_id=None,
                                      reason="price")
            audit = await session.scalar(select(AuditLog).where(
                AuditLog.target_id == str(conversation.id)))
            audit.created_at = now - timedelta(minutes=minutes)
            ids.append(conversation.id)
        await session.flush()
        assert await repo.list_idle_human_conversations(now=now, limit=10) == (ids[1],)
    await engine.dispose()
