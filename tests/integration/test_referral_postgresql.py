"""PostgreSQL 补证（修复 Spec 2026-10-08 fix2 §2.4、§2.6 v8–v10 验收）：真实仓储、真实 Worker。

覆盖：暂停主回复期间原生接管；等待期间清空；清空与处理的锁顺序；合并与最终作业中断窗口
（真实 `Worker.run_once` → 取消 → 真实 `recover_stale` → `claim_next`）；草稿失败落点；并发入库。
助手与平台为本地替身，不接真实外部接口；只证明本地持久化与去重，不证明平台受理或客人收件。
"""

import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from homestay_bot.application import (
    TransactionalOutboxWeCom,
    _deferred_message_from_payload,
    _fail_exhausted_referral_draft,
    _run_deferred_source,
)
from homestay_bot.domain.enums import (
    ConversationMode,
    JobStatus,
    KnowledgeReferralStatus,
    Language,
    MessageOrigin,
)
from homestay_bot.domain.models import (
    BusinessTask,
    ComplaintReview,
    Conversation,
    Customer,
    CustomerTag,
    CustomerTagLink,
    Job,
    KnowledgeEntry,
    KnowledgeReferral,
    Message,
)
from homestay_bot.integrations.deepseek_client import (
    AssistantDecision,
    AssistantUnavailableError,
)
from homestay_bot.integrations.deepseek_faq_drafter import FaqDraftUnavailableError
from homestay_bot.repositories.complaints import SQLAlchemyComplaintRepository
from homestay_bot.repositories.conversations import (
    SQLAlchemyConversationRepository,
    SQLAlchemyMessageRepository,
)
from homestay_bot.repositories.customers import TEST_ACCOUNT_TAG, SQLAlchemyCustomerRepository
from homestay_bot.repositories.jobs import SQLAlchemyJobRepository
from homestay_bot.repositories.knowledge_referrals import SQLAlchemyKnowledgeReferralRepository
from homestay_bot.repositories.operations import SQLAlchemyOperationsRepository
from homestay_bot.routes.knowledge import KnowledgeAdminService, KnowledgeFormError
from homestay_bot.services.business_task_service import BusinessTaskService
from homestay_bot.services.complaint_service import ComplaintService
from homestay_bot.services.conversation_service import ConversationService
from homestay_bot.services.emergency_service import EmergencyService
from homestay_bot.services.item_coverage import GapUnit, ReferralCandidate
from homestay_bot.services.knowledge_referral_service import ReferralDraftJobService
from homestay_bot.services.message_service import IncomingMessage, MessageService
from homestay_bot.services.reply_plan import ReplyPart
from homestay_bot.worker import Worker
from tests.integration.test_retention_postgresql import pg_engine  # noqa: F401
from tests.plan_helpers import plan_for

GAP = "健身房几点开门"
CALM_REQUEST = "第一次来太开心了!!!麻烦送两瓶水"
UNCONFIRMED = "这项信息暂时无法确认。"


def _guest(msgid: str, content: str) -> IncomingMessage:
    """合成客人消息。"""
    return IncomingMessage(
        msgid=msgid, open_kfid="synthetic-kf", external_userid="synthetic-guest",
        origin=MessageOrigin.GUEST, msgtype="text", content=content, sent_at=datetime.now(UTC),
    )


async def _seed(factory, msgid: str, content: str, *, human: bool = False) -> tuple[int, Any]:
    """建带「测试专用号」标签的客户、会话，并记录一条客人消息。"""
    message = _guest(msgid, content)
    async with factory() as session:
        customer = Customer(display_name="合成测试号")
        tag = CustomerTag(name=TEST_ACCOUNT_TAG, is_active=True)
        session.add_all([customer, tag])
        await session.flush()
        session.add(CustomerTagLink(customer_id=customer.id, tag_id=tag.id))
        conversation = await SQLAlchemyConversationRepository(session).get_or_create(message)
        conversation.customer_id = customer.id
        if human:
            conversation.mode = ConversationMode.HUMAN_ACTIVE
        await MessageService(SQLAlchemyMessageRepository(session)).record_incoming(
            conversation.id, message
        )
        await session.commit()
        return conversation.id, message


