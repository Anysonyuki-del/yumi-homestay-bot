"""复用已受限的本机测试库，验证请求与 outbox 事务及并发幂等。"""

import asyncio
from dataclasses import replace

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


@pytest.mark.asyncio
async def test_soft_judgment_planning_holds_no_lock_and_drops_stale_results(
    pg_engine,  # noqa: F811
) -> None:
    """合并作业里的软判定规划：等待期间不持活动锁，新客人活动可推进；结果过时则不建客诉、
    不排最终任务（回复泛用化 Spec §2.3 V4-R2、V6-R2，§2.7）。"""
    from datetime import UTC, datetime

    from homestay_bot.domain.enums import ConversationMode, Language, MessageOrigin
    from homestay_bot.domain.models import ComplaintReview, Conversation
    from homestay_bot.repositories.complaints import SQLAlchemyComplaintRepository
    from homestay_bot.repositories.conversations import (
        SQLAlchemyConversationRepository,
        SQLAlchemyMessageRepository,
    )
    from homestay_bot.repositories.jobs import SQLAlchemyJobRepository
    from homestay_bot.services.complaint_service import ComplaintService
    from homestay_bot.services.conversation_service import ConversationService
    from homestay_bot.services.emergency_service import EmergencyService
    from homestay_bot.services.message_service import IncomingMessage, MessageService
    from homestay_bot.services.turn_plan import failed_plan

    factory = async_sessionmaker(pg_engine, expire_on_commit=False)

    def guest(msgid: str, content: str) -> IncomingMessage:
        """合成客人消息。"""
        return IncomingMessage(
            msgid=msgid, open_kfid="synthetic-kf", external_userid="synthetic-guest",
            origin=MessageOrigin.GUEST, msgtype="text", content=content,
            sent_at=datetime.now(UTC),
        )

    source = guest("synthetic-soft-1", "第一次来太开心了!!!")
    async with factory() as session:
        conversations = SQLAlchemyConversationRepository(session)
        conversation = await conversations.get_or_create(source)
        await MessageService(SQLAlchemyMessageRepository(session)).record_incoming(
            conversation.id, source
        )
        await session.commit()
        conversation_id = conversation.id

    planning = asyncio.Event()
    release = asyncio.Event()

    class _Planner:
        """规划停在门闩处；主回复不应被调用。"""

        async def plan_turn(self, *, text: str, language: Language):
            planning.set()
            await release.wait()
            return failed_plan(text, "timeout")

        async def respond(self, **kwargs):
            raise AssertionError("过时结果不应进入主回复")

    async with factory() as session:
        service = ConversationService(
            conversations=SQLAlchemyConversationRepository(session),
            messages=MessageService(SQLAlchemyMessageRepository(session)),
            assistant=_Planner(),  # type: ignore[arg-type]
            emergency_service=EmergencyService(),
            wecom=TransactionalOutboxWeCom(session, source_message_id=source.msgid),
            agent_id=1,
            duty_employee_userids=[],
            jobs=SQLAlchemyJobRepository(session),
            complaint_service=ComplaintService(),
            complaint_reviews=SQLAlchemyComplaintRepository(session),
            commit_boundary=session.commit,
        )
        job = asyncio.create_task(service.process_debounced_message(source))
        try:
            await asyncio.wait_for(planning.wait(), 5)
            # 规划等待期间另一事务能立即锁住会话行并写入新客人消息（锁超时 5 秒会报错）。
            async with factory() as other:
                await SQLAlchemyConversationRepository(other).lock_activity(conversation_id)
                await MessageService(SQLAlchemyMessageRepository(other)).record_incoming(
                    conversation_id, guest("synthetic-soft-2", "还有个问题")
                )
                await other.commit()
            release.set()
            await asyncio.wait_for(job, 5)
            await session.commit()
        finally:
            release.set()
            if not job.done():
                job.cancel()
                await asyncio.gather(job, return_exceptions=True)

    async with factory() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation.mode is ConversationMode.BOT_ACTIVE
        assert await session.scalar(select(func.count()).select_from(ComplaintReview)) == 0
        assert await session.scalar(select(func.count()).select_from(Job)) == 0


async def _seed_guest_message(factory, msgid: str, content: str, *, human: bool = False):
    """在隔离库建会话并记录一条客人消息，返回（会话编号, 消息）。"""
    from datetime import UTC, datetime

    from homestay_bot.domain.enums import ConversationMode, MessageOrigin
    from homestay_bot.repositories.conversations import (
        SQLAlchemyConversationRepository,
        SQLAlchemyMessageRepository,
    )
    from homestay_bot.services.message_service import IncomingMessage, MessageService

    message = IncomingMessage(
        msgid=msgid, open_kfid="synthetic-kf", external_userid="synthetic-guest",
        origin=MessageOrigin.GUEST, msgtype="text", content=content, sent_at=datetime.now(UTC),
    )
    async with factory() as session:
        conversation = await SQLAlchemyConversationRepository(session).get_or_create(message)
        if human:
            conversation.mode = ConversationMode.HUMAN_ACTIVE
        await MessageService(SQLAlchemyMessageRepository(session)).record_incoming(
            conversation.id, message
        )
        await session.commit()
        return conversation.id, message


