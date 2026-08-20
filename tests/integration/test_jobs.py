import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.application import TransactionalOutboxWeCom
from homestay_bot.domain.enums import ComplaintReviewStatus, JobStatus
from homestay_bot.domain.models import (
    AuditLog,
    Base,
    ComplaintReview,
    Conversation,
    Customer,
    Job,
)
from homestay_bot.repositories.jobs import SQLAlchemyJobRepository
from homestay_bot.worker import Worker


@pytest.mark.asyncio
async def test_job_status_lookup_uses_dedupe_key_without_payload() -> None:
    """最终回复可按 outbox 幂等键确认安抚状态，且无需读取敏感载荷。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        repository = SQLAlchemyJobRepository(session)
        job = await repository.enqueue(
            "wecom_send_text",
            {"content": "不应依赖这段正文"},
            dedupe_key="outbox:fast-ack",
        )

        assert (
            await repository.status_for_dedupe_key("outbox:fast-ack")
            is JobStatus.PENDING
        )
        await repository.mark_completed(job)
        assert job.payload == {}
        assert (
            await repository.status_for_dedupe_key("outbox:fast-ack")
            is JobStatus.COMPLETED
        )
        assert await repository.status_for_dedupe_key("outbox:missing") is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_stale_running_job_is_recovered_and_claimed_after_restart() -> None:
    """进程中断遗留的超时 RUNNING 任务应恢复为可领取状态。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)

    async with factory() as session:
        stale = Job(
            job_type="wecom_sync",
            payload={"token": "sync", "open_kfid": "wk-1"},
            status=JobStatus.RUNNING,
            attempts=1,
            available_at=now - timedelta(minutes=10),
            locked_at=now - timedelta(minutes=10),
        )
        session.add(stale)
        await session.commit()

        repository = SQLAlchemyJobRepository(session)
        recovered = await repository.recover_stale(
            before=now - timedelta(minutes=5)
        )
        claimed = await repository.claim_next(now=now)

        assert recovered == 1
        assert claimed is not None
        assert claimed.id == stale.id
        assert claimed.status is JobStatus.RUNNING
        assert claimed.attempts == 2
        await repository.release_claim_lock()

    await engine.dispose()


@pytest.mark.asyncio
async def test_failed_read_job_retries_but_booking_write_does_not() -> None:
    """只读任务可有限重试，百居易创建订单任务绝不能自动重放。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)

    async with factory() as session:
        repository = SQLAlchemyJobRepository(session)
        read_job = await repository.enqueue(
            "wecom_sync", {"token": "sync"}, available_at=now
        )
        write_job = await repository.enqueue(
            "hostex_create_reservation", {"approval_id": 1}, available_at=now
        )

        await repository.mark_failed(
            read_job, error_code="timeout", retry_allowed=True, max_attempts=3
        )
        await repository.mark_failed(
            write_job, error_code="timeout", retry_allowed=False, max_attempts=1
        )

        assert read_job.status is JobStatus.PENDING
        assert write_job.status is JobStatus.FAILED

    await engine.dispose()


@pytest.mark.asyncio
async def test_long_lived_retry_delay_is_capped_at_one_hour() -> None:
    """长期等待管理员的任务不得因指数退避溢出或沉睡过久。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        repository = SQLAlchemyJobRepository(session)
        job = await repository.enqueue("faq_draft_generate", {"candidate_id": 7})
        job.attempts = 100
        before = datetime.now(UTC)

        await repository.mark_failed(
            job,
            error_code="DeferredRetryJobError",
            retry_allowed=True,
            max_attempts=10_000,
        )

        assert job.status is JobStatus.PENDING
        assert job.available_at <= before + timedelta(hours=1, seconds=1)

    await engine.dispose()