def _service(session, assistant, msgid: str, *, phase: str) -> ConversationService:
    """按生产 deferred 装配：真实仓储、业务任务、审计、作业、转交、出站与提交边界。"""
    operations = SQLAlchemyOperationsRepository(session)
    return ConversationService(
        conversations=SQLAlchemyConversationRepository(session),
        messages=MessageService(SQLAlchemyMessageRepository(session)),
        assistant=assistant,
        emergency_service=EmergencyService(),
        wecom=TransactionalOutboxWeCom(
            session, source_message_id=msgid, source_guest_message_id=msgid,
            delivery_phase=phase,
        ),
        agent_id=1000002,
        duty_employee_userids=["staff-1"],
        business_tasks=BusinessTaskService(operations),
        audit_events=operations,
        jobs=SQLAlchemyJobRepository(session),
        complaint_service=ComplaintService(),
        complaint_reviews=SQLAlchemyComplaintRepository(session),
        knowledge_referrals=SQLAlchemyKnowledgeReferralRepository(session),
        commit_boundary=session.commit,
        savepoint_factory=session.begin_nested,
    )


def _gap_decision(text: str = GAP) -> AssistantDecision:
    """一项资料缺失、模型选中转交的主回复结果。"""
    unit = GapUnit(unit_id=f"1:0-{len(text)}", item_id=1, text=text,
                   reason="knowledge_missing", property_id=None)
    return AssistantDecision(
        reply_text=UNCONFIRMED, language=Language.ZH, intent="faq", confidence=0.9,
        reply_parts=[ReplyPart(question=text, status="missing", text=UNCONFIRMED,
                               gap_unit=unit.unit_id)],
        turn_plan=plan_for(text, ("static_fact", text)),
        referral_candidates=[ReferralCandidate(unit=unit, canonical_question="健身房开放时间",
                                               category="设施")],
    )


def _request_plan(text: str, *, complaint: bool):
    """「太开心了!!!」加送水：闲聊项与服务申请项，可选把闲聊项标为投诉。"""
    return plan_for(
        text,
        ("chitchat", "第一次来太开心了!!!",
         {"risk": "complaint" if complaint else "none"}),
        ("service_request", "麻烦送两瓶水", {"subject": "水"}),
    )


class _Assistant:
    """可在规划或主回复处暂停的助手替身，记录调用次数。"""

    def __init__(self, *, decision=None, plan=None, pause: str | None = None,
                 error: Exception | None = None) -> None:
        self.decision, self.plan, self.pause, self.error = decision, plan, pause, error
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.respond_calls = 0
        self.plan_calls = 0

    async def plan_turn(self, *, text: str, language: Language):
        self.plan_calls += 1
        if self.pause == "plan":
            self.entered.set()
            await self.release.wait()
        return self.plan(text) if callable(self.plan) else self.plan

    async def respond(self, **kwargs: Any) -> AssistantDecision:
        self.respond_calls += 1
        if self.pause == "respond":
            self.entered.set()
            await self.release.wait()
        if self.error is not None:
            raise self.error
        decision = self.decision
        if callable(decision):
            decision = decision(kwargs.get("turn_plan"))
        return decision


async def _counts(factory) -> Counter:
    """按作业类型（最终作业单列）、客诉、业务任务与转交计数。"""
    async with factory() as session:
        jobs = (await session.scalars(select(Job))).all()
        counter: Counter = Counter()
        for job in jobs:
            key = job.job_type
            if key == "wecom_process_message":
                key = f"{key}:{job.payload.get('phase')}"
            counter[key] += 1
        counter["complaint_reviews"] = await session.scalar(
            select(func.count()).select_from(ComplaintReview)) or 0
        counter["business_tasks"] = await session.scalar(
            select(func.count()).select_from(BusinessTask)) or 0
        counter["referrals"] = await session.scalar(
            select(func.count()).select_from(KnowledgeReferral)) or 0
        return counter


