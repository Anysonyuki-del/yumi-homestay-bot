"""知识缺口转管家核实（修复 Spec 2026-10-08 fix2 §2.6）：真实仓储加临时 SQLite。

覆盖会话层登记转交与客人文案、来源完成收据与来源清空、资料核实卡片、草稿代次、入库与清空。
助手与平台是本地替身，不调用真实 DeepSeek 或企业微信。
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.application import (
    TransactionalOutboxWeCom,
    _guest_reply_is_stale,
    _handoff_card_for,
    _run_deferred_source,
    _send_referral_card,
)
from homestay_bot.domain.enums import (
    ConversationMode,
    KnowledgeReferralStatus,
    Language,
    MessageOrigin,
)
from homestay_bot.domain.models import (
    Base,
    Conversation,
    Customer,
    CustomerTag,
    CustomerTagLink,
    Job,
    KnowledgeEntry,
    KnowledgeReferral,
    Message,
    PropertyProfile,
    SourceCompletionReceipt,
)
from homestay_bot.integrations.deepseek_client import AssistantDecision
from homestay_bot.integrations.deepseek_faq_drafter import (
    FaqDraftUnavailableError,
    StaffDraft,
)
from homestay_bot.repositories.conversations import (
    SQLAlchemyConversationRepository,
    SQLAlchemyMessageRepository,
)
from homestay_bot.repositories.customers import TEST_ACCOUNT_TAG, SQLAlchemyCustomerRepository
from homestay_bot.repositories.jobs import SQLAlchemyJobRepository
from homestay_bot.repositories.knowledge_referrals import SQLAlchemyKnowledgeReferralRepository
from homestay_bot.routes.knowledge import KnowledgeAdminService, KnowledgeFormError
from homestay_bot.services.conversation_service import ConversationService
from homestay_bot.services.emergency_service import EmergencyService
from homestay_bot.services.handoff_card import HANDOFF_CARD_JOB_TYPE, build_handoff_card
from homestay_bot.services.item_coverage import GapUnit, ReferralCandidate
from homestay_bot.services.knowledge_referral_service import ReferralDraftJobService
from homestay_bot.services.message_service import IncomingMessage, MessageService
from homestay_bot.services.reply_plan import ReplyPart
from homestay_bot.worker import RetrySafeJobError
from tests.plan_helpers import plan_for

QUESTION = "健身房几点开门"
UNCONFIRMED = "这项信息暂时无法确认。"


@pytest.fixture
async def factory():
    """每个用例一个内存库。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _guest(msgid: str, content: str = QUESTION, *, sent_at: datetime | None = None):
    """合成客人消息。"""
    return IncomingMessage(
        msgid=msgid, open_kfid="synthetic-kf", external_userid="synthetic-guest",
        origin=MessageOrigin.GUEST, msgtype="text", content=content,
        sent_at=sent_at or datetime.now(UTC),
    )


async def _seed(factory, msgid: str = "g-1", content: str = QUESTION, *, test_account=False):
    """建客户、会话并记录一条客人消息，返回（会话编号, 消息）。"""
    message = _guest(msgid, content)
    async with factory() as session:
        customer = Customer(display_name="合成客人")
        session.add_all([customer, PropertyProfile(id=401, room_number="401", title="合成401")])
        await session.flush()
        if test_account:
            tag = CustomerTag(name=TEST_ACCOUNT_TAG, is_active=True)
            session.add(tag)
            await session.flush()
            session.add(CustomerTagLink(customer_id=customer.id, tag_id=tag.id))
        conversation = await SQLAlchemyConversationRepository(session).get_or_create(message)
        conversation.customer_id = customer.id
        await MessageService(SQLAlchemyMessageRepository(session)).record_incoming(
            conversation.id, message
        )
        await session.commit()
        return conversation.id, message


def _decision(
    text: str = QUESTION,
    *,
    unit_text: str = QUESTION,
    canonical: str = "健身房开放时间",
    status: str = "missing",
    property_id: int | None = 401,
    reason: str = "knowledge_missing",
) -> AssistantDecision:
    """一项本店事实问题、本地判为资料缺失、模型选中转交的主回复结果。"""
    plan = plan_for(text, ("static_fact", unit_text))
    unit = GapUnit(
        unit_id=f"1:0-{len(unit_text)}", item_id=1, text=unit_text,
        reason=reason, property_id=property_id,  # type: ignore[arg-type]
    )
    return AssistantDecision(
        reply_text=UNCONFIRMED, language=Language.ZH, intent="faq", confidence=0.9,
        reply_parts=[ReplyPart(
            question=unit_text, status=status,  # type: ignore[arg-type]
            text=UNCONFIRMED if status == "missing" else "健身房早上七点开门。",
            gap_unit=unit.unit_id,
        )],
        turn_plan=plan,
        referral_candidates=[ReferralCandidate(
            unit=unit, canonical_question=canonical, category="设施",
        )],
    )


