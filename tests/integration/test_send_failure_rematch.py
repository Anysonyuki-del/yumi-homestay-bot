"""企业微信失败回执比消息入库先到时的补匹配（Spec 2026-11-19 F1、M1–M3、M7）。

只用合成数据和文件型 SQLite（多个会话各自提交，才能复现交错），不调用外部服务。
PostgreSQL 上的列宽与提交交错见 test_send_failure_rematch_postgresql。
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot import application
from homestay_bot.domain.enums import ComplaintReviewStatus, JobStatus, MessageOrigin
from homestay_bot.domain.models import (
    Base,
    ComplaintReview,
    Conversation,
    Customer,
    Job,
    Message,
)
from homestay_bot.repositories.complaints import SQLAlchemyComplaintRepository
from homestay_bot.repositories.jobs import SQLAlchemyJobRepository
from homestay_bot.worker import Worker

REMATCH = application.SEND_FAILURE_REMATCH_JOB_TYPE


class LifecycleStub:
    """生命周期提醒归属：本文件不涉及提醒，始终未找到。"""

    async def handle_send_failure(self, external_message_id: str, fail_type: int) -> bool:
        """返回未找到所属提醒。"""
        return False


class BundleStub:
    """补匹配处理器只读取应用编号与值班名单。"""

    agent_id = 1000002
    duty_userids = ("staff-1",)


async def seed_conversation(session_factory) -> None:
    """放入一位合成客户和一个会话（编号 1）；PostgreSQL 版本复用同一份数据。"""
    async with session_factory() as session:
        customer = Customer(display_name="合成客人")
        session.add(customer)
        await session.flush()
        session.add(Conversation(
            id=1, customer_id=customer.id, open_kfid="wk-1", external_userid="wm-1"
        ))
        await session.commit()


@pytest.fixture
async def factory(tmp_path):
    """文件型 SQLite：多个会话各自提交，才能复现交错。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rematch.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    await seed_conversation(session_factory)
    yield session_factory
    await engine.dispose()


async def _consume(factory, msgid: str, fail_type: int = 10, open_kfid: str = "wk-1") -> None:
    """按同步路径消费一条失败回执并提交，与生产 handle_send_failure 的事务边界一致。"""
    async with factory() as session:
        await application._consume_send_failure(
            session,
            msgid,
            fail_type,
            open_kfid,
            agent_id=BundleStub.agent_id,
            duty_userids=BundleStub.duty_userids,
            lifecycle=LifecycleStub(),  # type: ignore[arg-type]
        )
        await session.commit()


async def _record_bot(factory, msgid: str, message_type: str = "text") -> None:
    """模拟发送事务提交：机器人消息带着平台编号入库。"""
    async with factory() as session:
        session.add(Message(
            conversation_id=1,
            external_message_id=msgid,
            origin=MessageOrigin.BOT,
            message_type=message_type,
            content="[图片]" if message_type == "image" else "停车场在楼下。",
            message_metadata={"delivery_status": "accepted", "delivery_retry_count": 0},
            sent_at=datetime.now(UTC),
        ))
        await session.commit()