def _service_for(session, assistant, source_msgid: str, *, phase: str):
    """按生产 deferred 装配构造会话服务：真实仓储、作业、出站与提交边界。"""
    from homestay_bot.repositories.complaints import SQLAlchemyComplaintRepository
    from homestay_bot.repositories.conversations import (
        SQLAlchemyConversationRepository,
        SQLAlchemyMessageRepository,
    )
    from homestay_bot.repositories.jobs import SQLAlchemyJobRepository
    from homestay_bot.services.complaint_service import ComplaintService
    from homestay_bot.services.conversation_service import ConversationService
    from homestay_bot.services.emergency_service import EmergencyService
    from homestay_bot.services.message_service import MessageService

    return ConversationService(
        conversations=SQLAlchemyConversationRepository(session),
        messages=MessageService(SQLAlchemyMessageRepository(session)),
        assistant=assistant,
        emergency_service=EmergencyService(),
        wecom=TransactionalOutboxWeCom(
            session, source_message_id=source_msgid, source_guest_message_id=source_msgid,
            delivery_phase=phase,
        ),
        agent_id=1,
        duty_employee_userids=["staff-1"],
        jobs=SQLAlchemyJobRepository(session),
        complaint_service=ComplaintService(),
        complaint_reviews=SQLAlchemyComplaintRepository(session),
        commit_boundary=session.commit,
        savepoint_factory=session.begin_nested,
    )


def _calm_or_complaint_plan(text: str, *, complaint: bool):
    """经生产核验的单项闲聊计划，可选标为投诉。"""
    import json
    from datetime import date

    from homestay_bot.services.turn_plan import verify_turn_plan

    item = {"id": 1, "kind": "chitchat", "quote": text, "start": 0,
            "risk": "complaint" if complaint else "none"}
    return verify_turn_plan(
        json.dumps({"items": [item]}, ensure_ascii=False), text,
        today_provider=lambda: date(2026, 10, 7),
    )


@pytest.mark.asyncio
async def test_main_reply_wait_after_soft_review_holds_no_lock(pg_engine) -> None:  # noqa: F811
    """人工接待中只命中情绪词：后台复核后继续主回复，等主回复期间不持活动锁；期间新客人
    消息推进后，旧结果按过时纪律丢弃，不登记出站（修复 Spec §2.7 / Codex 审查 §4.1）。"""
    from homestay_bot.domain.enums import Language
    from homestay_bot.integrations.deepseek_client import AssistantDecision
    from homestay_bot.repositories.conversations import (
        SQLAlchemyConversationRepository,
        SQLAlchemyMessageRepository,
    )
    from homestay_bot.services.message_service import MessageService

    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    text = "第一次来太开心了!!!"
    conversation_id, source = await _seed_guest_message(
        factory, "synthetic-final-1", text, human=True
    )
    replying = asyncio.Event()
    release = asyncio.Event()

    class _Assistant:
        """规划判为闲聊；主回复停在门闩处。"""

        async def plan_turn(self, *, text: str, language: Language):
            return _calm_or_complaint_plan(text, complaint=False)

        async def respond(self, **kwargs):
            replying.set()
            await release.wait()
            return AssistantDecision(
                reply_text="欢迎入住！", language=Language.ZH, intent="chitchat", confidence=0.9
            )

    async with factory() as session:
        service = _service_for(session, _Assistant(), source.msgid, phase="final")
        job = asyncio.create_task(service.process_recorded_message(source))
        try:
            await asyncio.wait_for(replying.wait(), 5)
            async with factory() as other:
                await SQLAlchemyConversationRepository(other).lock_activity(conversation_id)
                newer = replace(source, msgid="synthetic-final-2", content="还有个问题")
                await MessageService(SQLAlchemyMessageRepository(other)).record_incoming(
                    conversation_id, newer
                )
                await other.commit()
            release.set()
            await asyncio.wait_for(job, 5)
            await session.commit()
        finally:
            release.set()
            if not job.done():
                job.cancel()
                await asyncio.gather(job, return_exceptions=True)

    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Job)) == 0
        assert await session.scalar(select(func.count()).select_from(BusinessTask)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("complaint", [False, True])
async def test_replayed_debounce_job_does_not_duplicate_side_effects(
    pg_engine, complaint: bool  # noqa: F811
) -> None:
    """合并作业规划复核中途提交；作业重放（recover_stale）后，最终任务、客诉复核、客诉
    分析作业与客人/员工出站各只有一份（修复 Spec §2.7 / Codex 审查 §4.1）。"""
    from homestay_bot.domain.enums import Language
    from homestay_bot.domain.models import ComplaintReview

    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    text = "第一次来太开心了!!!"
    _, source = await _seed_guest_message(factory, "synthetic-replay-1", text)

    class _Planner:
        """规划按参数判为闲聊或投诉；本用例不进入主回复。"""

        async def plan_turn(self, *, text: str, language: Language):
            return _calm_or_complaint_plan(text, complaint=complaint)

    async def run_once() -> tuple[int, int]:
        async with factory() as session:
            await _service_for(
                session, _Planner(), source.msgid, phase="ack"
            ).process_debounced_message(source)
            await session.commit()
        async with factory() as session:
            return (
                await session.scalar(select(func.count()).select_from(Job)),
                await session.scalar(select(func.count()).select_from(ComplaintReview)),
            )

    first = await run_once()
    replayed = await run_once()
    assert first == replayed
    jobs, reviews = first
    assert reviews == (1 if complaint else 0)
    assert jobs >= 1