class _Assistant:
    """按顺序返回预设决定，并记录被调用次数。"""

    def __init__(self, *decisions: AssistantDecision) -> None:
        self.decisions = list(decisions)
        self.calls = 0

    async def respond(self, **kwargs: Any) -> AssistantDecision:
        self.calls += 1
        return self.decisions[min(self.calls, len(self.decisions)) - 1]


class _FailingCardJobs(SQLAlchemyJobRepository):
    """资料核实卡片入队失败，其他作业照常。"""

    async def enqueue(self, job_type, payload, **kwargs):
        if job_type == HANDOFF_CARD_JOB_TYPE:
            raise RuntimeError("card queue down")
        return await super().enqueue(job_type, payload, **kwargs)


def _service(session, assistant, msgid: str, *, jobs=None) -> ConversationService:
    """按生产 deferred 装配构造会话服务（真实仓储、作业、出站、保存点与提交边界）。"""
    return ConversationService(
        conversations=SQLAlchemyConversationRepository(session),
        messages=MessageService(SQLAlchemyMessageRepository(session)),
        assistant=assistant,
        emergency_service=EmergencyService(),
        wecom=TransactionalOutboxWeCom(
            session, source_message_id=msgid, source_guest_message_id=msgid,
            delivery_phase="final",
        ),
        agent_id=1000002,
        duty_employee_userids=["staff-1"],
        jobs=jobs or SQLAlchemyJobRepository(session),
        knowledge_referrals=SQLAlchemyKnowledgeReferralRepository(session),
        commit_boundary=session.commit,
        savepoint_factory=session.begin_nested,
    )


async def _run_final(factory, assistant, message, *, jobs_class=None) -> ConversationService:
    """按生产后台最终作业入口执行一次：查收据、分发、写收据、提交。"""
    async with factory() as session:
        jobs = jobs_class(session) if jobs_class is not None else None
        service = _service(session, assistant, message.msgid, jobs=jobs)
        await _run_deferred_source(session, service, message, phase="final")
        await session.commit()
        return service


async def _jobs(factory, job_type: str) -> list[Job]:
    """按类型取作业。"""
    async with factory() as session:
        return list((await session.scalars(
            select(Job).where(Job.job_type == job_type).order_by(Job.id)
        )).all())


async def _referrals(factory) -> list[KnowledgeReferral]:
    """全部转交记录。"""
    async with factory() as session:
        return list((await session.scalars(
            select(KnowledgeReferral).order_by(KnowledgeReferral.id)
        )).all())


# ---- 会话层：登记转交、卡片与客人文案（§2.6(2)） ----


@pytest.mark.asyncio
async def test_gap_is_referred_with_minimal_card_and_fixed_guest_line(factory) -> None:
    """被接受的缺口：建转交、登记只含编号的卡片任务，客人文案在出口之后追加且不含未确认。"""
    conversation_id, message = await _seed(factory)
    service = await _run_final(factory, _Assistant(_decision()), message)

    [referral] = await _referrals(factory)
    assert referral.status is KnowledgeReferralStatus.PENDING
    assert (referral.conversation_id, referral.property_id) == (conversation_id, 401)
    assert referral.question == QUESTION and referral.unit_id == "1:0-7"
    assert service.created_referral_ids == [referral.id]
    [card] = await _jobs(factory, HANDOFF_CARD_JOB_TYPE)
    assert card.dedupe_key == f"referral-card:{referral.id}"
    # 载荷只有用途、编号与收件人：卡片正文由 worker 按记录组装，不带原文或聊天上下文。
    assert card.payload == {
        "purpose": "referral", "referral_id": referral.id, "agent_id": 1000002,
        "employee_userids": ["staff-1"], "task_id": f"referral-{referral.id}",
    }
    [reply] = await _jobs(factory, "wecom_send_text")
    assert reply.payload["content"] == f"「{QUESTION}」：资料里还没有确认，已提交管家核实。"
    async with factory() as session:
        receipt = await session.get(SourceCompletionReceipt, message.msgid)
        assert receipt is not None and receipt.referral_ids == [referral.id]


@pytest.mark.asyncio
async def test_card_queue_failure_keeps_current_unconfirmed_reply(factory) -> None:
    """卡片没入队：保存点回滚该单元，不留转交记录，客人仍收到现行未确认。"""
    _, message = await _seed(factory)
    await _run_final(factory, _Assistant(_decision()), message, jobs_class=_FailingCardJobs)

    assert await _referrals(factory) == []
    [reply] = await _jobs(factory, "wecom_send_text")
    assert UNCONFIRMED in reply.payload["content"]
    assert "管家核实" not in reply.payload["content"]