async def _guest_texts(factory) -> list[str]:
    """客人出站正文（按登记顺序）。"""
    async with factory() as session:
        return [job.payload["content"] for job in (await session.scalars(
            select(Job).where(Job.job_type == "wecom_send_text").order_by(Job.id)
        )).all()]


async def _clear(factory, conversation_id: int) -> None:
    """管理员清空测试号数据（独立事务）。"""
    async with factory() as session:
        conversation = await session.get(Conversation, conversation_id)
        await SQLAlchemyCustomerRepository(session).clear_test_customer_data(
            conversation.customer_id, administrator_id=None  # type: ignore[arg-type]
        )
        await session.commit()


# ---- §2.4(1) 暂停主回复期间原生接管 ----


@pytest.mark.parametrize("accept", [True, False])
@pytest.mark.asyncio
async def test_native_takeover_during_main_reply_discards_old_result(
    pg_engine, accept  # noqa: F811
) -> None:
    """人工模式会话，主回复等待期间管家接入（只写接管审计、不写员工消息）：旧结果出站 0。

    对照：不接入时照常回复，说明丢弃来自原生接管判据而不是别的原因。
    """
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    conversation_id, source = await _seed(factory, "pg-native-1", GAP, human=True)
    assistant = _Assistant(decision=_gap_decision(), pause="respond")
    async with factory() as session:
        task = asyncio.create_task(
            _run_deferred_source(session, _service(session, assistant, source.msgid,
                                                   phase="final"), source, phase="final")
        )
        try:
            await asyncio.wait_for(assistant.entered.wait(), 5)
            if accept:
                async with factory() as other:
                    await SQLAlchemyConversationRepository(other).lock_activity(conversation_id)
                    await SQLAlchemyOperationsRepository(other).accept_handoff(
                        conversation_id, servicer_userid="staff-1", now=datetime.now(UTC)
                    )
                    await other.commit()
            assistant.release.set()
            await asyncio.wait_for(task, 5)
            await session.commit()
        finally:
            assistant.release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    texts = await _guest_texts(factory)
    assert len(texts) == (0 if accept else 1)
    counts = await _counts(factory)
    assert counts["business_tasks"] == 0
    assert counts["wecom_send_internal_markdown"] == 0


# ---- v10 等待中清空（V9-R1） ----


@pytest.mark.parametrize("exit_kind", ["success", "failure", "planning"])
@pytest.mark.asyncio
async def test_clear_committed_during_wait_leaves_no_new_derivations(
    pg_engine, exit_kind  # noqa: F811
) -> None:
    """主回复成功出口、失败出口、软规划返回出口：等待中清空提交，再恢复结果，新派生全为 0。"""
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    if exit_kind == "planning":
        conversation_id, source = await _seed(factory, "pg-clear-1", "第一次来太开心了!!!")
        assistant = _Assistant(
            plan=lambda text: _request_plan(text, complaint=True), pause="plan"
        )
        phase = "debounce"
    else:
        conversation_id, source = await _seed(factory, "pg-clear-1", GAP)
        assistant = _Assistant(
            decision=_gap_decision(), pause="respond",
            error=AssistantUnavailableError() if exit_kind == "failure" else None,
        )
        phase = "final"
    async with factory() as session:
        task = asyncio.create_task(_run_deferred_source(
            session, _service(session, assistant, source.msgid,
                              phase="ack" if phase == "debounce" else "final"),
            source, phase=phase,
        ))
        try:
            await asyncio.wait_for(assistant.entered.wait(), 5)
            await _clear(factory, conversation_id)
            assistant.release.set()
            await asyncio.wait_for(task, 5)
            await session.commit()
        finally:
            assistant.release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    counts = await _counts(factory)
    derived = {key: value for key, value in counts.items() if value}
    assert derived == {}, derived
    async with factory() as session:
        contents = (await session.scalars(select(Message.content))).all()
        assert all(content is None for content in contents)


# ---- v10 锁顺序（V9-R1） ----