@pytest.mark.asyncio
async def test_stale_external_send_is_not_replayed() -> None:
    """发送结果不明确的企业微信任务应转失败，禁止恢复后重复发送。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)

    async with factory() as session:
        job = Job(
            job_type="wecom_send_text",
            payload={"content": "您好"},
            status=JobStatus.RUNNING,
            attempts=1,
            available_at=now - timedelta(minutes=10),
            locked_at=now - timedelta(minutes=10),
        )
        session.add(job)
        await session.commit()

        await SQLAlchemyJobRepository(session).recover_stale(
            before=now - timedelta(minutes=5)
        )
        await session.refresh(job)

        assert job.status is JobStatus.FAILED
        assert job.last_error_code == "stale_non_replayable"

    await engine.dispose()


@pytest.mark.asyncio
async def test_stale_delivery_rewrite_is_requeued_for_local_fallback() -> None:
    """遗留改写任务应恢复执行，由已持久化 started 标记阻止第二次模型调用。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)

    async with factory() as session:
        job = Job(
            job_type="guest_delivery_rewrite",
            payload={"message_id": 11},
            status=JobStatus.RUNNING,
            attempts=1,
            available_at=now - timedelta(minutes=10),
            locked_at=now - timedelta(minutes=10),
        )
        session.add(job)
        await session.commit()

        await SQLAlchemyJobRepository(session).recover_stale(
            before=now - timedelta(minutes=5)
        )
        await session.refresh(job)

        assert job.status is JobStatus.PENDING
        assert job.payload == {"message_id": 11}

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("job_type", "payload"),
    [
        ("guest_delivery_rewrite", {"message_id": 11}),
        (
            "wecom_send_text",
            {
                "content": "二次回复",
                "retry_of_message_id": "11",
            },
        ),
    ],
)
async def test_stale_terminal_delivery_job_enqueues_compensation(
    job_type: str,
    payload: dict[str, object],
) -> None:
    """改写重试耗尽或二次发送遗留时应登记去重人工补偿。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)

    async with factory() as session:
        job = Job(
            job_type=job_type,
            payload=payload,
            status=JobStatus.RUNNING,
            attempts=3,
            available_at=now - timedelta(minutes=10),
            locked_at=now - timedelta(minutes=10),
        )
        session.add(job)
        await session.commit()

        await SQLAlchemyJobRepository(session).recover_stale(
            before=now - timedelta(minutes=5)
        )
        jobs = list((await session.scalars(select(Job).order_by(Job.id))).all())

        assert jobs[0].status is JobStatus.FAILED
        assert jobs[1].job_type == "guest_delivery_failure_compensate"
        assert jobs[1].payload == {"message_id": 11}
        assert jobs[1].dedupe_key == "delivery-compensate:11"

    await engine.dispose()


@pytest.mark.asyncio
async def test_external_send_commit_failure_becomes_uncertain_without_replay(
    tmp_path,
) -> None:
    """外部发送后最终提交失败必须转不确定终态，禁止自动重放正文。"""
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'send-commit-failure.db'}"
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        job = Job(
            job_type="wecom_send_text",
            payload={"content": "已向平台发送的客人回复"},
            status=JobStatus.PENDING,
            attempts=0,
            available_at=datetime.now(UTC),
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    send_calls = 0
    async with factory() as session:
        repository = SQLAlchemyJobRepository(session)
        checkpoint_calls = 0

        async def send_once(payload: dict) -> None:
            """模拟平台已接收发送请求。"""
            nonlocal send_calls
            send_calls += 1

        async def fail_final_commit() -> None:
            """领取提交成功，外部发送后的最终提交失败。"""
            nonlocal checkpoint_calls
            checkpoint_calls += 1
            if checkpoint_calls == 2:
                raise RuntimeError("final commit failed")
            await session.commit()

        worker = Worker(
            repository=repository,
            handlers={"wecom_send_text": send_once},
            checkpoint=fail_final_commit,
        )
        with pytest.raises(RuntimeError, match="final commit failed"):
            await worker.run_once()

    stale_at = datetime.now(UTC) - timedelta(minutes=10)
    async with factory() as session:
        job = await session.get(Job, job_id)
        assert job is not None
        assert job.status is JobStatus.RUNNING
        job.locked_at = stale_at
        await session.commit()

        recovered = await SQLAlchemyJobRepository(session).recover_stale(
            before=datetime.now(UTC) - timedelta(minutes=5)
        )
        await session.commit()
        await session.refresh(job)

        assert recovered == 1
        assert send_calls == 1
        assert job.status is JobStatus.FAILED
        assert job.last_error_code == "stale_non_replayable"
        assert job.payload == {}

    await engine.dispose()


@pytest.mark.asyncio
async def test_stale_complaint_send_marks_review_delivery_failed() -> None:
    """worker 崩溃遗留的客诉发送必须同步回写投递失败状态。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)

    async with factory() as session:
        customer = Customer(display_name="客诉客户")
        conversation = Conversation(
            customer=customer,
            open_kfid="wk-1",
            external_userid="wm-1",
        )
        session.add(conversation)
        await session.flush()
        review = ComplaintReview(
            conversation_id=conversation.id,
            source_message_id="guest-source",
            status=ComplaintReviewStatus.SEND_QUEUED,
            reason="complaint",
            risk_level="high",
            delivery_outbox_id="outbox:stale-complaint",
        )
        session.add(review)
        await session.flush()
        job = Job(
            job_type="wecom_send_text",
            payload={
                "outbox_id": "outbox:stale-complaint",
                "source_message_id": f"complaint:{review.id}",
            },
            status=JobStatus.RUNNING,
            attempts=1,
            available_at=now - timedelta(minutes=10),
            locked_at=now - timedelta(minutes=10),
        )
        session.add(job)
        await session.commit()

        await SQLAlchemyJobRepository(session).recover_stale(
            before=now - timedelta(minutes=5)
        )
        await session.refresh(review)

        assert review.status is ComplaintReviewStatus.DELIVERY_FAILED
        assert review.delivery_error_code == "stale_non_replayable"

    await engine.dispose()