@pytest.mark.parametrize(
    ("decision", "case"),
    [
        (_decision(status="grounded"), "已回答的单元"),
        (_decision(canonical="房价多少钱"), "交易类标准问题"),
        (_decision("门锁密码是abcd1234吗", unit_text="门锁密码是abcd1234吗"), "含凭据"),
        (_decision(reason="conflict"), "冲突不是资料缺失"),
    ],
)
@pytest.mark.asyncio
async def test_units_that_are_not_referable_stay_unconfirmed(factory, decision, case) -> None:
    """模型选中不是缺口证明：已回答、交易类、含凭据或原因不是资料缺失都不转交。"""
    _, message = await _seed(factory, content=decision.referral_candidates[0].unit.text)
    if case == "冲突不是资料缺失":
        # 适配器层只接受资料缺失；这里模拟适配器已经把候选筛掉。
        decision = decision.model_copy(update={"referral_candidates": []})
    await _run_final(factory, _Assistant(decision), message)
    assert await _referrals(factory) == [], case
    assert await _jobs(factory, HANDOFF_CARD_JOB_TYPE) == []


@pytest.mark.asyncio
async def test_human_mode_does_not_refer(factory) -> None:
    """人工接待中不登记转交（会话须为机器人模式）。"""
    conversation_id, message = await _seed(factory)
    async with factory() as session:
        (await session.get(Conversation, conversation_id)).mode = ConversationMode.HUMAN_ACTIVE
        await session.commit()
    await _run_final(factory, _Assistant(_decision()), message)
    assert await _referrals(factory) == []


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (KnowledgeReferralStatus.PENDING, f"「{QUESTION}」：已提交管家核实，请稍候。"),
        (KnowledgeReferralStatus.DRAFTED, UNCONFIRMED),
    ],
)
@pytest.mark.asyncio
async def test_cooldown_reuses_pending_referral_without_new_card(factory, status, expected):
    """同会话同问题 24 小时内：仍待处理告诉客人稍候，已整理回现行未确认；都不发卡片。"""
    _, first = await _seed(factory)
    await _run_final(factory, _Assistant(_decision()), first)
    async with factory() as session:
        (await session.get(KnowledgeReferral, 1)).status = status
        second = _guest("g-2")
        await MessageService(SQLAlchemyMessageRepository(session)).record_incoming(1, second)
        await session.commit()
    await _run_final(factory, _Assistant(_decision()), second)

    assert len(await _referrals(factory)) == 1
    assert len(await _jobs(factory, HANDOFF_CARD_JOB_TYPE)) == 1
    replies = await _jobs(factory, "wecom_send_text")
    assert expected in replies[-1].payload["content"]


@pytest.mark.asyncio
async def test_closed_question_is_suppressed_for_thirty_days(factory) -> None:
    """管理员关闭为「不入库」后，同问题同房源不再转交，只回现行未确认。"""
    _, first = await _seed(factory)
    await _run_final(factory, _Assistant(_decision()), first)
    async with factory() as session:
        await KnowledgeAdminService(session).close_referral(1, employee_id=None)  # type: ignore[arg-type]
    async with factory() as session:
        conversation = Conversation(open_kfid="synthetic-kf", external_userid="other-guest")
        session.add(conversation)
        await session.flush()
        other = replace_user(_guest("g-other"))
        await MessageService(SQLAlchemyMessageRepository(session)).record_incoming(
            conversation.id, other
        )
        await session.commit()
    await _run_final(factory, _Assistant(_decision()), other)

    assert len(await _referrals(factory)) == 1
    assert UNCONFIRMED in (await _jobs(factory, "wecom_send_text"))[-1].payload["content"]


def replace_user(message: IncomingMessage) -> IncomingMessage:
    """换成另一位客人（另一会话），排除会话冷却的干扰。"""
    from dataclasses import replace

    return replace(message, external_userid="other-guest")


@pytest.mark.asyncio
async def test_guest_label_is_one_line_sanitized_and_truncated(factory) -> None:
    """客人原话引用压成一行、去链接、截断到 30 字。"""
    from homestay_bot.services.conversation_service import referral_label

    label = referral_label("健身房\n几点开门 https://evil.example/x 还有" + "很" * 40)
    assert "\n" not in label and "http" not in label
    assert len(label) == 30 and label.endswith("…")


# ---- 来源完成收据与来源清空（§2.6(2) 恢复规则） ----


@pytest.mark.asyncio
async def test_receipt_stops_replay_from_recomputing(factory) -> None:
    """业务提交后重放：收据存在就直接结束，助手不被调用，副作用与正文与首次一致。"""
    _, message = await _seed(factory)
    await _run_final(factory, _Assistant(_decision()), message)
    replay = _Assistant(_decision(canonical="完全不同的问题", property_id=None))
    await _run_final(factory, replay, message)

    assert replay.calls == 0
    assert len(await _referrals(factory)) == 1
    assert len(await _jobs(factory, HANDOFF_CARD_JOB_TYPE)) == 1
    assert len(await _jobs(factory, "wecom_send_text")) == 1


@pytest.mark.asyncio
async def test_without_receipt_replay_recomputes_without_duplicates(factory) -> None:
    """对照：首次在提交前中断（无收据）时从头计算，唯一约束与去重键保证不重复。"""
    _, message = await _seed(factory)
    async with factory() as session:
        service = _service(session, _Assistant(_decision()), message.msgid)
        await service.process_recorded_message(message)
        await session.commit()  # 业务已提交、收据未写：模拟旧版本或中断前的状态
    second = _Assistant(_decision())
    await _run_final(factory, second, message)

    assert second.calls == 1
    assert len(await _referrals(factory)) == 1
    assert len(await _jobs(factory, HANDOFF_CARD_JOB_TYPE)) == 1


