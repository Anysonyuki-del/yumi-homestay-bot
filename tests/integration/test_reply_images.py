"""知识配图与欢迎图片（Spec 2026-09-29 G1–G3）：在真实会话服务、事务 outbox 和
worker 循环上验证挑图、顺序、条数上限、过时与失败处理。只用合成数据，不调用外部服务。
"""

import io
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot import application
from homestay_bot.application import (
    GUEST_IMAGE_JOB_TYPE,
    RuntimeWorkerBindings,
    TransactionalOutboxWeCom,
)
from homestay_bot.domain.enums import JobStatus, Language, MessageOrigin
from homestay_bot.domain.models import (
    Base,
    Conversation,
    Customer,
    Job,
    KnowledgeEntry,
    KnowledgeImage,
    Message,
    PropertyProfile,
    StayOrder,
)
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
from homestay_bot.services.private_file_storage import PrivateFileStorage
from homestay_bot.services.reply_plan import ReplyEvidence, ReplyPart
from tests.unit.test_conversation_service import AssistantStub

# 最小合法 PNG：签名、IHDR 与 IEND 结构齐全，能通过私有存储的真实格式校验。
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 17 + b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


class StopLoop(RuntimeError):
    """队列清空后 worker 进入休眠时抛出，用来结束测试里的无限循环。"""


class FakeWeCom:
    """按发送顺序记录文字和图片；可按内容注入失败，并在发送后触发回调。"""

    def __init__(self, *, fail_on: dict[str, Exception] | None = None, after_send=None):
        """保存注入的失败与回调；`uploads` 记录上传的原始字节。"""
        self.events: list[tuple[str, str]] = []
        self.uploads: list[bytes] = []
        self._media: dict[str, bytes] = {}
        self._fail_on = dict(fail_on or {})
        self._after_send = after_send

    async def _sent(self, kind: str, value: str) -> str:
        """统一登记发送结果并返回模拟 msgid。"""
        error = self._fail_on.pop(value, None)
        if error is not None:
            raise error
        self.events.append((kind, value))
        if self._after_send is not None:
            await self._after_send(kind, value)
        return f"real-{len(self.events)}"

    async def send_text(self, open_kfid: str, external_userid: str, content: str) -> str:
        """记录文字。"""
        return await self._sent("text", content)

    async def upload_temporary_image(self, content: bytes, *, content_type: str) -> str:
        """记录上传并返回以序号区分的临时素材编号。"""
        self.uploads.append(content)
        media_id = f"media-{len(self.uploads)}"
        self._media[media_id] = content
        return media_id

    async def send_image(self, open_kfid: str, external_userid: str, media_id: str) -> str:
        """记录图片；按上传内容里的标记回查是哪张图，便于断言顺序。"""
        return await self._sent("image", self._media[media_id][16:20].decode())

    async def send_internal_text(self, *, agent_id, employee_userids, content) -> None:
        """员工通知不在本文件断言。"""


def _png(tag: str) -> bytes:
    """带可辨认标记的合法 PNG：标记写在 IHDR 数据开头（第 16–20 字节），不影响校验。"""
    assert len(tag) == 4
    return PNG.replace(b"\x00" * 17, tag.encode() + b"\x00" * 13)


class OneClientRegistry:
    """最小运行时注册表：每轮都借出同一组客户端，走生产的运行时 handler 装配。"""

    def __init__(self, bundle: Any) -> None:
        """保存要借出的客户端组合。"""
        self._bundle = bundle

    def acquire(self) -> Any:
        """返回异步上下文，进入时交出客户端组合。"""
        bundle = self._bundle

        class Lease:
            async def __aenter__(self) -> Any:
                return bundle

            async def __aexit__(self, *_: object) -> None:
                return None

        return Lease()


async def _run_until_idle(factory, client: FakeWeCom, storage, monkeypatch) -> None:
    """跑真实 worker 循环（含运行时注册的配图 handler），直到队列里没有到期任务。"""

    async def stop(_delay: float) -> None:
        raise StopLoop

    async def bindings(session, bundle) -> RuntimeWorkerBindings:
        """与生产 build_worker_bindings 相同的配图 handler 装配。"""
        return RuntimeWorkerBindings(
            sync_handler=object(),  # type: ignore[arg-type]
            wecom=bundle,
            handlers={
                GUEST_IMAGE_JOB_TYPE: application._build_guest_image_handler(
                    session, bundle, storage
                )
            },
        )

    monkeypatch.setattr(application.asyncio, "sleep", stop)
    with pytest.raises(StopLoop):
        await application._run_worker_loop(
            SimpleNamespace(state=SimpleNamespace()),
            factory=factory,
            registry=OneClientRegistry(client),  # type: ignore[arg-type]
            runtime_handler_factory=bindings,
            recover_stale=False,
        )


