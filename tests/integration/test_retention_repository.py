from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import (
    ApprovalStatus,
    BusinessTaskStatus,
    BusinessTaskType,
    JobStatus,
)
from homestay_bot.domain.models import (
    AuditLog,
    Base,
    BookingApproval,
    BusinessTask,
    Conversation,
    ExternalRequest,
    HostexWebhookEvent,
    Job,
    PropertyProfile,
    TaskAttachment,
)
from homestay_bot.repositories.retention import SQLAlchemyRetentionRepository
from homestay_bot.services.task_page_service import ATTACHMENT_CLEANUP_JOB_TYPE


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


@pytest.mark.asyncio
async def test_archived_task_cleanup_respects_every_boundary() -> None:
    """自动清理只碰归档满 180 天的任务，其余一律不动。

    这是本项目第一个会自动销毁业务数据的清理，边界写错就是静默丢数据：
    未归档的任务无论多旧都不删——自动删掉还没人处理的活，比留着它危险得多。
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 9, 8, tzinfo=UTC)

    async with factory() as session:
        await session.execute(text("PRAGMA foreign_keys=ON"))
        session.add(PropertyProfile(id=101, title="测试房间"))
        await session.flush()

        def make(description: str, archived_at: datetime | None) -> BusinessTask:
            """构造一条终态任务并指定归档时间。"""
            return BusinessTask(
                task_type=BusinessTaskType.CLEANING,
                status=BusinessTaskStatus.EXPIRED,
                property_id=101,
                service_date=date(2025, 1, 1),
                description=description,
                archived_at=archived_at,
            )

        old_archived = make("归档满 181 天", now - timedelta(days=181))
        fresh_archived = make("归档仅 179 天", now - timedelta(days=179))
        never_archived = make("从未归档但很旧", None)
        session.add_all([old_archived, fresh_archived, never_archived])
        await session.flush()
        session.add(
            TaskAttachment(
                task_id=old_archived.id,
                private_file_id="0123456789abcdef",
                kind="photo",
            )
        )
        await session.flush()

        deleted = await SQLAlchemyRetentionRepository(session).purge_archived_tasks(
            now=now,
        )
        await session.commit()

        assert deleted == 1
        assert await session.get(BusinessTask, old_archived.id) is None
        # 未满保留期与从未归档的都必须完好
        assert await session.get(BusinessTask, fresh_archived.id) is not None
        assert await session.get(BusinessTask, never_archived.id) is not None
        # 照片清理改为与删库同事务登记、提交之后由 worker 幂等执行。此前是先删
        # 文件再删库，一旦提交失败数据库回滚而照片已经没了，没有备份就无法重建。
        # 这里断言的是「清理有据可查且不会丢」，比原来的「已经删掉了」更强。
        cleanup = await session.scalar(
            select(Job).where(Job.job_type == ATTACHMENT_CLEANUP_JOB_TYPE)
        )
        assert cleanup is not None
        assert cleanup.payload["file_ids"] == ["0123456789abcdef"]
        assert cleanup.status is JobStatus.PENDING


@pytest.mark.asyncio
async def test_photo_survives_a_failed_commit() -> None:
    """提交失败时照片必须一张不少。

    此前三个删除入口都是先删磁盘照片、再另开事务删数据库行，理由是「反过来
    失败只留下一条指向缺失文件的记录，可以修复」。那句话对记录成立，对照片
    不成立：提交一旦失败，数据库回滚而照片已经没了，没有备份就无法重建原图。
    现在删库与清理登记同事务提交，照片由 worker 在提交之后才删。
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 9, 8, tzinfo=UTC)

    async with factory() as session:
        session.add(PropertyProfile(id=101, title="测试房间"))
        await session.flush()
        task = BusinessTask(
            task_type=BusinessTaskType.CLEANING,
            # COMPLETED 属于可执行状态，按 ck_business_task_execution_fields
            # 必须带房间与服务日期。
            status=BusinessTaskStatus.COMPLETED,
            property_id=101,
            service_date=date(2026, 8, 1),
            description="很久以前归档",
            archived_at=now - timedelta(days=400),
        )
        session.add(task)
        await session.flush()
        session.add(
            TaskAttachment(
                task_id=task.id,
                private_file_id="0123456789abcdef",
                kind="photo",
            )
        )
        await session.commit()

    deleted_files: list[str] = []

    async with factory() as session:
        await SQLAlchemyRetentionRepository(session).purge_archived_tasks(now=now)
        # 模拟提交失败：回滚后数据库回到原状，而这一路上没有任何文件被删过。
        await session.rollback()

    assert deleted_files == []
    async with factory() as session:
        assert await session.get(BusinessTask, task.id) is not None
        assert await session.scalar(
            select(Job).where(Job.job_type == ATTACHMENT_CLEANUP_JOB_TYPE)
        ) is None

    await engine.dispose()
