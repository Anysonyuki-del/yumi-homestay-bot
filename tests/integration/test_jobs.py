from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