@pytest.mark.asyncio
async def test_cleared_source_is_not_processed_and_queued_reply_is_blocked(factory) -> None:
    """清空之后：旧最终任务首次执行直接结束；已排队的客人回复在发送处被拦，豁免也不行。"""
    conversation_id, message = await _seed(factory, test_account=True)
    async with factory() as session:
        conversation = await session.get(Conversation, conversation_id)
        await SQLAlchemyCustomerRepository(session).clear_test_customer_data(
            conversation.customer_id, administrator_id=None  # type: ignore[arg-type]
        )
        await session.commit()
    assistant = _Assistant(_decision())
    await _run_final(factory, assistant, message)
    assert assistant.calls == 0
    async with factory() as session:
        assert await _guest_reply_is_stale(session, {
            "open_kfid": "synthetic-kf", "external_userid": "synthetic-guest",
            "source_guest_message_id": message.msgid, "stale_exempt": True,
        })


@pytest.mark.asyncio
async def test_debounce_receipt_only_when_terminal(factory) -> None:
    """合并阶段排出最终作业时不写收据（由最终作业写）；直接终结时写。"""
    _, staged = await _seed(factory, "g-staged", "明天早餐几点")
    async with factory() as session:
        service = _service(session, _Assistant(), staged.msgid)
        await _run_deferred_source(session, service, staged, phase="debounce")
        await session.commit()
        assert await session.get(SourceCompletionReceipt, staged.msgid) is None
    async with factory() as session:
        newer = _guest("g-newer", "再问一下")
        await MessageService(SQLAlchemyMessageRepository(session)).record_incoming(1, newer)
        await session.commit()
    async with factory() as session:
        # 有更新消息：合并阶段放弃本来源，属于直接终结。
        service = _service(session, _Assistant(), staged.msgid)
        await _run_deferred_source(session, service, staged, phase="debounce")
        await session.commit()
        assert await session.get(SourceCompletionReceipt, staged.msgid) is not None


# ---- 资料核实卡片（§2.6(2)） ----


class _CardSink:
    """捕获 worker 实际发出的模板卡片。"""

    def __init__(self) -> None:
        self.cards: list[dict[str, Any]] = []

    async def send_internal_template_card(self, **kwargs: Any) -> None:
        self.cards.append(kwargs["template_card"])


@pytest.mark.asyncio
async def test_worker_card_carries_only_minimal_sanitized_content(factory) -> None:
    """worker 组卡只用转交记录：原因、净化原问、房间与对话链接；不混入聊天原文。"""
    # 同一会话里更早的客人消息含凭据与订单号：卡片不能把它当「客人最近说」带出。
    conversation_id, _secret = await _seed(
        factory, "g-0", "我的门锁密码是 Ab12_X 订单 123456789012"
    )
    message = _guest("g-1")
    async with factory() as session:
        await MessageService(SQLAlchemyMessageRepository(session)).record_incoming(
            conversation_id, message
        )
        await session.commit()
    await _run_final(factory, _Assistant(_decision()), message)
    [job] = await _jobs(factory, HANDOFF_CARD_JOB_TYPE)
    sink = _CardSink()
    async with factory() as session:
        await _send_referral_card(session, sink, job.payload, public_base_url="https://yumi.test")
    [card] = sink.cards
    rendered = str(card)
    assert card["main_title"]["title"] == "资料待核实"
    assert QUESTION in rendered and "401" in rendered
    assert "Ab12_X" not in rendered and "123456789012" not in rendered
    assert "quote_area" not in card  # 没有接手要点或客人最近原话
    assert card["task_id"] == job.payload["task_id"]


@pytest.mark.parametrize(
    "status",
    [KnowledgeReferralStatus.CLOSED, KnowledgeReferralStatus.CLEARED,
     KnowledgeReferralStatus.DRAFTED],
)
@pytest.mark.asyncio
async def test_worker_skips_card_unless_pending(factory, status) -> None:
    """转交不是待处理（已整理、已关闭、已清空）时不发卡片。"""
    _, message = await _seed(factory)
    await _run_final(factory, _Assistant(_decision()), message)
    async with factory() as session:
        (await session.get(KnowledgeReferral, 1)).status = status
        await session.commit()
    [job] = await _jobs(factory, HANDOFF_CARD_JOB_TYPE)
    sink = _CardSink()
    async with factory() as session:
        await _send_referral_card(session, sink, job.payload, public_base_url="https://yumi.test")
    assert sink.cards == []