@pytest.mark.asyncio
async def test_stale_credential_part_is_not_replayed() -> None:
    """进程在凭证发送后中断时必须冻结任务而不是恢复重放。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)

    async with factory() as session:
        job = Job(
            job_type="credential_send_part",
            payload={"part_id": 51},
            status=JobStatus.RUNNING,
            attempts=1,
            available_at=now - timedelta(minutes=10),
            locked_at=now - timedelta(minutes=10),
        )
        session.add(job)
        await session.commit()

        await SQLAlchemyJobRepository(session).recover_stale(
            before=now - timedelta(minutes=5)
        )
        await session.refresh(job)

        assert job.status is JobStatus.FAILED
        assert job.last_error_code == "stale_non_replayable"

    await engine.dispose()


@pytest.mark.asyncio
async def test_stale_lifecycle_send_is_not_blindly_replayed() -> None:
    """主动提醒发送结果不明时必须冻结，等待人工确认。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)

    async with factory() as session:
        job = Job(
            job_type="lifecycle_send",
            payload={"reminder_id": 51},
            status=JobStatus.RUNNING,
            attempts=1,
            available_at=now - timedelta(minutes=10),
            locked_at=now - timedelta(minutes=10),
        )
        session.add(job)
        await session.commit()

        await SQLAlchemyJobRepository(session).recover_stale(
            before=now - timedelta(minutes=5)
        )
        await session.refresh(job)

        assert job.status is JobStatus.FAILED
        assert job.last_error_code == "stale_non_replayable"

    await engine.dispose()
@pytest.mark.asyncio
async def test_outbound_messages_are_committed_to_outbox_before_network_send() -> None:
    """会话事务只写出站任务，不在提交前直接调用企业微信。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        outbox = TransactionalOutboxWeCom(
            session, source_message_id="msg-1"
        )
        message_id = await outbox.send_text("wk-1", "wm-1", "您好")
        await outbox.send_internal_text(
            agent_id=100001,
            employee_userids=["staff-1"],
            content="需要人工处理",
        )
        await session.commit()
        jobs = list(
            (
                await session.scalars(
                    select(Job).order_by(Job.id)
                )
            ).all()
        )

        assert message_id.startswith("outbox:")
        assert [job.job_type for job in jobs] == [
            "wecom_send_text",
            "wecom_send_internal_text",
        ]
        assert all(job.status is JobStatus.PENDING for job in jobs)

    await engine.dispose()


@pytest.mark.asyncio
async def test_completed_wecom_jobs_purge_content_payloads() -> None:
    """完成的企业微信正文任务不得长期保存客人或员工通知内容。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        jobs = [
            Job(
                job_type="wecom_sync",
                payload={
                    "token": "sensitive-callback-token",
                    "open_kfid": "wk-1",
                },
                status=JobStatus.RUNNING,
                attempts=1,
                available_at=datetime.now(UTC),
            ),
            Job(
                job_type="wecom_process_message",
                payload={"content": "我的手机号是13800138000", "msgid": "msg-1"},
                status=JobStatus.RUNNING,
                attempts=1,
                available_at=datetime.now(UTC),
            ),
            Job(
                job_type="wecom_send_text",
                payload={"content": "客人回复正文"},
                status=JobStatus.RUNNING,
                attempts=1,
                available_at=datetime.now(UTC),
            ),
            Job(
                job_type="wecom_send_internal_text",
                payload={"content": "员工通知正文", "employee_userids": ["staff-1"]},
                status=JobStatus.RUNNING,
                attempts=1,
                available_at=datetime.now(UTC),
            ),
            Job(
                job_type="wecom_send_internal_card",
                payload={"title": "待处理", "description": "内部卡片正文"},
                status=JobStatus.RUNNING,
                attempts=1,
                available_at=datetime.now(UTC),
            ),
        ]
        session.add_all(jobs)
        await session.commit()
        repository = SQLAlchemyJobRepository(session)
        for job in jobs:
            await repository.mark_completed(job)
        await session.commit()
        for job in jobs:
            await session.refresh(job)

        assert all(job.payload == {} for job in jobs)

    await engine.dispose()


