"""长回复分段链式发送：在真实 worker 循环上验证顺序、中断与失败处理。

每个用例都用 SQLite 文件库和记录调用的企业微信替身驱动 `_run_worker_loop`，
不只测拆出来的函数，确保分段、续发、过时判定与失败回调在真实调度里接得上。
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot import application
from homestay_bot.application import TransactionalOutboxWeCom
from homestay_bot.domain.enums import ConversationMode, JobStatus, Language, MessageOrigin
from homestay_bot.domain.models import Base, Conversation, Job, Message
from homestay_bot.repositories.conversations import (
    SQLAlchemyConversationRepository,
    SQLAlchemyMessageRepository,
)
from homestay_bot.repositories.operations import SQLAlchemyOperationsRepository
from homestay_bot.services.conversation_service import ConversationService
from homestay_bot.services.emergency_service import EmergencyService
from homestay_bot.services.message_service import IncomingMessage, MessageService

PARTS = ["（1/3）第一段。", "（2/3）第二段。", "（3/3）第三段。"]


class StopLoop(RuntimeError):
    """队列清空后 worker 进入休眠时抛出，用来结束测试里的无限循环。"""


class FakeWeCom:
    """记录发出的客人消息；可按正文注入一次失败，并在发送时触发回调。"""

    def __init__(
        self,
        *,
        fail_on: dict[str, Exception] | None = None,
        after_send: Any = None,
    ) -> None:
        """保存注入的失败与发送后回调。"""
        self.sent: list[str] = []
        self.internal: list[str] = []
        self._fail_on = dict(fail_on or {})
        self._after_send = after_send

    async def send_text(self, open_kfid: str, external_userid: str, content: str) -> str:
        """记录发送并返回模拟的企业微信 msgid；命中注入时只失败一次。"""
        error = self._fail_on.pop(content, None)
        if error is not None:
            raise error
        self.sent.append(content)
        if self._after_send is not None:
            await self._after_send(content)
        return f"real-{len(self.sent)}"

    async def send_internal_text(self, *, agent_id, employee_userids, content) -> None:
        """记录员工通知。"""
        self.internal.append(content)


async def _setup(
    tmp_path, *, already_human: bool = False, with_conversation_id: bool = True
) -> tuple[Any, int]:
    """建库、建会话与来源客人消息，并以链式方式登记三段回复。

    `with_conversation_id=False` 按生产装配构造出站：生产的会话服务在会话建立前
    就创建了出站对象，拿不到会话编号。1.39.11 的修复只在测试自行传入编号时生效。
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'chain.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        conversation = Conversation(
            open_kfid="wk-chain",
            external_userid="wm-chain",
            language=Language.ZH,
            mode=ConversationMode.BOT_ACTIVE,
        )
        session.add(conversation)
        await session.flush()
        session.add(
            Message(
                conversation_id=conversation.id,
                external_message_id="guest-1",
                origin=MessageOrigin.GUEST,
                message_type="text",
                content="排一条路线吧",
                sent_at=datetime(2026, 9, 24, 8, tzinfo=UTC),
            )
        )
        await session.flush()
        if already_human:
            await _takeover(session, conversation)
        outbox = TransactionalOutboxWeCom(
            session,
            source_message_id="guest-1",
            delivery_phase="final",
            source_guest_message_id="guest-1",
            conversation_id=conversation.id if with_conversation_id else None,
        )
        await outbox.send_text_chain("wk-chain", "wm-chain", PARTS)
        await session.commit()
        return factory, conversation.id


async def _takeover(session, conversation, *, through_message: bool = False) -> None:
    """调用生产共用的接管入口，真实保存模式与审计，不用 SQL 伪造状态。"""
    service = ConversationService(
        conversations=SQLAlchemyConversationRepository(session),
        messages=MessageService(SQLAlchemyMessageRepository(session)),
        assistant=object(), emergency_service=EmergencyService(),
        wecom=FakeWeCom(), agent_id=1, duty_employee_userids=[],
        audit_events=SQLAlchemyOperationsRepository(session),
    )
    if through_message:
        await service.handle_message(IncomingMessage(
            msgid="request-human", open_kfid=conversation.open_kfid,
            external_userid=conversation.external_userid, origin=MessageOrigin.GUEST,
            msgtype="text", content="转人工", sent_at=datetime.now(UTC),
        ))
    else:
        await service._switch_to_human(conversation, "requested_human")