@pytest.mark.asyncio
async def test_card_rebuild_validates_referral_conversation(factory) -> None:
    """按钮快照：转交编号存在且属于同一会话才组卡；清空、缺失、会话不符返回空。"""
    conversation_id, message = await _seed(factory)
    await _run_final(factory, _Assistant(_decision()), message)
    async with factory() as session:
        conversation = await session.get(Conversation, conversation_id)
        other = Conversation(open_kfid="kf", external_userid="other")
        session.add(other)
        await session.flush()
        kwargs = {"identity_resolver": None, "public_base_url": "https://yumi.test"}
        card = await _handoff_card_for(session, conversation, "referral-1", **kwargs)
        assert card is not None and card.reason == "资料待核实"
        assert build_handoff_card(card, stage="pending")["main_title"]["title"] == "资料待核实"
        assert await _handoff_card_for(session, other, "referral-1", **kwargs) is None
        assert await _handoff_card_for(session, conversation, "referral-99", **kwargs) is None
        (await session.get(KnowledgeReferral, 1)).status = KnowledgeReferralStatus.CLEARED
        assert await _handoff_card_for(session, conversation, "referral-1", **kwargs) is None


# ---- 草稿代次、入库与清空（§2.6(3)–(6)） ----


class _Drafter:
    """草稿模型替身：返回预设结果或抛错，并记录收到的管家消息。"""

    def __init__(self, result: StaffDraft | Exception) -> None:
        self.result = result
        self.inputs: list[list[str]] = []

    async def generate_from_staff(self, *, guest_question, canonical_question, staff_messages):
        self.inputs.append(list(staff_messages))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


_GOOD_DRAFT = StaffDraft(
    found_answer=True, category="设施", question_zh="健身房几点开门？",
    answer_zh="健身房每天早上七点开门。", question_en="When does the gym open?",
    answer_en="The gym opens at 7 a.m. every day.", keywords=["健身房"],
    source_quotes=["健身房每天早上七点开门"],
)


async def _referral_with_staff_replies(factory, *replies: str, offset_seconds: int = 60):
    """建一条待处理转交，并在其后写入若干管家消息；返回会话编号。"""
    conversation_id, message = await _seed(factory)
    await _run_final(factory, _Assistant(_decision()), message)
    async with factory() as session:
        referral = await session.get(KnowledgeReferral, 1)
        base = referral.created_at.replace(tzinfo=UTC) if referral.created_at.tzinfo is None \
            else referral.created_at
        for index, reply in enumerate(replies, start=1):
            session.add(Message(
                conversation_id=conversation_id, external_message_id=f"s-{index}",
                origin=MessageOrigin.SERVICER, message_type="text", content=reply,
                sent_at=base + timedelta(seconds=offset_seconds + index),
            ))
        await session.commit()
    return conversation_id


async def _start_and_run_draft(factory, drafter) -> None:
    """管理员点整理，再执行一次草稿任务。"""
    async with factory() as session:
        await KnowledgeAdminService(session).start_referral_draft(1, employee_id=None)  # type: ignore[arg-type]
    [job] = [job for job in await _jobs(factory, "knowledge_referral_draft")][-1:]
    async with factory() as session:
        await ReferralDraftJobService(
            referrals=SQLAlchemyKnowledgeReferralRepository(session), drafter=drafter
        ).handle(job.payload)
        await session.commit()


@pytest.mark.asyncio
async def test_draft_uses_latest_five_non_sensitive_replies(factory) -> None:
    """寒暄 5 条后的真实答案在「最新 5 条」内；含凭据的消息只列出不交给模型。"""
    await _referral_with_staff_replies(
        factory, "您好", "稍等", "我问一下", "好的", "门锁密码是 Ab12_X",
        "健身房每天早上七点开门", "还有别的吗",
    )
    drafter = _Drafter(_GOOD_DRAFT)
    await _start_and_run_draft(factory, drafter)

    [inputs] = drafter.inputs
    assert "健身房每天早上七点开门" in inputs and len(inputs) == 5
    assert all("Ab12_X" not in text for text in inputs)
    [referral] = await _referrals(factory)
    assert referral.status is KnowledgeReferralStatus.DRAFTED
    assert referral.draft["source_quotes"][0]["text"] == "健身房每天早上七点开门"


@pytest.mark.asyncio
async def test_messages_before_referral_or_after_window_are_excluded(factory) -> None:
    """转交之前、48 小时之后的管家消息不计入窗口。"""
    conversation_id = await _referral_with_staff_replies(factory, "健身房每天早上七点开门")
    async with factory() as session:
        referral = await session.get(KnowledgeReferral, 1)
        created = referral.created_at.replace(tzinfo=UTC)
        session.add_all([
            Message(conversation_id=conversation_id, external_message_id="s-late",
                    origin=MessageOrigin.SERVICER, message_type="text", content="太晚了",
                    sent_at=created + timedelta(hours=49)),
            Message(conversation_id=conversation_id, external_message_id="s-old",
                    origin=MessageOrigin.SERVICER, message_type="text", content="早先的",
                    sent_at=created - timedelta(hours=1)),
        ])
        await session.commit()
        detail = await KnowledgeAdminService(session).referral_detail(1)
    assert [item.message.content for item in detail.messages] == ["健身房每天早上七点开门"]


