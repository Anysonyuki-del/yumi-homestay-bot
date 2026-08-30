from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import ApprovalStatus, JobStatus
from homestay_bot.domain.models import (
    AuditLog,
    Base,
    BookingApproval,
    Conversation,
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
            "booking_approval_pii": 0,
            "jobs": 1,
            "external_requests": 1,
            "hostex_webhook_events": 1,
            "audit_logs": 1,
        }
        assert await session.get(Job, 2) is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_retention_purges_only_expired_terminal_approval_pii() -> None:
    """只清理达到期限的终态审批 PII，开放或近期审批必须完整保留。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 31, 12, tzinfo=UTC)

    async with factory() as session:
        conversation = Conversation(open_kfid="wk-retention", external_userid="wm-retention")
        session.add(conversation)
        await session.flush()

        def approval(
            code: str,
            status: ApprovalStatus,
            *,
            check_out_days_ago: int,
            updated_days_ago: int,
        ) -> BookingApproval:
            """构造带完整密文和指定生命周期时间的审批。"""
            return BookingApproval(
                approval_code=code,
                conversation_id=conversation.id,
                status=status,
                check_in_date=(now - timedelta(days=check_out_days_ago + 1)).date(),
                check_out_date=(now - timedelta(days=check_out_days_ago)).date(),
                number_of_guests=2,
                guest_name_ciphertext=b"encrypted-name",
                guest_mobile_ciphertext=b"encrypted-mobile",
                room_type_preference="江景房",
                special_requests_ciphertext=b"encrypted-request",
                created_at=now - timedelta(days=updated_days_ago),
                updated_at=now - timedelta(days=updated_days_ago),
            )

        records = [
            approval(
                "BOOKED-OLD",
                ApprovalStatus.BOOKED,
                check_out_days_ago=30,
                updated_days_ago=30,
            ),
            approval(
                "BOOKED-NEW",
                ApprovalStatus.BOOKED,
                check_out_days_ago=29,
                updated_days_ago=29,
            ),
            approval(
                "REJECTED-OLD",
                ApprovalStatus.REJECTED,
                check_out_days_ago=100,
                updated_days_ago=90,
            ),
            approval(
                "CONFLICT-OLD",
                ApprovalStatus.CONFLICT,
                check_out_days_ago=100,
                updated_days_ago=90,
            ),
            approval(
                "PENDING-OLD",
                ApprovalStatus.PENDING,
                check_out_days_ago=100,
                updated_days_ago=200,
            ),
            approval(
                "CREATING-OLD",
                ApprovalStatus.CREATING,
                check_out_days_ago=100,
                updated_days_ago=200,
            ),
            approval(
                "REVIEW-OLD",
                ApprovalStatus.NEEDS_REVIEW,
                check_out_days_ago=100,
                updated_days_ago=200,
            ),
        ]
        session.add_all(records)
        await session.commit()

        deleted = await SQLAlchemyRetentionRepository(session).purge(now=now)
        await session.commit()
        for record in records:
            await session.refresh(record)

        assert deleted["booking_approval_pii"] == 3
        purged_codes = {
            record.approval_code
            for record in records
            if record.pii_purged_at is not None
        }
        assert purged_codes == {"BOOKED-OLD", "REJECTED-OLD", "CONFLICT-OLD"}
        for record in records:
            if record.approval_code in purged_codes:
                assert record.guest_name_ciphertext is None
                assert record.guest_mobile_ciphertext is None
                assert record.special_requests_ciphertext is None
            else:
                assert record.guest_name_ciphertext == b"encrypted-name"
                assert record.guest_mobile_ciphertext == b"encrypted-mobile"

    await engine.dispose()