@pytest.mark.asyncio
async def test_processing_holding_lock_commits_first_then_clear_removes_it(
    pg_engine,  # noqa: F811
) -> None:
    """处理先锁先提交、随后清空：清空等锁后清掉新写的转交正文；不死锁。"""
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    conversation_id, source = await _seed(factory, "pg-order-1", GAP)
    async with factory() as session:
        await _run_deferred_source(
            session, _service(session, _Assistant(decision=_gap_decision()), source.msgid,
                              phase="final"), source, phase="final",
        )
        # 业务尚未提交，会话活动锁仍由处理一侧持有。
        clearing = asyncio.create_task(_clear(factory, conversation_id))
        await asyncio.sleep(0.5)
        assert not clearing.done(), "清空应等待会话锁"
        await session.commit()
    await asyncio.wait_for(clearing, 5)
    async with factory() as session:
        [referral] = (await session.scalars(select(KnowledgeReferral))).all()
        assert referral.status is KnowledgeReferralStatus.CLEARED and referral.question is None


@pytest.mark.asyncio
async def test_clear_holding_lock_commits_first_then_processing_stops(
    pg_engine,  # noqa: F811
) -> None:
    """清空先锁先提交、随后处理：处理在来源复核处看到清空直接结束；新来源照常处理。"""
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    conversation_id, source = await _seed(factory, "pg-order-2", GAP)
    assistant = _Assistant(decision=_gap_decision())
    async with factory() as clearing:
        conversation = await clearing.get(Conversation, conversation_id)
        await SQLAlchemyCustomerRepository(clearing).clear_test_customer_data(
            conversation.customer_id, administrator_id=None  # type: ignore[arg-type]
        )

        async def process() -> None:
            async with factory() as session:
                await _run_deferred_source(
                    session, _service(session, assistant, source.msgid, phase="final"),
                    source, phase="final",
                )
                await session.commit()

        processing = asyncio.create_task(process())
        await asyncio.sleep(0.5)
        await clearing.commit()
    await asyncio.wait_for(processing, 5)
    counts = await _counts(factory)
    assert counts["referrals"] == 0 and counts["wecom_send_text"] == 0
    fresh = _guest("pg-order-3", GAP)
    async with factory() as session:
        await MessageService(SQLAlchemyMessageRepository(session)).record_incoming(
            conversation_id, fresh
        )
        await session.commit()
    async with factory() as session:
        await _run_deferred_source(
            session, _service(session, _Assistant(decision=_gap_decision()), fresh.msgid,
                              phase="final"), fresh, phase="final",
        )
        await session.commit()
    assert (await _counts(factory))["referrals"] == 1


# ---- §2.4(2) 中断窗口：真实 Worker、recover_stale、claim_next ----


class _Barrier:
    """业务提交之后、handler 返回之前的暂停屏障。"""

    def __init__(self) -> None:
        self.reached = asyncio.Event()
        self.never = asyncio.Event()

    async def wait(self) -> None:
        self.reached.set()
        await self.never.wait()


def _handler(factory, assistant, barrier: _Barrier | None = None):
    """生产同款处理器：独立业务会话、补齐装配的会话服务，提交后才返回。"""

    async def handle(payload: dict[str, Any]) -> None:
        message = _deferred_message_from_payload(payload)
        phase = str(payload.get("phase", "final"))
        async with factory() as session:
            service = _service(session, assistant, message.msgid,
                               phase="ack" if phase == "debounce" else "final")
            await _run_deferred_source(session, service, message, phase=phase)
            await session.commit()
        if barrier is not None:
            await barrier.wait()

    return handle


async def _run_worker(factory, handlers: dict[str, Any], *, on_job_failed=None) -> bool:
    """队列会话与业务会话分离的真实 Worker.run_once。"""
    async with factory() as queue:
        worker = Worker(
            repository=SQLAlchemyJobRepository(queue), handlers=handlers,
            checkpoint=queue.commit, on_job_failed=on_job_failed,
        )
        return await worker.run_once()