@pytest.mark.parametrize(
    "interleave",
    ["regenerate", "close", "clear"],
)
@pytest.mark.asyncio
async def test_stale_draft_results_are_discarded(factory, interleave) -> None:
    """旧任务在换代、关闭、清空之后写回：成功、没答案、失败都被丢弃。"""
    await _referral_with_staff_replies(factory, "健身房每天早上七点开门")
    async with factory() as session:
        await KnowledgeAdminService(session).start_referral_draft(1, employee_id=None)  # type: ignore[arg-type]
    [old_job] = await _jobs(factory, "knowledge_referral_draft")
    async with factory() as session:
        service = KnowledgeAdminService(session)
        if interleave == "regenerate":
            referral = await session.get(KnowledgeReferral, 1)
            referral.status = KnowledgeReferralStatus.DRAFT_FAILED
            await session.commit()
            await service.start_referral_draft(1, employee_id=None)  # type: ignore[arg-type]
        elif interleave == "close":
            await service.close_referral(1, employee_id=None)  # type: ignore[arg-type]
        else:
            conversation = await session.get(Conversation, 1)
            customer = await session.get(Customer, conversation.customer_id)
            tag = CustomerTag(name=TEST_ACCOUNT_TAG, is_active=True)
            session.add(tag)
            await session.flush()
            session.add(CustomerTagLink(customer_id=customer.id, tag_id=tag.id))
            await session.flush()
            await SQLAlchemyCustomerRepository(session).clear_test_customer_data(
                customer.id, administrator_id=None  # type: ignore[arg-type]
            )
            await session.commit()
    before = (await _referrals(factory))[0]
    drafter = _Drafter(_GOOD_DRAFT)
    async with factory() as session:
        await ReferralDraftJobService(
            referrals=SQLAlchemyKnowledgeReferralRepository(session), drafter=drafter
        ).handle(old_job.payload)
        written = await SQLAlchemyKnowledgeReferralRepository(session).finish_draft(
            1, generation=old_job.payload["generation"],
            status=KnowledgeReferralStatus.DRAFT_FAILED,
        )
        await session.commit()
    after = (await _referrals(factory))[0]
    assert drafter.inputs == []  # 不调用模型
    assert written is False
    assert (after.status, after.generation, after.draft) == (
        before.status, before.generation, before.draft
    )


@pytest.mark.asyncio
async def test_same_generation_second_write_is_rejected(factory) -> None:
    """同代次第二次写回（成功后又来一次失败）被拒：状态已不是整理中。"""
    await _referral_with_staff_replies(factory, "健身房每天早上七点开门")
    await _start_and_run_draft(factory, _Drafter(_GOOD_DRAFT))
    async with factory() as session:
        assert not await SQLAlchemyKnowledgeReferralRepository(session).finish_draft(
            1, generation=1, status=KnowledgeReferralStatus.DRAFT_FAILED
        )
    assert (await _referrals(factory))[0].status is KnowledgeReferralStatus.DRAFTED


@pytest.mark.asyncio
async def test_draft_model_failure_is_left_to_worker_retry(factory) -> None:
    """模型失败交给 Worker 重试，服务内不改状态；没找到答案记为终态。"""
    await _referral_with_staff_replies(factory, "健身房每天早上七点开门")
    with pytest.raises(RetrySafeJobError):
        await _start_and_run_draft(factory, _Drafter(FaqDraftUnavailableError()))
    assert (await _referrals(factory))[0].status is KnowledgeReferralStatus.DRAFTING


def test_staff_draft_quotes_must_come_from_input() -> None:
    """依据片段必须非空且逐条出现在交给模型的管家消息里；凭据与链接拒绝。"""
    from homestay_bot.integrations.deepseek_faq_drafter import DeepSeekFaqDrafter

    messages = ["健身房每天早上七点开门"]
    DeepSeekFaqDrafter.validate_staff_draft(_GOOD_DRAFT, messages)
    for bad in (
        _GOOD_DRAFT.model_copy(update={"source_quotes": []}),
        _GOOD_DRAFT.model_copy(update={"source_quotes": ["  "]}),
        _GOOD_DRAFT.model_copy(update={"source_quotes": ["健身房晚上十点关门"]}),
        _GOOD_DRAFT.model_copy(update={"answer_en": "Gym PIN: 4321"}),
        _GOOD_DRAFT.model_copy(update={"keywords": ["https://x.example"]}),
    ):
        with pytest.raises(FaqDraftUnavailableError):
            DeepSeekFaqDrafter.validate_staff_draft(bad, messages)


async def _drafted(factory) -> None:
    """准备一条已整理的转交。"""
    await _referral_with_staff_replies(factory, "健身房每天早上七点开门")
    await _start_and_run_draft(factory, _Drafter(_GOOD_DRAFT))


