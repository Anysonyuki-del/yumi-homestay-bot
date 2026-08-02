from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import JobStatus
from homestay_bot.domain.models import (
    AuditLog,
    Base,
    ExternalRequest,
    HostexWebhookEvent,
    Job,
)
from homestay_bot.repositories.retention import SQLAlchemyRetentionRepository


@pytest.mark.asyncio
async def test_retention_purges_only_expired_terminal_records() -> None:
    """清理应删除过期终态记录，但保留待处理任务和近期审计。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 3, tzinfo=UTC)

    async with factory() as session:
        old = now - timedelta(days=400)
        session.add_all(
            [
                Job(
                    job_type="wecom_sync",
                    payload={},
                    status=JobStatus.COMPLETED,
                    attempts=1,
                    available_at=old,
                    created_at=old,
                    updated_at=old,
                ),
                Job(
                    job_type="wecom_sync",
                    payload={"safe": "pending"},
                    status=JobStatus.PENDING,
                    attempts=0,
                    available_at=old,
                    created_at=old,
                    updated_at=old,
                ),
                ExternalRequest(
                    provider="hostex",
                    method="GET",
                    path="/reservations",
                    succeeded=True,
                    created_at=old,
                ),
                HostexWebhookEvent(
                    event_key="old-event",
                    event_type="reservation.updated",
                    payload={},
                    status="completed",
                    attempts=1,
                    created_at=old,
                    updated_at=old,
                ),
                AuditLog(
                    action="old-action",
                    target_type="job",
                    target_id="1",
                    details={},
                    created_at=old,
                ),
            ]
        )
        await session.commit()

        deleted = await SQLAlchemyRetentionRepository(session).purge(now=now)
        await session.commit()

        assert deleted == {
            "jobs": 1,
            "external_requests": 1,
            "hostex_webhook_events": 1,
            "audit_logs": 1,
        }
        assert await session.get(Job, 2) is not None

    await engine.dispose()
