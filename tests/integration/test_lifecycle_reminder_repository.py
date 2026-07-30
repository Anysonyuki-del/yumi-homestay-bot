from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import (
    BusinessTaskStatus,
    BusinessTaskType,
    CustomerIdentityProvider,
    JobStatus,
    Language,
    MessageOrigin,
    ReminderStatus,
    ReminderType,
)
from homestay_bot.domain.models import (
    AuditLog,
    Base,
    BusinessTask,
    Conversation,
    Customer,
    CustomerIdentity,
    Job,
    LifecycleReminder,
    Message,
    PropertyProfile,
    StayOrder,
)
from homestay_bot.repositories.lifecycle_reminders import (
    SQLAlchemyLifecycleReminderRepository,
)
from homestay_bot.repositories.operations import (
    SQLAlchemyOperationsRepository,
)
from homestay_bot.services.business_task_service import BusinessTaskService


async def seed_order_context(session):
    """建立两位客户，供客户隔离和消息计数测试使用。"""
    target = Customer(display_name="订单客户")
    other = Customer(display_name="其他客户")
    property_profile = PropertyProfile(
        id=101,
        title="长江中心",
        district="武昌区",
        address_hint="地铁站步行约 5 分钟",
        parking_instructions="到店前联系管家确认车位",
    )
    session.add_all([target, other, property_profile])
    await session.flush()
    order = StayOrder(
        hostex_reservation_code="R-1",
        stay_code="S-1",
        customer_id=target.id,
        property_id=property_profile.id,
        check_in_date=date(2026, 8, 2),
        check_out_date=date(2026, 8, 3),
        status="confirmed",
    )
    target_conversation = Conversation(
        customer_id=target.id,
        open_kfid="wk-target",
        external_userid="wm-target",
        language=Language.ZH,
    )
    other_conversation = Conversation(
        customer_id=other.id,
        open_kfid="wk-other",
        external_userid="wm-other",
        language=Language.ZH,
    )
    session.add_all([order, target_conversation, other_conversation])
    await session.flush()
    session.add_all(
        [
            CustomerIdentity(
                customer_id=target.id,
                provider=CustomerIdentityProvider.WECOM_KF,
                external_id="wm-target",
                is_verified=True,
            ),
            CustomerIdentity(
                customer_id=other.id,
                provider=CustomerIdentityProvider.WECOM_KF,
                external_id="wm-other",
                is_verified=True,
            ),
            Message(
                conversation_id=target_conversation.id,
                external_message_id="target-guest",
                origin=MessageOrigin.GUEST,
                message_type="text",
                content="明天见",
                sent_at=datetime(2026, 8, 1, 8, tzinfo=UTC),
            ),
            Message(
                conversation_id=target_conversation.id,
                external_message_id="target-bot",
                origin=MessageOrigin.BOT,
                message_type="text",
                content="好的",
                sent_at=datetime(2026, 8, 1, 8, 1, tzinfo=UTC),
            ),
            Message(
                conversation_id=target_conversation.id,
                external_message_id="target-servicer",
                origin=MessageOrigin.SERVICER,
                message_type="text",
                content="欢迎入住",
                sent_at=datetime(2026, 8, 1, 8, 2, tzinfo=UTC),
            ),
            Message(
                conversation_id=other_conversation.id,
                external_message_id="other-newer",
                origin=MessageOrigin.GUEST,
                message_type="text",
                content="不属于订单客户",
                sent_at=datetime(2026, 8, 1, 9, tzinfo=UTC),
            ),
        ]
    )
    await session.flush()
    return order