def _convert_fields(**overrides: Any) -> dict[str, Any]:
    """入库表单字段（已经过路由的范围映射）。"""
    fields: dict[str, Any] = {
        "scope": "global", "property_id": None, "valid_from": None, "valid_until": None,
        "category": "设施", "question_zh": "健身房几点开门？",
        "answer_zh": "健身房每天早上七点开门。",
        "question_en": "When does the gym open?", "answer_en": "The gym opens at 7 a.m.",
        "keywords": ["健身房"], "trigger_any": None, "trigger_exclude": None,
    }
    fields.update(overrides)
    return fields


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"reviewed": False}, "已核对"),
        ({"generation": 0}, "刷新"),
        ({"fields": {"scope": "unreviewed"}}, "仅此房间"),
        ({"fields": {"answer_en": "Door password: 9988"}}, "密码"),
        ({"fields": {"answer_zh": "【待管理员确认】点开门"}}, "待管理员确认"),
    ],
)
@pytest.mark.asyncio
async def test_convert_rejections_leave_no_knowledge(factory, kwargs, message) -> None:
    """未勾选、过期代次、未选范围、含凭据或占位符都拒绝，且不留下启用知识。"""
    await _drafted(factory)
    async with factory() as session:
        with pytest.raises(KnowledgeFormError, match=message):
            await KnowledgeAdminService(session).convert_referral(
                1, None,  # type: ignore[arg-type]
                generation=kwargs.get("generation", 1),
                reviewed=kwargs.get("reviewed", True),
                **_convert_fields(**kwargs.get("fields", {})),
            )
        await session.rollback()
        assert await session.scalar(select(func.count()).select_from(KnowledgeEntry)) == 0
    assert (await _referrals(factory))[0].status is KnowledgeReferralStatus.DRAFTED


@pytest.mark.asyncio
async def test_convert_creates_enabled_knowledge_and_ends_only_this_referral(factory) -> None:
    """入库建启用知识并只结束这一条；删除知识不被外键拦住，转交不重开。"""
    await _drafted(factory)
    async with factory() as session:
        other = KnowledgeReferral(
            conversation_id=1, source_message_id="g-1", unit_id="9:0-1", plan_item_id=9,
            question=QUESTION, normalized_question="健身房开放时间", property_id=401,
        )
        session.add(other)
        await session.commit()
        entry = await KnowledgeAdminService(session).convert_referral(
            1, None, generation=1, reviewed=True,  # type: ignore[arg-type]
            **_convert_fields(scope="property", property_id=401),
        )
        assert entry.is_enabled and entry.property_id == 401
    referrals = await _referrals(factory)
    assert referrals[0].status is KnowledgeReferralStatus.CONVERTED
    assert referrals[0].knowledge_entry_id == entry.id
    assert referrals[1].status is KnowledgeReferralStatus.PENDING
    async with factory() as session:
        await KnowledgeAdminService(session).delete_entry(entry.id, None)  # type: ignore[arg-type]
    assert (await _referrals(factory))[0].status is KnowledgeReferralStatus.CONVERTED


@pytest.mark.asyncio
async def test_clearing_removes_referral_text_and_keeps_receipt(factory) -> None:
    """清空：转交正文、草稿、问题键清掉，状态已清空、代次加一；收据保留，旧来源重放结束。"""
    conversation_id, message = await _seed(factory, test_account=True)
    await _run_final(factory, _Assistant(_decision()), message)
    async with factory() as session:
        conversation = await session.get(Conversation, conversation_id)
        counts = await SQLAlchemyCustomerRepository(session).clear_test_customer_data(
            conversation.customer_id, administrator_id=None  # type: ignore[arg-type]
        )
        await session.commit()
    assert counts["knowledge_referrals"] == 1
    [referral] = await _referrals(factory)
    assert referral.status is KnowledgeReferralStatus.CLEARED
    assert (referral.question, referral.canonical_question, referral.draft) == (None, None, None)
    assert referral.normalized_question == "" and referral.generation == 1
    async with factory() as session:
        assert await session.get(SourceCompletionReceipt, message.msgid) is not None
    replay = _Assistant(_decision())
    await _run_final(factory, replay, message)
    assert replay.calls == 0
    # 新来源的正常问题仍可创建转交。
    async with factory() as session:
        fresh = _guest("g-fresh")
        await MessageService(SQLAlchemyMessageRepository(session)).record_incoming(
            conversation_id, fresh
        )
        await session.commit()
    await _run_final(factory, _Assistant(_decision()), fresh)
    assert len(await _referrals(factory)) == 2