async def _run_rematch(factory) -> None:
    """把补匹配作业推进到可领取，用真实 Worker 跑一次。"""
    async with factory() as session:
        await session.execute(
            update(Job)
            .where(Job.job_type == REMATCH, Job.status == JobStatus.PENDING)
            .values(available_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
    async with factory() as session:
        repository = SQLAlchemyJobRepository(session, included_job_types={REMATCH})
        worker = Worker(
            repository=repository,
            handlers={
                REMATCH: application._build_send_failure_rematch_handler(
                    session,
                    BundleStub(),  # type: ignore[arg-type]
                    lifecycle=LifecycleStub(),  # type: ignore[arg-type]
                )
            },
            checkpoint=session.commit,
        )
        await worker.run_once()


async def _jobs(factory, job_type: str) -> list[Job]:
    """按类型列出作业。"""
    async with factory() as session:
        return list(await session.scalars(
            select(Job).where(Job.job_type == job_type).order_by(Job.id)
        ))


async def _message(factory, msgid: str) -> Message:
    """读取最新的机器人消息。"""
    async with factory() as session:
        message = await session.scalar(
            select(Message).where(Message.external_message_id == msgid)
        )
        assert message is not None
        return message


@pytest.mark.asyncio
@pytest.mark.parametrize("message_type", ["text", "image"])
async def test_receipt_before_record_is_rematched(factory, message_type) -> None:
    """回执先到：同步路径登记延后补匹配；消息入库后补匹配按原策略处理。"""
    before = datetime.now(UTC)
    await _consume(factory, "real-1")

    [rematch] = await _jobs(factory, REMATCH)
    assert rematch.payload == {
        "external_message_id": "real-1", "fail_type": 10, "open_kfid": "wk-1"
    }
    assert rematch.available_at.replace(tzinfo=UTC) >= before + timedelta(seconds=59)

    await _record_bot(factory, "real-1", message_type)
    await _run_rematch(factory)

    [rematch] = await _jobs(factory, REMATCH)
    assert rematch.status is JobStatus.COMPLETED
    assert (await _message(factory, "real-1")).message_metadata["delivery_status"] == "failed"
    text_retries = await _jobs(factory, "wecom_send_text")
    staff_alerts = await _jobs(factory, "wecom_send_internal_text")
    if message_type == "text":
        # 普通文字首次失败：排一次去重重试，在途期间不惊动员工。
        assert len(text_retries) == 1 and staff_alerts == []
    else:
        # 图片不按文字重发，只记失败并通知员工。
        assert text_retries == [] and len(staff_alerts) == 1


@pytest.mark.asyncio
async def test_record_before_receipt_needs_no_rematch(factory) -> None:
    """消息先入库：同步路径直接处理，不登记补匹配。"""
    await _record_bot(factory, "real-2")
    await _consume(factory, "real-2")

    assert await _jobs(factory, REMATCH) == []
    assert len(await _jobs(factory, "wecom_send_text")) == 1


@pytest.mark.asyncio
async def test_duplicate_receipt_enqueues_one_rematch_and_one_action(factory) -> None:
    """同一页重放：补匹配只登记一次；补匹配处理后再来同一回执，不产生第二个业务动作。"""
    await _consume(factory, "real-3")
    await _consume(factory, "real-3")
    assert len(await _jobs(factory, REMATCH)) == 1

    await _record_bot(factory, "real-3")
    await _run_rematch(factory)
    await _consume(factory, "real-3")

    assert len(await _jobs(factory, "wecom_send_text")) == 1
    assert await _jobs(factory, "wecom_send_internal_text") == []


@pytest.mark.asyncio
async def test_long_ids_produce_bounded_distinct_dedupe_keys(factory) -> None:
    """平台编号很长时去重键仍在列宽内；同一对编号只登记一次，不同账号互不合并。"""
    msgid = "m" * 128
    await _consume(factory, msgid, open_kfid="k" * 128)
    await _consume(factory, msgid, open_kfid="k" * 128)
    await _consume(factory, msgid, open_kfid="other")

    jobs = await _jobs(factory, REMATCH)
    assert len(jobs) == 2
    assert all(job.dedupe_key and len(job.dedupe_key) <= 128 for job in jobs)
    assert jobs[0].payload["external_message_id"] == msgid


@pytest.mark.asyncio
async def test_unmatched_receipt_ends_failed_with_its_own_error_code(factory) -> None:
    """三次都找不到归属：作业终态 FAILED，错误码说明「无人认领」而不是数据库故障。"""
    await _consume(factory, "nobody")
    for expected in ((JobStatus.PENDING, 1), (JobStatus.PENDING, 2), (JobStatus.FAILED, 3)):
        await _run_rematch(factory)
        [job] = await _jobs(factory, REMATCH)
        assert (job.status, job.attempts) == expected
        assert job.last_error_code == "UnmatchedSendFailure"


@pytest.mark.asyncio
async def test_rematch_storage_error_rolls_back_and_retries(factory, monkeypatch) -> None:
    """补匹配写到一半出错：已写的失败标记一并撤销，作业按存储故障重试，不记成无人认领。"""
    await _consume(factory, "real-4")
    await _record_bot(factory, "real-4", "image")

    async def broken_notify(*args: Any, **kwargs: Any) -> bool:
        """在消息已标记失败之后、登记员工通知时写库失败。"""
        raise OperationalError("insert", {}, Exception("fixture"))

    monkeypatch.setattr(application, "_notify_guest_delivery_failure", broken_notify)
    await _run_rematch(factory)

    [job] = await _jobs(factory, REMATCH)
    assert job.status is JobStatus.PENDING
    assert job.last_error_code == "SendFailureRematchStorageError"
    assert (await _message(factory, "real-4")).message_metadata["delivery_status"] == "accepted"
    assert await _jobs(factory, "wecom_send_internal_text") == []


async def _seed_sent_complaint(factory, msgid: str) -> int:
    """一条已送出的客诉回复，平台编号为 msgid。"""
    async with factory() as session:
        review = ComplaintReview(
            conversation_id=1,
            source_message_id="g-complaint",
            reason="complaint",
            risk_level="high",
            status=ComplaintReviewStatus.SENT,
            draft="我已收到您的反馈。",
            version=3,
        )
        session.add(review)
        await session.commit()
        return review.id


@pytest.mark.asyncio
async def test_complaint_reply_is_never_retried_as_ordinary_text(factory) -> None:
    """客诉回复与机器人消息同时存在：按客诉处理，不排普通重试；重复回执不再改版本。"""
    review_id = await _seed_sent_complaint(factory, "real-5")
    async with factory() as session:
        await session.execute(
            update(ComplaintReview)
            .where(ComplaintReview.id == review_id)
            .values(delivery_external_message_id="real-5")
        )
        await session.commit()
    await _record_bot(factory, "real-5")

    await _consume(factory, "real-5")
    async with factory() as session:
        review = await session.get(ComplaintReview, review_id)
        version = review.version
        assert review.status is ComplaintReviewStatus.DELIVERY_FAILED
    await _consume(factory, "real-5")

    async with factory() as session:
        assert (await session.get(ComplaintReview, review_id)).version == version
    assert await _jobs(factory, "wecom_send_text") == []
    assert await _jobs(factory, REMATCH) == []


@pytest.mark.asyncio
async def test_send_commit_right_after_complaint_lookup_is_not_misread(
    factory, monkeypatch
) -> None:
    """查完客诉后发送事务才提交（客诉归属与机器人消息同事务可见）：不得当普通文字重发。"""
    review_id = await _seed_sent_complaint(factory, "real-6")
    original = SQLAlchemyComplaintRepository.mark_delivery_failed_by_external_message_id
    committed = False

    async def lookup_then_send_commits(self, external_message_id, *, error_code):
        """先按原逻辑查客诉，随后另一会话提交发送事务。"""
        nonlocal committed
        result = await original(self, external_message_id, error_code=error_code)
        if not committed:
            committed = True
            async with factory() as other:
                await other.execute(
                    update(ComplaintReview)
                    .where(ComplaintReview.id == review_id)
                    .values(delivery_external_message_id="real-6")
                )
                other.add(Message(
                    conversation_id=1, external_message_id="real-6",
                    origin=MessageOrigin.BOT, message_type="text", content="我已收到您的反馈。",
                    message_metadata={"delivery_status": "accepted"},
                    sent_at=datetime.now(UTC),
                ))
                await other.commit()
        return result

    monkeypatch.setattr(
        SQLAlchemyComplaintRepository,
        "mark_delivery_failed_by_external_message_id",
        lookup_then_send_commits,
    )
    await _consume(factory, "real-6")
    await _run_rematch(factory)

    assert await _jobs(factory, "wecom_send_text") == []
    async with factory() as session:
        review = await session.get(ComplaintReview, review_id)
        assert review.status is ComplaintReviewStatus.DELIVERY_FAILED