async def _world(tmp_path, *, welcome_image: bool = True, images_per_entry: int = 2):
    """合成客人、唯一当前订单、带配图的房间知识，以及私有存储里的图片文件。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'images.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    storage = PrivateFileStorage(tmp_path / "private")
    welcome = await storage.save_image(io.BytesIO(_png("WELC")), "image/png", 1 << 20)
    entry_files = [
        await storage.save_image(io.BytesIO(_png(f"PK{n:02d}")), "image/png", 1 << 20)
        for n in range(1, images_per_entry + 1)
    ]
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    async with factory() as session:
        session.add_all([
            Customer(id=1, display_name="合成客人"),
            PropertyProfile(
                id=1,
                title="合成201房",
                welcome_image_file_id=welcome.file_id if welcome_image else None,
            ),
        ])
        await session.flush()
        session.add_all([
            Conversation(
                id=1, customer_id=1, open_kfid="test-kf", external_userid="test-guest",
                language=Language.ZH,
            ),
            StayOrder(
                customer_id=1, property_id=1, hostex_reservation_code="synthetic-0",
                stay_code="synthetic-0", check_in_date=today,
                check_out_date=today + timedelta(days=2), status="confirmed",
            ),
            KnowledgeEntry(
                id=7, scope="property", property_id=1, category="停车",
                question_zh="开车停哪里", answer_zh="停地下一层 A 区。",
                question_en="Parking?", answer_en="Level B1, zone A.", keywords=["停车"],
            ),
        ])
        await session.flush()
        # 故意倒着插入，验证按 sort_order 而不是插入顺序发送。
        for order, stored in reversed(list(enumerate(entry_files, start=1))):
            session.add(KnowledgeImage(
                knowledge_entry_id=7, file_id=stored.file_id, content_type="image/png",
                size=stored.size, sort_order=order,
            ))
        await session.commit()
    return engine, factory, storage


def _grounded_parking(entry_id: int = 7) -> AssistantDecision:
    """证据计划采用审核知识原文作固定回答时的决策形态。"""
    return AssistantDecision(
        reply_text="停地下一层 A 区。",
        language=Language.ZH,
        intent="parking",
        confidence=1,
        reply_parts=[
            ReplyPart(
                question="开车停哪里",
                status="grounded",
                text="停地下一层 A 区。",
                evidence=(
                    ReplyEvidence(
                        source_kind="knowledge",
                        source_id=str(entry_id),
                        property_id=1,
                        fetched_at=datetime.now(UTC),
                    ),
                ),
            )
        ],
    )


async def _receive(factory, decision: AssistantDecision, msgid: str, text: str) -> None:
    """按生产装配处理一条客人消息：事务 outbox、真实仓储，业务与入队同一事务提交。"""
    async with factory() as session:
        service = ConversationService(
            conversations=SQLAlchemyConversationRepository(session),
            messages=MessageService(SQLAlchemyMessageRepository(session)),
            customer_context=SQLAlchemyContextRepository(session),
            assistant=AssistantStub(decision=decision),
            emergency_service=EmergencyService(),
            wecom=TransactionalOutboxWeCom(
                session, source_message_id=msgid, source_guest_message_id=msgid
            ),
            agent_id=1,
            duty_employee_userids=["test-staff"],
            audit_events=SQLAlchemyOperationsRepository(session),
            savepoint_factory=session.begin_nested,
        )
        await service.handle_message(IncomingMessage(
            msgid=msgid, open_kfid="test-kf", external_userid="test-guest",
            origin=MessageOrigin.GUEST, msgtype="text", content=text,
            sent_at=datetime.now(UTC),
        ))
        await session.commit()


async def _bot_messages(factory) -> list[Message]:
    """按顺序返回机器人消息。"""
    async with factory() as session:
        return list(await session.scalars(
            select(Message).where(Message.origin == MessageOrigin.BOT).order_by(Message.id)
        ))


@pytest.mark.asyncio
async def test_fixed_answer_sends_text_then_welcome_image_then_entry_images(
    tmp_path, monkeypatch
) -> None:
    """首轮：欢迎加答案的文字先发，再发欢迎图、条目配图（按顺序）；次轮不再发欢迎图。"""
    engine, factory, storage = await _world(tmp_path)
    client = FakeWeCom()

    await _receive(factory, _grounded_parking(), "g-1", "开车停哪里")
    await _run_until_idle(factory, client, storage, monkeypatch)

    kinds = [kind for kind, _ in client.events]
    assert kinds == ["text", "image", "image", "image"]
    assert client.events[0][1].startswith("欢迎入住合成201房！")
    assert [value[:4] for _, value in client.events[1:]] == ["WELC", "PK01", "PK02"]
    bots = await _bot_messages(factory)
    assert [bot.message_type for bot in bots] == ["text", "image", "image", "image"]
    assert all(bot.content == "[图片]" for bot in bots[1:])
    assert [bot.message_metadata["reply_part"]["index"] for bot in bots] == [1, 2, 3, 4]
    assert {bot.message_metadata["reply_part"]["total"] for bot in bots} == {4}

    await _receive(factory, _grounded_parking(), "g-2", "停车要钱吗")
    await _run_until_idle(factory, client, storage, monkeypatch)
    # 欢迎消息同一张订单只发一次，欢迎图跟着只发一次。
    assert [value[:4] for kind, value in client.events[4:] if kind == "image"] == [
        "PK01", "PK02"
    ]
    await engine.dispose()


@pytest.mark.asyncio
async def test_model_free_answer_and_property_card_never_carry_images(
    tmp_path, monkeypatch
) -> None:
    """模型自由作答（没有知识证据）与房源卡片（负数来源编号）都不附条目配图。"""
    engine, factory, storage = await _world(tmp_path, welcome_image=False)
    client = FakeWeCom()
    free = AssistantDecision(
        reply_text="附近有停车场。", language=Language.ZH, intent="parking", confidence=1
    )
    card = _grounded_parking(entry_id=-1)

    await _receive(factory, free, "g-1", "开车停哪里")
    await _run_until_idle(factory, client, storage, monkeypatch)
    await _receive(factory, card, "g-2", "停车在哪")
    await _run_until_idle(factory, client, storage, monkeypatch)

    assert [kind for kind, _ in client.events] == ["text", "text"]
    assert client.uploads == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_disabled_entry_does_not_send_its_images(tmp_path, monkeypatch) -> None:
    """条目停用后即使证据仍指向它，也不再附图。"""
    engine, factory, storage = await _world(tmp_path, welcome_image=False)
    async with factory() as session:
        (await session.get(KnowledgeEntry, 7)).is_enabled = False
        await session.commit()
    client = FakeWeCom()

    await _receive(factory, _grounded_parking(), "g-1", "开车停哪里")
    await _run_until_idle(factory, client, storage, monkeypatch)

    assert [kind for kind, _ in client.events] == ["text"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_text_parts_and_images_stay_within_five_messages(
    tmp_path, monkeypatch, caplog
) -> None:
    """文字分成多段时，图片只用剩下的名额：总条数不超过 5，多出的图不发，只记日志。"""
    engine, factory, storage = await _world(tmp_path, welcome_image=True, images_per_entry=3)
    # 两段各约 1200 字节的审核原文，加上欢迎语拆成多条；欢迎图加 3 张配图共 4 张图。
    long_answer = "\n\n".join(["停车说明" + "很长的一段。" * 65] * 2)
    decision = _grounded_parking()
    decision.reply_parts[0].text = long_answer
    decision.reply_text = long_answer
    client = FakeWeCom()

    with caplog.at_level(logging.INFO):
        await _receive(factory, decision, "g-1", "开车停哪里")
    await _run_until_idle(factory, client, storage, monkeypatch)

    kinds = [kind for kind, _ in client.events]
    text_count = kinds.count("text")
    assert text_count >= 2
    assert len(kinds) == 5
    assert kinds == ["text"] * text_count + ["image"] * (5 - text_count)
    assert f"dropped={4 - (5 - text_count)}" in caplog.text
    await engine.dispose()


@pytest.mark.asyncio
async def test_staff_reply_after_text_stops_images_but_guest_followup_does_not(
    tmp_path, monkeypatch
) -> None:
    """文字发出后员工发言：剩余图片停发；换成客人追问则照发，与文字续发段同一口径。"""
    for origin, expected_images in ((MessageOrigin.SERVICER, 0), (MessageOrigin.GUEST, 2)):
        run_dir = tmp_path / origin.value
        run_dir.mkdir()
        engine, factory, storage = await _world(run_dir, welcome_image=False)

        async def someone_writes(kind: str, _value: str, *, origin=origin, factory=factory):
            """第一条文字送达后插入一条新消息。"""
            if kind == "text":
                async with factory() as session:
                    session.add(Message(
                        conversation_id=1, external_message_id=f"new-{origin.value}",
                        origin=origin, message_type="text", content="新消息",
                        sent_at=datetime.now(UTC),
                    ))
                    await session.commit()

        client = FakeWeCom(after_send=someone_writes)
        await _receive(factory, _grounded_parking(), "g-1", "开车停哪里")
        await _run_until_idle(factory, client, storage, monkeypatch)

        assert [kind for kind, _ in client.events].count("image") == expected_images
        await engine.dispose()


@pytest.mark.asyncio
async def test_new_guest_message_before_text_is_sent_drops_text_and_images(
    tmp_path, monkeypatch
) -> None:
    """排队期间客人又发了消息：文字判过时不发，图片随之一起放弃。"""
    engine, factory, storage = await _world(tmp_path, welcome_image=False)
    await _receive(factory, _grounded_parking(), "g-1", "开车停哪里")
    async with factory() as session:
        session.add(Message(
            conversation_id=1, external_message_id="g-2", origin=MessageOrigin.GUEST,
            message_type="text", content="算了", sent_at=datetime.now(UTC),
        ))
        await session.commit()
    client = FakeWeCom()

    await _run_until_idle(factory, client, storage, monkeypatch)

    assert client.events == []
    assert client.uploads == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_deleted_image_file_is_skipped_and_the_rest_still_sent(
    tmp_path, monkeypatch
) -> None:
    """排队期间管理员删了第一张图的文件：跳过它，第二张照发。"""
    engine, factory, storage = await _world(tmp_path, welcome_image=False)
    async with factory() as session:
        first = await session.scalar(
            select(KnowledgeImage).where(KnowledgeImage.sort_order == 1)
        )
    await _receive(factory, _grounded_parking(), "g-1", "开车停哪里")
    storage.delete(first.file_id)
    client = FakeWeCom()

    await _run_until_idle(factory, client, storage, monkeypatch)

    assert [value[:4] for kind, value in client.events if kind == "image"] == ["PK02"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_failed_image_stops_the_rest_and_alerts_staff(tmp_path, monkeypatch) -> None:
    """一张图终态失败：后面的图不发，登记注明段号的员工通知；连接失败则可重试。"""
    engine, factory, storage = await _world(tmp_path, welcome_image=False)
    client = FakeWeCom(fail_on={"PK01": RuntimeError("rejected")})

    await _receive(factory, _grounded_parking(), "g-1", "开车停哪里")
    await _run_until_idle(factory, client, storage, monkeypatch)

    assert [kind for kind, _ in client.events] == ["text"]
    async with factory() as session:
        jobs = list(await session.scalars(select(Job).order_by(Job.id)))
    failed = [
        j for j in jobs if j.job_type == GUEST_IMAGE_JOB_TYPE and j.status is JobStatus.FAILED
    ]
    alerts = [j for j in jobs if j.job_type == "guest_reply_chain_undelivered"]
    assert len(failed) == 1
    assert len(alerts) == 1 and alerts[0].payload["index"] == 2
    assert alerts[0].payload["total"] == 3
    await engine.dispose()


@pytest.mark.asyncio
async def test_connect_error_on_image_is_retried_not_failed(tmp_path, monkeypatch) -> None:
    """连接失败的图片任务保留重试，不算终态失败。"""
    engine, factory, storage = await _world(tmp_path, welcome_image=False)
    client = FakeWeCom(fail_on={"PK01": httpx.ConnectError("offline")})

    await _receive(factory, _grounded_parking(), "g-1", "开车停哪里")
    await _run_until_idle(factory, client, storage, monkeypatch)

    async with factory() as session:
        image_jobs = list(await session.scalars(
            select(Job).where(Job.job_type == GUEST_IMAGE_JOB_TYPE)
        ))
    assert len(image_jobs) == 1
    assert image_jobs[0].status is JobStatus.PENDING
    await engine.dispose()


@pytest.mark.asyncio
async def test_async_failure_of_an_image_is_never_resent_as_text(tmp_path, monkeypatch) -> None:
    """图片被异步判失败：不按文字重发「[图片]」，交给员工通知。"""
    engine, factory, storage = await _world(tmp_path, welcome_image=False)
    client = FakeWeCom()
    await _receive(factory, _grounded_parking(), "g-1", "开车停哪里")
    await _run_until_idle(factory, client, storage, monkeypatch)
    image_message = (await _bot_messages(factory))[1]
    assert image_message.message_type == "image"

    async with factory() as session:
        queued = await application._handle_guest_delivery_failure(
            session, image_message.external_message_id, fail_type=1
        )
        await session.commit()
        text_jobs = list(await session.scalars(
            select(Job).where(Job.job_type == "wecom_send_text")
        ))

    assert queued is False
    assert len(text_jobs) == 1  # 只有原来那条文字
    await engine.dispose()
