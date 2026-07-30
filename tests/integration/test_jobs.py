from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.application import TransactionalOutboxWeCom
from homestay_bot.domain.enums import JobStatus
from homestay_bot.domain.models import Base, Job
from homestay_bot.repositories.jobs import SQLAlchemyJobRepository


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