@pytest.mark.asyncio
async def test_ack_and_final_reply_use_different_outbox_keys() -> None:
    """同一客人消息的安抚与最终回复必须各自创建发送任务。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        ack_outbox = TransactionalOutboxWeCom(
            session,
            source_message_id="msg-1",
        )
        final_outbox = TransactionalOutboxWeCom(
            session,
            source_message_id="msg-1",
            delivery_phase="final",
        )

        await ack_outbox.send_text("wk-1", "wm-1", "收到啦，我来帮您看看。")
        await final_outbox.send_text("wk-1", "wm-1", "已经为您查好啦。")
        await session.commit()

        jobs = list(
            (
                await session.scalars(
                    select(Job).where(Job.job_type == "wecom_send_text")
                )
            ).all()
        )
        assert len(jobs) == 2
        assert jobs[0].dedupe_key != jobs[1].dedupe_key

    await engine.dispose()


@pytest.mark.asyncio
async def test_replayed_final_outbox_reports_existing_message() -> None:
    """同一最终阶段重放时不得再次登记或记录客人消息。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        first = TransactionalOutboxWeCom(
            session,
            source_message_id="msg-1",
            delivery_phase="final",
        )
        replay = TransactionalOutboxWeCom(
            session,
            source_message_id="msg-1",
            delivery_phase="final",
        )

        first_id = await first.send_text("wk-1", "wm-1", "已经为您查好啦。")
        replay_id = await replay.send_text("wk-1", "wm-1", "已经为您查好啦。")

        assert first_id is not None
        assert replay_id is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_job_repository_can_separate_fast_and_deferred_queues() -> None:
    """快速发送 worker 与耗时最终生成 worker 必须领取不同任务类型。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        repository = SQLAlchemyJobRepository(session)
        await repository.enqueue("wecom_send_text", {"content": "收到啦"})
        await repository.enqueue("wecom_process_message", {"msgid": "msg-1"})
        await session.commit()

        fast_repository = SQLAlchemyJobRepository(
            session,
            excluded_job_types={"wecom_process_message"},
        )
        fast_job = await fast_repository.claim_next()
        assert fast_job is not None
        assert fast_job.job_type == "wecom_send_text"
        await fast_repository.mark_completed(fast_job)
        await session.commit()
        await fast_repository.release_claim_lock()

        deferred_repository = SQLAlchemyJobRepository(
            session,
            included_job_types={"wecom_process_message"},
        )
        deferred_job = await deferred_repository.claim_next()
        assert deferred_job is not None
        assert deferred_job.job_type == "wecom_process_message"

    await engine.dispose()


@pytest.mark.asyncio
async def test_stale_recovery_respects_worker_job_type_filters() -> None:
    """两个 worker 恢复遗留锁时只能处理自己负责的任务类型。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    now = datetime.now(UTC)

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        fast_job = Job(
            job_type="wecom_send_text",
            payload={"content": "回复"},
            status=JobStatus.RUNNING,
            attempts=1,
            available_at=now - timedelta(minutes=10),
            locked_at=now - timedelta(minutes=10),
        )
        deferred_job = Job(
            job_type="wecom_process_message",
            payload={"content": "客人原文"},
            status=JobStatus.RUNNING,
            attempts=1,
            available_at=now - timedelta(minutes=10),
            locked_at=now - timedelta(minutes=10),
        )
        session.add_all([fast_job, deferred_job])
        await session.commit()

        fast_repository = SQLAlchemyJobRepository(
            session,
            excluded_job_types={"wecom_process_message"},
        )
        recovered = await fast_repository.recover_stale(
            before=now - timedelta(minutes=5)
        )

        await session.refresh(fast_job)
        await session.refresh(deferred_job)
        assert recovered == 1
        assert fast_job.status is JobStatus.FAILED
        assert deferred_job.status is JobStatus.RUNNING

    await engine.dispose()