@pytest.mark.asyncio
async def test_reminder_is_idempotent_and_context_is_customer_isolated() -> None:
    """提醒唯一，且只能使用订单客户已验证的最近微信会话。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        order = await seed_order_context(session)
        repository = SQLAlchemyLifecycleReminderRepository(session)
        fields = {
            "order_id": order.id,
            "reminder_type": ReminderType.PRE_ARRIVAL,
            "scheduled_local_date": date(2026, 8, 1),
            "scheduled_at": datetime(2026, 8, 1, 10, tzinfo=UTC),
        }

        first = await repository.ensure_reminder(**fields)
        second = await repository.ensure_reminder(**fields)
        context = await repository.require_send_context(first.id)
        await session.commit()

        count = await session.scalar(
            select(func.count(LifecycleReminder.id))
        )
        assert first.id == second.id
        assert count == 1
        assert context.open_kfid == "wk-target"
        assert context.external_userid == "wm-target"
        assert context.last_guest_at == datetime(
            2026, 8, 1, 8, tzinfo=UTC
        )
        assert context.sent_count == 2

    await engine.dispose()


@pytest.mark.asyncio
async def test_accepted_and_async_failure_create_safe_manual_task() -> None:
    """平台受理后仍可按异步失败转为幂等人工联系任务。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        order = await seed_order_context(session)
        repository = SQLAlchemyLifecycleReminderRepository(session)
        reminder = await repository.ensure_reminder(
            order_id=order.id,
            reminder_type=ReminderType.PRE_ARRIVAL,
            scheduled_local_date=date(2026, 8, 1),
            scheduled_at=datetime(2026, 8, 1, 10, tzinfo=UTC),
        )
        await repository.mark_platform_accepted(
            reminder.id,
            "accepted-msg-1",
        )
        tasks = BusinessTaskService(
            SQLAlchemyOperationsRepository(session)
        )
        await tasks.create_manual_contact(reminder, "wecom_fail_10")
        await tasks.create_manual_contact(reminder, "wecom_fail_10")
        await repository.mark_manual_followup(
            reminder.id,
            "wecom_fail_10",
        )
        await session.commit()

        task = await session.scalar(select(BusinessTask))
        audits = list(
            (
                await session.scalars(
                    select(AuditLog)
                    .where(
                        AuditLog.target_type.in_(
                            ["lifecycle_reminder", "business_task"]
                        )
                    )
                    .order_by(AuditLog.id)
                )
            ).all()
        )

        assert reminder.status is ReminderStatus.MANUAL_FOLLOWUP
        assert reminder.external_message_id == "accepted-msg-1"
        assert reminder.failure_reason == "wecom_fail_10"
        assert task is not None
        assert task.task_type is BusinessTaskType.MANUAL_CONTACT
        assert task.status is BusinessTaskStatus.PENDING_CONFIRMATION
        assert "客户拒收" in task.description
        assert (
            await session.scalar(select(func.count(BusinessTask.id)))
            == 1
        )
        assert "订单客户" not in str([item.details for item in audits])

    await engine.dispose()


@pytest.mark.asyncio
async def test_cancel_for_order_only_closes_scheduled_reminders() -> None:
    """订单取消只撤销尚未发送的提醒，并保持历史平台受理记录。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        order = await seed_order_context(session)
        repository = SQLAlchemyLifecycleReminderRepository(session)
        scheduled = await repository.ensure_reminder(
            order_id=order.id,
            reminder_type=ReminderType.ARRIVAL_DAY,
            scheduled_local_date=date(2026, 8, 2),
            scheduled_at=datetime(2026, 8, 2, 2, tzinfo=UTC),
        )
        accepted = await repository.ensure_reminder(
            order_id=order.id,
            reminder_type=ReminderType.PRE_ARRIVAL,
            scheduled_local_date=date(2026, 8, 1),
            scheduled_at=datetime(2026, 8, 1, 10, tzinfo=UTC),
        )
        await repository.mark_platform_accepted(accepted.id, "msg-history")

        changed = await repository.cancel_for_order(order.id)
        await session.commit()

        assert changed == 1
        assert scheduled.status is ReminderStatus.CANCELLED
        assert accepted.status is ReminderStatus.PLATFORM_ACCEPTED

    await engine.dispose()


@pytest.mark.asyncio
async def test_reschedule_cancels_obsolete_pending_reminder() -> None:
    """订单改期后旧日期提醒必须撤销，避免旧任务误发。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        order = await seed_order_context(session)
        repository = SQLAlchemyLifecycleReminderRepository(session)
        obsolete = await repository.ensure_reminder(
            order_id=order.id,
            reminder_type=ReminderType.PRE_ARRIVAL,
            scheduled_local_date=date(2026, 7, 31),
            scheduled_at=datetime(2026, 7, 31, 10, tzinfo=UTC),
        )

        changed = await repository.cancel_obsolete_for_order(
            order.id,
            [
                (ReminderType.PRE_ARRIVAL, date(2026, 8, 1)),
                (ReminderType.ARRIVAL_DAY, date(2026, 8, 2)),
            ],
        )
        await session.commit()

        assert changed == 1
        assert obsolete.status is ReminderStatus.CANCELLED
        assert obsolete.failure_reason == "order_rescheduled"

    await engine.dispose()


@pytest.mark.asyncio
async def test_reconfirmed_same_dates_rearms_cancelled_schedule() -> None:
    """取消后按相同日期恢复的订单必须重新激活唯一提醒和旧任务。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        order = await seed_order_context(session)
        repository = SQLAlchemyLifecycleReminderRepository(session)
        fields = {
            "order_id": order.id,
            "reminder_type": ReminderType.PRE_ARRIVAL,
            "scheduled_local_date": date(2026, 8, 1),
            "scheduled_at": datetime(2026, 8, 1, 10, tzinfo=UTC),
        }
        reminder = await repository.ensure_reminder(**fields)
        job = Job(
            job_type="lifecycle_send",
            dedupe_key="lifecycle:1:pre_arrival:2026-08-01",
            payload={"reminder_id": reminder.id},
            status=JobStatus.COMPLETED,
            attempts=1,
            available_at=fields["scheduled_at"],
        )
        session.add(job)
        await repository.cancel_for_order(order.id)

        reactivated = await repository.ensure_reminder(**fields)
        await session.commit()

        assert reactivated.id == reminder.id
        assert reactivated.status is ReminderStatus.SCHEDULED
        assert reactivated.failure_reason is None
        assert job.status is JobStatus.PENDING
        assert job.attempts == 0

    await engine.dispose()