async def _interrupt(factory, handlers) -> int:
    """跑到屏障处取消，返回仍为 RUNNING 的作业编号。"""
    barrier = next(
        handler.barrier for handler in handlers.values() if hasattr(handler, "barrier")
    )
    task = asyncio.create_task(_run_worker(factory, handlers))
    await asyncio.wait_for(barrier.reached.wait(), 10)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    async with factory() as session:
        [running] = (await session.scalars(
            select(Job).where(Job.status == JobStatus.RUNNING)
        )).all()
        return running.id


async def _recover(factory, job_id: int, *, max_attempts: int = 3) -> None:
    """真实 recover_stale：同一编号、同一载荷，RUNNING→PENDING、locked_at 清空。"""
    async with factory() as session:
        before_payload = (await session.get(Job, job_id)).payload
        await SQLAlchemyJobRepository(session).recover_stale(
            before=datetime.now(UTC) + timedelta(seconds=1), max_attempts=max_attempts
        )
        await session.commit()
    async with factory() as session:
        job = await session.get(Job, job_id)
        if job.status is JobStatus.PENDING:
            assert job.payload == before_payload and job.locked_at is None


def _barrier_handler(factory, assistant):
    """带暂停屏障的处理器；屏障挂在处理器上供 `_interrupt` 等待。"""
    barrier = _Barrier()
    handler = _handler(factory, assistant, barrier)
    handler.barrier = barrier  # type: ignore[attr-defined]
    return handler


async def _enqueue_debounce(factory, source) -> None:
    """登记合并作业（立即到期）。"""
    async with factory() as session:
        await SQLAlchemyJobRepository(session).enqueue(
            "wecom_process_message",
            {"phase": "debounce", "msgid": source.msgid, "open_kfid": source.open_kfid,
             "external_userid": source.external_userid, "origin": "guest",
             "msgtype": "text", "content": source.content,
             "sent_at": (source.sent_at - timedelta(seconds=10)).isoformat()},
            dedupe_key=f"debounce:{source.msgid}",
        )
        await session.commit()


def _request_decision(complaint: bool):
    """服务申请的主回复：按传入的计划登记任务。"""

    def build(turn_plan):
        return AssistantDecision(
            reply_text="好的。", language=Language.ZH, intent="service_request", confidence=0.9,
            turn_plan=turn_plan,
        )

    return build


async def _drain(factory, handlers) -> None:
    """把到期作业依次执行完（只执行有处理器的类型）。"""
    for _ in range(10):
        async with factory() as session:
            pending = (await session.scalars(select(Job).where(
                Job.status == JobStatus.PENDING, Job.job_type.in_(list(handlers))
            ))).all()
        if not pending:
            return
        await _run_worker(factory, handlers)


@pytest.mark.parametrize("complaint", [False, True])
@pytest.mark.asyncio
async def test_window_one_planning_interrupted_then_rebuilt(pg_engine, complaint) -> None:  # noqa: F811
    """窗口一：合并作业释放锁规划的提交之后、复核结论落地之前中断；直接重建续接。"""
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    _, source = await _seed(factory, "pg-w1", CALM_REQUEST)
    paused = _Assistant(plan=lambda text: _request_plan(text, complaint=complaint), pause="plan")
    async with factory() as session:
        task = asyncio.create_task(_run_deferred_source(
            session, _service(session, paused, source.msgid, phase="ack"), source,
            phase="debounce",
        ))
        await asyncio.wait_for(paused.entered.wait(), 5)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert sum((await _counts(factory)).values()) == 0  # 中间提交没有落下任何副作用
    resumed = _Assistant(plan=lambda text: _request_plan(text, complaint=complaint),
                         decision=_request_decision(complaint))
    async with factory() as session:
        await _run_deferred_source(
            session, _service(session, resumed, source.msgid, phase="ack"), source,
            phase="debounce",
        )
        await session.commit()
    await _drain(factory, {"wecom_process_message": _handler(factory, resumed)})
    counts = await _counts(factory)
    if complaint:
        assert counts["complaint_reviews"] == 1
        assert counts["complaint_review_generate"] == 1
        assert counts["wecom_process_message:final"] == 0
        assert len(await _guest_texts(factory)) == 1
    else:
        assert counts["wecom_process_message:final"] == 1
        assert counts["business_tasks"] == 1
        assert counts["complaint_reviews"] == 0
        assert len(await _guest_texts(factory)) == 1