@pytest.mark.asyncio
@pytest.mark.parametrize("already_human", [False, True])
@pytest.mark.parametrize("with_conversation_id", [True, False])
async def test_new_handoff_without_staff_message_stops_chain(
    tmp_path, monkeypatch, already_human, with_conversation_id
) -> None:
    """发送途中重新交给人工，即使还没有员工消息，也不续发原答案。"""
    factory, conversation_id = await _setup(
        tmp_path,
        already_human=already_human,
        with_conversation_id=with_conversation_id,
    )

    async def takeover_after_first(content: str) -> None:
        """首段发出后走实际接管保存链路。"""
        if content == PARTS[0]:
            async with factory() as session:
                conversation = await session.get(Conversation, conversation_id)
                await _takeover(session, conversation, through_message=not already_human)
                await session.commit()

    client = FakeWeCom(after_send=takeover_after_first)
    await _run_until_idle(factory, client, monkeypatch)
    assert client.sent == [PARTS[0]]


@pytest.mark.asyncio
async def test_old_handoff_and_other_conversation_handoff_do_not_stop_chain(
    tmp_path, monkeypatch
) -> None:
    """保留旧人工模式下独立回答，其他会话的新接管也不能误伤本会话。"""
    factory, _ = await _setup(tmp_path, already_human=True)

    async def other_handoff(content: str) -> None:
        """第一段后为另一会话登记接管。"""
        if content == PARTS[0]:
            async with factory() as session:
                conversation = Conversation(open_kfid="other", external_userid="other")
                session.add(conversation)
                await session.flush()
                await _takeover(session, conversation)
                await session.commit()

    client = FakeWeCom(after_send=other_handoff)
    await _run_until_idle(factory, client, monkeypatch)
    assert client.sent == PARTS


async def _run_until_idle(factory, client: FakeWeCom, monkeypatch) -> None:
    """跑真实 worker 循环，直到队列里没有到期任务。"""

    async def stop(_delay: float) -> None:
        """worker 空闲休眠即表示队列已处理完。"""
        raise StopLoop

    monkeypatch.setattr(application.asyncio, "sleep", stop)
    with pytest.raises(StopLoop):
        await application._run_worker_loop(
            SimpleNamespace(state=SimpleNamespace()),
            factory=factory,
            handler=object(),
            wecom=client,
            recover_stale=False,
        )


async def _add_message(factory, conversation_id: int, origin: MessageOrigin, msgid: str) -> None:
    """用独立会话写入一条新消息，模拟发送途中客人或员工发言。"""
    async with factory() as session:
        session.add(
            Message(
                conversation_id=conversation_id,
                external_message_id=msgid,
                origin=origin,
                message_type="text",
                content="新消息",
                sent_at=datetime(2026, 9, 24, 8, 1, tzinfo=UTC),
            )
        )
        await session.commit()


async def _guest_send_jobs(factory) -> list[Job]:
    """按编号返回全部客人消息发送任务。"""
    async with factory() as session:
        return list(
            await session.scalars(
                select(Job).where(Job.job_type == "wecom_send_text").order_by(Job.id)
            )
        )


@pytest.mark.asyncio
async def test_only_the_first_part_is_queued_up_front(tmp_path) -> None:
    """登记时只入队第一段：同一条回复任何时刻只有一段在队列里。"""
    factory, _ = await _setup(tmp_path)

    jobs = await _guest_send_jobs(factory)

    assert len(jobs) == 1
    assert jobs[0].payload["content"] == PARTS[0]
    assert jobs[0].payload["reply_chain"]["index"] == 1
    assert jobs[0].payload["reply_chain"]["total"] == 3


@pytest.mark.asyncio
async def test_parts_are_sent_in_order_and_each_is_recorded(tmp_path, monkeypatch) -> None:
    """三段按顺序发出，每段是一条独立的机器人消息并记录段序号。"""
    factory, conversation_id = await _setup(tmp_path)
    client = FakeWeCom()

    await _run_until_idle(factory, client, monkeypatch)

    assert client.sent == PARTS
    async with factory() as session:
        bots = list(
            await session.scalars(
                select(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.origin == MessageOrigin.BOT,
                )
                .order_by(Message.id)
            )
        )
    assert [bot.content for bot in bots] == PARTS
    assert [bot.message_metadata["reply_part"]["index"] for bot in bots] == [1, 2, 3]