@pytest.mark.asyncio
async def test_frozen_staff_credentials_are_removed_before_draft_sdk_request(factory) -> None:
    """冻结后变为含凭据的管家消息仍须在真实 SDK 请求边界净化；这里只替换网络方法。"""
    import json
    from types import SimpleNamespace

    from homestay_bot.integrations.deepseek_faq_drafter import DeepSeekFaqDrafter

    await _referral_with_staff_replies(factory, "健身房每天早上七点开门")
    async with factory() as session:
        await KnowledgeAdminService(session).start_referral_draft(1, employee_id=None)  # type: ignore[arg-type]
        referral = await session.get(KnowledgeReferral, 1)
        message = await session.get(Message, referral.source_message_ids[0])
        message.content = 'Wi-Fi password: "A1! SYNTHETIC_SDK_TAIL"\n健身房每天早上七点开门'
        await session.commit()

    requests = []

    async def create(**kwargs):
        """捕获实际序列化输入并返回合成答案，不发起任何外部请求。"""
        requests.append(json.loads(kwargs["messages"][1]["content"]))
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=_GOOD_DRAFT.model_dump_json())
        )])

    drafter = DeepSeekFaqDrafter(
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
        model="synthetic-no-network",
    )
    [job] = await _jobs(factory, "knowledge_referral_draft")
    async with factory() as session:
        await ReferralDraftJobService(
            referrals=SQLAlchemyKnowledgeReferralRepository(session), drafter=drafter
        ).handle(job.payload)
        await session.commit()
    [request] = requests
    assert request["staff_messages"] == ["[credential removed]\n健身房每天早上七点开门"]
    assert "SYNTHETIC_SDK_TAIL" not in json.dumps(request)
    assert (await _referrals(factory))[0].status is KnowledgeReferralStatus.DRAFTED


@pytest.mark.parametrize("field", ["answer_zh", "answer_en", "keywords"])
@pytest.mark.asyncio
async def test_quoted_credential_tail_is_rejected_by_admin_conversion(factory, field) -> None:
    """管理员提交的中英文答案与关键词含引号凭据时拒绝，数据库不留下启用知识。"""
    await _drafted(factory)
    secret = '密码："A1! SYNTHETIC_ADMIN_TAIL"' if field == "answer_zh" else (
        'Wi-Fi password: "A1! SYNTHETIC_ADMIN_TAIL"'
    )
    async with factory() as session:
        with pytest.raises(KnowledgeFormError, match="密码"):
            await KnowledgeAdminService(session).convert_referral(
                1, None, generation=1, reviewed=True,  # type: ignore[arg-type]
                **_convert_fields(**{field: [secret] if field == "keywords" else secret}),
            )
        await session.rollback()
        assert await session.scalar(select(func.count()).select_from(KnowledgeEntry)) == 0
    assert (await _referrals(factory))[0].status is KnowledgeReferralStatus.DRAFTED


# ---- 1.70.4 房号省略追问：真实适配器结果经会话层落库（房号追问 Spec §2.5，R174 §7-4） ----


async def _follow_up_decision(text: str, plan, referral_unit: str | None):
    """真实 `DeepSeekGuestAssistant` 跑一次（合成规划与主模型），模型可主动选中缺口编号。"""
    from tests.unit.test_reply_fix2_items import _assistant, _Knowledge, _respond, _snippet
    from tests.unit.test_reply_fix2_items import _decision as _model_decision

    breakfast = _snippet(8401, "早餐几点开始？", "401早餐8:00开始。", category="早餐",
                         scope="property", property_id=401)
    referral = (
        [{"unit_id": referral_unit, "canonical_question": "302早餐几点", "category": "早餐"}]
        if referral_unit else None
    )
    assistant, _ = _assistant(
        [_model_decision(gap_referrals=referral)],
        _Knowledge([breakfast], {"302": 302, "401": 401}),
    )
    return await _respond(assistant, text, plan)


@pytest.mark.asyncio
async def test_inherited_follow_up_referral_stores_dated_question_and_room(factory) -> None:
    """继承成功、302 真缺资料：保存的问法带日期、房源是 302、单元编号是「302呢」的原文区间。"""
    text = "明天401早餐几点，302呢"
    plan = plan_for(text, ("static_fact", text))
    probe = await _follow_up_decision(text, plan, None)
    [gap] = probe.gap_units
    decision = await _follow_up_decision(text, plan, gap.unit_id)
    _, message = await _seed(factory, content=text)
    async with factory() as session:
        session.add(PropertyProfile(id=302, room_number="302", title="合成302"))
        await session.commit()
    await _run_final(factory, _Assistant(decision), message)
    [referral] = await _referrals(factory)
    assert (referral.question, referral.property_id, referral.unit_id) == (
        "2026-10-08：302早餐几点", 302, "1:10-14"
    )
    assert len(await _jobs(factory, HANDOFF_CARD_JOB_TYPE)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("text,unit", [
    ("302呢", "1:0-4"),
    ("401早餐几点\n302呢", "1:8-12"),
])
async def test_unbound_follow_up_never_creates_referral(factory, text, unit) -> None:
    """D 类（无父句、跨行）模型主动选中编号也不建转交、不发卡。"""
    decision = await _follow_up_decision(text, plan_for(text, ("static_fact", text)), unit)
    assert ("302呢", "scope_unbound") in [(g.text, g.reason) for g in decision.gap_units]
    _, message = await _seed(factory, content=text)
    await _run_final(factory, _Assistant(decision), message)
    assert await _referrals(factory) == []
    assert await _jobs(factory, HANDOFF_CARD_JOB_TYPE) == []