@pytest.mark.parametrize("complaint", [False, True])
@pytest.mark.asyncio
async def test_window_two_debounce_interrupted_after_business_commit(
    pg_engine, complaint  # noqa: F811
) -> None:
    """窗口二：合并作业业务提交之后、Worker 标记完成之前取消；recover_stale → claim_next。

    投诉分支是合并阶段直接终结（写收据），重放时规划器不被调用、客诉派生各 1；平静分支
    排出最终作业（不写收据），最终作业正常回复，说明收据不挡正常回复。
    """
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    _, source = await _seed(factory, "pg-w2", CALM_REQUEST)
    await _enqueue_debounce(factory, source)
    first = _Assistant(plan=lambda text: _request_plan(text, complaint=complaint),
                       decision=_request_decision(complaint))
    handler = _barrier_handler(factory, first)
    job_id = await _interrupt(factory, {"wecom_process_message": handler})
    committed = await _counts(factory)
    await _recover(factory, job_id)
    replay = _Assistant(plan=lambda text: _request_plan(text, complaint=False),
                        decision=_request_decision(False))
    await _drain(factory, {"wecom_process_message": _handler(factory, replay)})
    counts = await _counts(factory)
    if complaint:
        assert replay.plan_calls == 0 and replay.respond_calls == 0
        assert counts == committed
        assert counts["complaint_reviews"] == 1 and counts["complaint_review_generate"] == 1
        assert counts["wecom_process_message:final"] == 0
        assert len(await _guest_texts(factory)) == 1
    else:
        assert counts["wecom_process_message:final"] == 1
        assert counts["business_tasks"] == 1
        assert len(await _guest_texts(factory)) == 1


@pytest.mark.asyncio
async def test_final_window_replay_is_not_recomputed(pg_engine) -> None:  # noqa: F811
    """最终作业业务提交后、标记完成前中断：恢复后第二次助手（改了计划、风险与缺口）不被调用，
    全部副作用、正文与队列载荷与首次完全一致（V7-R1）。"""
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    _, source = await _seed(factory, "pg-final", GAP)
    async with factory() as session:
        await SQLAlchemyJobRepository(session).enqueue(
            "wecom_process_message",
            {"phase": "final", "msgid": source.msgid, "open_kfid": source.open_kfid,
             "external_userid": source.external_userid, "origin": "guest", "msgtype": "text",
             "content": source.content, "sent_at": source.sent_at.isoformat()},
            dedupe_key=f"final:{source.msgid}",
        )
        await session.commit()
    first = _Assistant(decision=_gap_decision())
    handler = _barrier_handler(factory, first)
    job_id = await _interrupt(factory, {"wecom_process_message": handler})
    committed, texts = await _counts(factory), await _guest_texts(factory)
    async with factory() as session:
        payloads = sorted(
            str(job.payload) for job in (await session.scalars(select(Job))).all()
            if job.id != job_id
        )
    await _recover(factory, job_id)
    different = AssistantDecision(
        reply_text="完全不同", language=Language.ZH, intent="faq", confidence=0.9,
    )
    replay = _Assistant(decision=different)
    await _drain(factory, {"wecom_process_message": _handler(factory, replay)})
    assert replay.respond_calls == 0
    assert await _counts(factory) == committed
    assert await _guest_texts(factory) == texts
    async with factory() as session:
        assert sorted(
            str(job.payload) for job in (await session.scalars(select(Job))).all()
            if job.id != job_id
        ) == payloads
        assert (await session.get(Job, job_id)).status is JobStatus.COMPLETED


# ---- 草稿失败落点（V8-R2） ----