@pytest.mark.asyncio
async def test_a_retried_part_is_never_overtaken_by_the_next(tmp_path, monkeypatch) -> None:
    """第一段连接失败待重试时，第二段不会先发；重试成功后三段仍按顺序送达。"""
    factory, _ = await _setup(tmp_path)
    client = FakeWeCom(fail_on={PARTS[0]: httpx.ConnectError("offline")})

    await _run_until_idle(factory, client, monkeypatch)

    assert client.sent == []
    assert len(await _guest_send_jobs(factory)) == 1

    async with factory() as session:
        await session.execute(
            update(Job).values(available_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
    await _run_until_idle(factory, client, monkeypatch)

    assert client.sent == PARTS


@pytest.mark.asyncio
async def test_a_new_guest_message_does_not_cut_the_reply_short(tmp_path, monkeypatch) -> None:
    """发到一半客人又发了消息：剩余段照发，避免答案只发一半。"""
    factory, conversation_id = await _setup(tmp_path)

    async def guest_writes(content: str) -> None:
        """第一段送达后客人追问。"""
        if content == PARTS[0]:
            await _add_message(factory, conversation_id, MessageOrigin.GUEST, "guest-2")

    client = FakeWeCom(after_send=guest_writes)
    await _run_until_idle(factory, client, monkeypatch)

    assert client.sent == PARTS


@pytest.mark.asyncio
async def test_a_staff_reply_stops_the_remaining_parts(tmp_path, monkeypatch) -> None:
    """发到一半员工发言：剩余段停止，不插话打断人工。"""
    factory, conversation_id = await _setup(tmp_path)

    async def staff_writes(content: str) -> None:
        """第一段送达后员工接手回复。"""
        if content == PARTS[0]:
            await _add_message(factory, conversation_id, MessageOrigin.SERVICER, "staff-1")

    client = FakeWeCom(after_send=staff_writes)
    await _run_until_idle(factory, client, monkeypatch)

    assert client.sent == [PARTS[0]]
    assert len(await _guest_send_jobs(factory)) == 2  # 第二段被判过时后不再续发第三段


@pytest.mark.asyncio
@pytest.mark.parametrize("with_conversation_id", [True, False])
async def test_a_failed_part_stops_the_chain_and_alerts_staff(
    tmp_path, monkeypatch, with_conversation_id
) -> None:
    """第二段终态失败：第三段不发，并登记注明段号的员工通知任务。"""
    factory, conversation_id = await _setup(
        tmp_path, with_conversation_id=with_conversation_id
    )
    client = FakeWeCom(fail_on={PARTS[1]: RuntimeError("rejected")})

    await _run_until_idle(factory, client, monkeypatch)

    assert client.sent == [PARTS[0]]
    async with factory() as session:
        jobs = list(await session.scalars(select(Job).order_by(Job.id)))
    failed = [
        job
        for job in jobs
        if job.job_type == "wecom_send_text" and job.status is JobStatus.FAILED
    ]
    alerts = [job for job in jobs if job.job_type == "guest_reply_chain_undelivered"]
    # 终态失败的任务载荷按既有隐私规则清空，不留客人正文。
    assert len(failed) == 1 and failed[0].payload == {}
    assert len(alerts) == 1
    assert alerts[0].payload["index"] == 2
    assert alerts[0].payload["total"] == 3
    assert alerts[0].payload["conversation_id"] == conversation_id
    assert set(alerts[0].payload) == {"group", "index", "total", "conversation_id"}


@pytest.mark.asyncio
async def test_undelivered_alert_names_the_part(tmp_path) -> None:
    """员工通知写明从第几段起未送达，且只登记一次。"""
    factory, _ = await _setup(tmp_path)
    async with factory() as session:
        payload = {"group": "g-1", "index": 2, "total": 3, "conversation_id": 1}
        assert await application._notify_undelivered_reply_chain(
            session, payload, agent_id=1000002, employee_userids=["staff-1"]
        )
        await session.commit()
        jobs = list(
            await session.scalars(select(Job).where(Job.job_type == "wecom_send_internal_text"))
        )
    assert len(jobs) == 1
    assert "从第 2 段起未成功送达" in jobs[0].payload["content"]
    assert "会话编号：1" in jobs[0].payload["content"]


def test_failure_payload_only_matches_reply_chains() -> None:
    """只有分段回复的发送失败才触发这类通知，普通回复与其他任务不受影响。"""
    chain_payload = {"open_kfid": "wk", "reply_chain": {"group": "g", "index": 2, "total": 3}}

    assert application.reply_chain_undelivered_payload("wecom_send_text", chain_payload) == {
        "group": "g",
        "index": 2,
        "total": 3,
    }
    plain = {"open_kfid": "wk"}
    assert application.reply_chain_undelivered_payload("wecom_send_text", plain) is None
    assert (
        application.reply_chain_undelivered_payload("guest_delivery_rewrite", chain_payload)
        is None
    )


@pytest.mark.asyncio
async def test_async_failure_of_a_part_resends_only_that_part(tmp_path, monkeypatch) -> None:
    """某段送达后被异步判失败：只重发这一段，不会再次触发后续段的续发。"""
    factory, _ = await _setup(tmp_path)
    client = FakeWeCom()
    await _run_until_idle(factory, client, monkeypatch)
    assert client.sent == PARTS

    async with factory() as session:
        handled = await application._handle_guest_delivery_failure(
            session, "real-1", fail_type=1
        )
        await session.commit()
    assert handled is True

    await _run_until_idle(factory, client, monkeypatch)

    # 第一段重发一次（带原序号，客人能对上顺序），第二、三段不会被再发一遍。
    assert client.sent == [*PARTS, PARTS[0]]
    # 三段原始发送加一次重发；已完成任务的载荷按隐私规则清空，只核对任务数。
    assert len(await _guest_send_jobs(factory)) == 4


async def _handle_with_production_outbox(factory, msgid: str, content: str) -> None:
    """按生产装配处理一条客人消息：出站对象不带会话编号，业务与入队同一事务提交。"""
    async with factory() as session:
        service = ConversationService(
            conversations=SQLAlchemyConversationRepository(session),
            messages=MessageService(SQLAlchemyMessageRepository(session)),
            assistant=object(), emergency_service=EmergencyService(),
            wecom=TransactionalOutboxWeCom(
                session,
                source_message_id=msgid,
                source_guest_message_id=msgid,
            ),
            agent_id=1, duty_employee_userids=[],
            audit_events=SQLAlchemyOperationsRepository(session),
        )
        await service.handle_message(IncomingMessage(
            msgid=msgid, open_kfid="wk-chain", external_userid="wm-chain",
            origin=MessageOrigin.GUEST, msgtype="text", content=content,
            sent_at=datetime.now(UTC),
        ))
        await session.commit()


@pytest.mark.asyncio
async def test_emergency_safety_reply_is_sent_even_if_the_guest_writes_again(
    tmp_path, monkeypatch
) -> None:
    """客人连发「燃气味好重」「我们现在该怎么办」：排队中的撤离提示仍须送达。

    以前出站前的过时判定对所有回复一视同仁，第二条消息入库后第一条的安全提示
    被整条跳过，而第二条又不命中紧急词表。
    """
    factory, conversation_id = await _setup(tmp_path)
    async with factory() as session:
        await session.execute(update(Job).values(status=JobStatus.COMPLETED))
        await session.commit()
    await _handle_with_production_outbox(factory, "guest-gas", "房间里燃气味好重")
    await _add_message(factory, conversation_id, MessageOrigin.GUEST, "guest-what-now")
    client = FakeWeCom()

    await _run_until_idle(factory, client, monkeypatch)

    # 三段旧回复已标记完成，队列里唯一的客人消息就是这条安全提示。
    assert len(client.sent) == 1
    assert "开窗通风并离开房间" in client.sent[0]


@pytest.mark.asyncio
async def test_an_ordinary_reply_is_still_skipped_when_the_guest_writes_again(
    tmp_path, monkeypatch
) -> None:
    """对照：普通回复排队期间客人又发了消息，仍按过时跳过，由新消息重新生成答案。"""
    factory, conversation_id = await _setup(tmp_path)
    await _add_message(factory, conversation_id, MessageOrigin.GUEST, "guest-2")
    client = FakeWeCom()

    await _run_until_idle(factory, client, monkeypatch)

    assert client.sent == []