@pytest.mark.asyncio
async def test_stale_recovery_marks_retryable_job_failed_at_max_attempts() -> None:
    """遗留任务达到最大尝试次数后必须终止，不能无限恢复。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    now = datetime.now(UTC)

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        job = Job(
            job_type="wecom_process_message",
            payload={"content": "客人原文"},
            status=JobStatus.RUNNING,
            attempts=3,
            available_at=now - timedelta(minutes=10),
            locked_at=now - timedelta(minutes=10),
        )
        session.add(job)
        await session.commit()

        recovered = await SQLAlchemyJobRepository(session).recover_stale(
            before=now - timedelta(minutes=5),
            max_attempts=3,
        )

        await session.refresh(job)
        assert recovered == 1
        assert job.status is JobStatus.FAILED
        assert job.last_error_code == "stale_retry_limit"
        assert job.payload == {}

    await engine.dispose()


@pytest.mark.asyncio
async def test_terminal_failed_wecom_jobs_purge_payload() -> None:
    """企业微信任务进入失败终态后不得继续保留正文。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    now = datetime.now(UTC)

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        sync_job = Job(
            job_type="wecom_sync",
            payload={"token": "sensitive-callback-token", "open_kfid": "wk-1"},
            status=JobStatus.RUNNING,
            attempts=3,
            available_at=now,
        )
        process_job = Job(
            job_type="wecom_process_message",
            payload={"content": "我的手机号是13800138000"},
            status=JobStatus.RUNNING,
            attempts=3,
            available_at=now,
        )
        send_job = Job(
            job_type="wecom_send_text",
            payload={"content": "含有客人信息的回复"},
            status=JobStatus.RUNNING,
            attempts=3,
            available_at=now,
        )
        internal_job = Job(
            job_type="wecom_send_internal_text",
            payload={"content": "员工通知正文", "employee_userids": ["staff-1"]},
            status=JobStatus.RUNNING,
            attempts=3,
            available_at=now,
        )
        card_job = Job(
            job_type="wecom_send_internal_card",
            payload={"title": "待处理", "description": "内部卡片正文"},
            status=JobStatus.RUNNING,
            attempts=3,
            available_at=now,
        )
        session.add_all([sync_job, process_job, send_job, internal_job, card_job])
        await session.commit()

        repository = SQLAlchemyJobRepository(session)
        await repository.mark_failed(
            sync_job,
            error_code="timeout",
            retry_allowed=True,
            max_attempts=3,
        )
        await repository.mark_failed(
            process_job,
            error_code="timeout",
            retry_allowed=True,
            max_attempts=3,
        )
        await repository.mark_failed(
            send_job,
            error_code="timeout",
            retry_allowed=False,
            max_attempts=1,
        )
        await repository.mark_failed(
            internal_job,
            error_code="timeout",
            retry_allowed=False,
            max_attempts=1,
        )
        await repository.mark_failed(
            card_job,
            error_code="timeout",
            retry_allowed=False,
            max_attempts=1,
        )

        assert all(
            job.status is JobStatus.FAILED
            for job in [sync_job, process_job, send_job, internal_job, card_job]
        )
        assert all(
            job.payload == {}
            for job in [sync_job, process_job, send_job, internal_job, card_job]
        )

    await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_workers_do_not_claim_same_job(tmp_path) -> None:
    """SQLite 同进程多个 worker 并发领取时不得重复执行同一任务。"""
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'jobs-claim-race.db'}?timeout=30"
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    now = datetime.now(UTC)

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add_all(
            [
                Job(
                    job_type="wecom_sync",
                    payload={"token": "one"},
                    status=JobStatus.PENDING,
                    attempts=0,
                    available_at=now,
                ),
                Job(
                    job_type="wecom_sync",
                    payload={"token": "two"},
                    status=JobStatus.PENDING,
                    attempts=0,
                    available_at=now,
                ),
            ]
        )
        await session.commit()

    async def run_claim() -> int | None:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            repository = SQLAlchemyJobRepository(session)
            job = await repository.claim_next(now=now)
            if job is None:
                return None
            await session.commit()
            await repository.release_claim_lock()
            return job.id

    claimed_ids = await asyncio.gather(run_claim(), run_claim())
    assert None not in claimed_ids
    assert len(set(claimed_ids)) == 2

    await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_slow_handler_does_not_block_fast_worker(tmp_path) -> None:
    """领取提交后必须释放 SQLite 锁，慢模型任务不能阻塞快速发送。"""
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'jobs-worker-concurrency.db'}?timeout=30"
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)

    async with factory() as session:
        session.add_all(
            [
                Job(
                    job_type="wecom_process_message",
                    payload={"content": "慢任务"},
                    status=JobStatus.PENDING,
                    attempts=0,
                    available_at=now,
                ),
                Job(
                    job_type="wecom_send_text",
                    payload={"content": "快速发送"},
                    status=JobStatus.PENDING,
                    attempts=0,
                    available_at=now,
                ),
            ]
        )
        await session.commit()

    slow_started = asyncio.Event()
    release_slow = asyncio.Event()
    fast_finished = asyncio.Event()

    async def run_slow_worker() -> None:
        """让延迟 worker 停在 handler 内，模拟耗时模型调用。"""
        async with factory() as session:
            async def slow_handler(payload: dict) -> None:
                """通知测试任务已进入处理阶段，再等待显式释放。"""
                slow_started.set()
                await release_slow.wait()

            worker = Worker(
                repository=SQLAlchemyJobRepository(
                    session,
                    included_job_types={"wecom_process_message"},
                ),
                handlers={"wecom_process_message": slow_handler},
                checkpoint=session.commit,
            )
            await worker.run_once()

    async def run_fast_worker() -> None:
        """运行快速发送 worker，并在 handler 完成后设置事件。"""
        async with factory() as session:
            async def fast_handler(payload: dict) -> None:
                """记录快速任务没有被慢 handler 阻塞。"""
                fast_finished.set()

            worker = Worker(
                repository=SQLAlchemyJobRepository(
                    session,
                    excluded_job_types={"wecom_process_message"},
                ),
                handlers={"wecom_send_text": fast_handler},
                checkpoint=session.commit,
            )
            await worker.run_once()

    slow_task = asyncio.create_task(run_slow_worker())
    await asyncio.wait_for(slow_started.wait(), timeout=1)
    fast_task = asyncio.create_task(run_fast_worker())
    fast_was_unblocked = True
    try:
        await asyncio.wait_for(fast_finished.wait(), timeout=1)
    except TimeoutError:
        fast_was_unblocked = False
    finally:
        release_slow.set()
        await asyncio.gather(slow_task, fast_task)

    assert fast_was_unblocked is True

    async with factory() as session:
        jobs = list((await session.scalars(select(Job).order_by(Job.id))).all())
        assert [job.status for job in jobs] == [
            JobStatus.COMPLETED,
            JobStatus.COMPLETED,
        ]

    await engine.dispose()