class _Drafter:
    """草稿模型替身：始终失败，或在指定时机暂停。"""

    def __init__(self, *, fail: bool = True) -> None:
        self.fail = fail

    async def generate_from_staff(self, **kwargs: Any):
        if self.fail:
            raise FaqDraftUnavailableError()
        from homestay_bot.integrations.deepseek_faq_drafter import StaffDraft

        return StaffDraft(
            found_answer=True, category="设施", question_zh="健身房几点开门？",
            answer_zh="七点开门。", question_en="When does the gym open?",
            answer_en="7 a.m.", source_quotes=["七点开门"],
        )


async def _drafting_referral(factory) -> None:
    """一条转交、一条管家回复，管理员已点整理（整理中，代次 1）。"""
    conversation_id, source = await _seed(factory, "pg-draft", GAP)
    async with factory() as session:
        session.add(KnowledgeReferral(
            conversation_id=conversation_id, source_message_id=source.msgid, unit_id="1:0-7",
            plan_item_id=1, question=GAP, normalized_question="健身房开放时间",
        ))
        await session.flush()
        session.add(Message(
            conversation_id=conversation_id, external_message_id="pg-staff",
            origin=MessageOrigin.SERVICER, message_type="text", content="七点开门",
            sent_at=datetime.now(UTC) + timedelta(seconds=5),
        ))
        await session.commit()
    async with factory() as session:
        await KnowledgeAdminService(session).start_referral_draft(1, None)  # type: ignore[arg-type]


def _draft_handlers(factory, drafter, barrier: _Barrier | None = None):
    """生产同款草稿处理器（独立业务会话）。"""

    async def handle(payload: dict[str, Any]) -> None:
        async with factory() as session:
            await ReferralDraftJobService(
                referrals=SQLAlchemyKnowledgeReferralRepository(session), drafter=drafter
            ).handle(payload)
            await session.commit()
        if barrier is not None:
            await barrier.wait()

    if barrier is not None:
        handle.barrier = barrier  # type: ignore[attr-defined]
    return {"knowledge_referral_draft": handle}


async def _on_failed(factory):
    """生产失败回调的草稿分支：作业已为 FAILED 才写草稿失败。"""

    async def callback(job, error_code, failed_payload) -> None:
        if job.status is JobStatus.FAILED:
            async with factory() as session:
                await _fail_exhausted_referral_draft(session, failed_payload)
                await session.commit()

    return callback


async def _referral_status(factory) -> KnowledgeReferralStatus:
    async with factory() as session:
        return (await session.get(KnowledgeReferral, 1)).status


async def _make_due(factory) -> None:
    """跳过重试退避，让作业立即可领取。"""
    async with factory() as session:
        await session.execute(update(Job).values(available_at=datetime.now(UTC)))
        await session.commit()


@pytest.mark.asyncio
async def test_draft_exhaustion_through_worker_failure_callback(pg_engine) -> None:  # noqa: F811
    """普通异常第 1、2 次保持整理中，第 3 次进入草稿失败。"""
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    await _drafting_referral(factory)
    callback = await _on_failed(factory)
    for attempt in (1, 2, 3):
        await _make_due(factory)
        await _run_worker(factory, _draft_handlers(factory, _Drafter()), on_job_failed=callback)
        expected = (KnowledgeReferralStatus.DRAFT_FAILED if attempt == 3
                    else KnowledgeReferralStatus.DRAFTING)
        assert await _referral_status(factory) is expected, attempt


@pytest.mark.parametrize("result_committed", [False, True])
@pytest.mark.asyncio
async def test_draft_exhaustion_through_recover_stale(
    pg_engine, result_committed  # noqa: F811
) -> None:
    """第三次领取后取消，recover_stale 也进入草稿失败；结果已提交后取消，不把已整理改成失败。"""
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    await _drafting_referral(factory)
    callback = await _on_failed(factory)
    for _ in (1, 2):
        await _make_due(factory)
        await _run_worker(factory, _draft_handlers(factory, _Drafter()), on_job_failed=callback)
    await _make_due(factory)
    barrier = _Barrier()
    if result_committed:
        handlers = _draft_handlers(factory, _Drafter(fail=False), barrier)
    else:
        # 第三次在模型调用处卡住：业务没有提交任何结果。
        class _Stuck:
            async def generate_from_staff(self, **kwargs):
                await barrier.wait()

        handlers = _draft_handlers(factory, _Stuck())
        handlers["knowledge_referral_draft"].barrier = barrier  # type: ignore[attr-defined]
    job_id = await _interrupt(factory, handlers)
    await _recover(factory, job_id)
    async with factory() as session:
        assert (await session.get(Job, job_id)).status is JobStatus.FAILED
    expected = (KnowledgeReferralStatus.DRAFTED if result_committed
                else KnowledgeReferralStatus.DRAFT_FAILED)
    assert await _referral_status(factory) is expected