@pytest.mark.asyncio
async def test_job_dedupe_key_prevents_duplicate_callback_chain() -> None:
    """相同回调游标重复入队时只能保留一项任务。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        repository = SQLAlchemyJobRepository(session)
        first = await repository.enqueue(
            "wecom_sync",
            {"cursor": "cursor-1"},
            dedupe_key="wecom-sync:key-1",
        )
        second = await repository.enqueue(
            "wecom_sync",
            {"cursor": "cursor-1"},
            dedupe_key="wecom-sync:key-1",
        )

        assert first.id == second.id

    await engine.dispose()


@pytest.mark.asyncio
async def test_enqueue_unique_race_preserves_outer_transaction(monkeypatch) -> None:
    """任务唯一键竞争应返回已存在任务，且不能破坏外层事务。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        existing = Job(
            job_type="wecom_sync",
            dedupe_key="job-race:key-1",
            payload={"cursor": "existing"},
            status=JobStatus.PENDING,
            attempts=0,
            available_at=datetime.now(UTC),
        )
        session.add(existing)
        await session.commit()
        existing_id = existing.id

    async with factory() as session:
        session.add(
            AuditLog(
                actor_employee_id=None,
                action="outer_transaction_marker",
                target_type="test",
                target_id="job-race",
                details={},
            )
        )
        original_scalar = session.scalar
        scalar_calls = 0

        async def scalar_after_race(statement, *args, **kwargs):
            """第一次查询模拟未命中，冲突后的查询读取竞争方结果。"""
            nonlocal scalar_calls
            scalar_calls += 1
            if scalar_calls == 1:
                return None
            return await original_scalar(statement, *args, **kwargs)

        monkeypatch.setattr(session, "scalar", scalar_after_race)
        raced = await SQLAlchemyJobRepository(session).enqueue(
            "wecom_sync",
            {"cursor": "racing"},
            dedupe_key="job-race:key-1",
        )
        await session.commit()

        assert raced.id == existing_id
        assert await session.scalar(
            select(AuditLog.id).where(
                AuditLog.action == "outer_transaction_marker"
            )
        ) is not None

    await engine.dispose()