# ---- 并发入库（PostgreSQL 行锁） ----


@pytest.mark.asyncio
async def test_concurrent_convert_waits_for_row_lock(pg_engine) -> None:  # noqa: F811
    """并发入库按行锁串行：另一事务持锁并结束记录后，等待中的入库被拒，不建知识。"""
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    await _drafting_referral(factory)
    await _make_due(factory)
    await _run_worker(factory, _draft_handlers(factory, _Drafter(fail=False)))
    assert await _referral_status(factory) is KnowledgeReferralStatus.DRAFTED
    fields = {
        "scope": "global", "property_id": None, "valid_from": None, "valid_until": None,
        "category": "设施", "question_zh": "健身房几点开门？", "answer_zh": "七点开门。",
        "question_en": "When does the gym open?", "answer_en": "7 a.m.", "keywords": [],
        "trigger_any": None, "trigger_exclude": None,
    }

    async def convert():
        async with factory() as session:
            return await KnowledgeAdminService(session).convert_referral(
                1, None, generation=1, reviewed=True, **fields  # type: ignore[arg-type]
            )

    # 第一个入库事务持有转交行锁尚未提交时，第二个必须等待；第一个提交后第二个看到状态已变而
    # 被拒，只建一条知识。没有行锁时两者都读到「已整理」，会各建一条。
    async with factory() as first:
        locked = await SQLAlchemyKnowledgeReferralRepository(first).get(1, lock=True)
        assert locked is not None
        second = asyncio.create_task(convert())
        await asyncio.sleep(0.5)
        assert not second.done(), "第二个入库应等待行锁"
        locked.status = KnowledgeReferralStatus.CONVERTED
        await first.commit()
    with pytest.raises(Exception, match="刷新"):
        await asyncio.wait_for(second, 5)
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(KnowledgeEntry)) == 0


@pytest.mark.asyncio
async def test_two_real_conversions_create_exactly_one_entry(pg_engine) -> None:  # noqa: F811
    """两个实际入库入口并发：一条启用知识、一个已入库关联，失败者没有孤立写入。"""
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    await _drafting_referral(factory)
    await _make_due(factory)
    await _run_worker(factory, _draft_handlers(factory, _Drafter(fail=False)))

    async def convert():
        """每个请求独立会话，保留真实校验、行锁、CAS 与事务。"""
        async with factory() as session:
            return await KnowledgeAdminService(session).convert_referral(
                1, None, generation=1, reviewed=True,
                scope="global", property_id=None, valid_from=None, valid_until=None,
                category="设施", question_zh="健身房几点开门？", answer_zh="七点开门。",
                question_en="When does the gym open?", answer_en="7 a.m.", keywords=[],
                trigger_any=None, trigger_exclude=None,
            )

    results = await asyncio.wait_for(
        asyncio.gather(convert(), convert(), return_exceptions=True), 10,
    )
    assert sum(isinstance(result, KnowledgeEntry) for result in results) == 1
    assert sum(isinstance(result, KnowledgeFormError) for result in results) == 1
    async with factory() as session:
        [entry] = (await session.scalars(select(KnowledgeEntry))).all()
        record = await session.get(KnowledgeReferral, 1)
        assert entry.is_enabled and record.knowledge_entry_id == entry.id
        assert record.status is KnowledgeReferralStatus.CONVERTED
