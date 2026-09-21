from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import (
    BusinessTaskStatus,
    BusinessTaskType,
    EmployeeRole,
    RoomOperationalStatus,
    TaskClosureReason,
    TaskClosureSource,
)
from homestay_bot.domain.errors import OperationRefused
from homestay_bot.domain.models import (
    AuditLog,
    Base,
    BusinessTask,
    Customer,
    Employee,
    HostexWebhookEvent,
    Job,
    PropertyProfile,
    PurgedTaskMark,
    RoomOperationalState,
    StayOrder,
    TaskAttachment,
)
from homestay_bot.integrations.hostex_client import Reservation
from homestay_bot.repositories.operations import (
    PURGED_MARK_RETENTION_DAYS,
    SQLAlchemyOperationsRepository,
)
from homestay_bot.repositories.retention import SQLAlchemyRetentionRepository
from homestay_bot.services.business_task_service import BusinessTaskService
from homestay_bot.services.task_lifecycle_service import TaskLifecycleService
from homestay_bot.services.task_page_service import TaskPageService


@pytest.mark.asyncio
async def test_turnover_task_dedupe_key_is_unique() -> None:
    """同一房间同一服务日只能生成一个周转保洁任务。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        session.add(PropertyProfile(id=101, title="测试房间"))
        await session.flush()
        repository = SQLAlchemyOperationsRepository(session)

        first = await repository.create_turnover(
            property_id=101,
            service_date=date(2026, 8, 1),
        )
        second = await repository.create_turnover(
            property_id=101,
            service_date=date(2026, 8, 1),
        )
        await session.commit()

        assert first.id == second.id
        assert first.task_type is BusinessTaskType.CLEANING
        assert first.status is BusinessTaskStatus.PENDING_ASSIGNMENT

    await engine.dispose()


@pytest.mark.asyncio
async def test_pending_ai_task_allows_unknown_property_and_date() -> None:
    """待管理员确认的 AI 建议允许暂时缺少房间和服务日期。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        customer = Customer(display_name="测试客户")
        session.add(customer)
        await session.flush()
        session.add(
            BusinessTask(
                source_message_id="msg-pending",
                task_type=BusinessTaskType.SUPPLIES,
                status=BusinessTaskStatus.PENDING_CONFIRMATION,
                customer_id=customer.id,
                property_id=None,
                service_date=None,
                description="补矿泉水",
            )
        )
        await session.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_hostex_completion_does_not_overwrite_newer_event_status() -> None:
    """百居易网络调用后的旧快照不得覆盖其他事务写入的终态。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        event = HostexWebhookEvent(
            event_key="conditional-event",
            event_type="reservation.updated",
            reservation_code="R-1",
            payload={},
        )
        session.add(event)
        await session.commit()
        event_id = event.id

    async with factory() as worker_session:
        repository = SQLAlchemyOperationsRepository(worker_session)
        stale_event = await repository.require_pending_event("conditional-event")
        await worker_session.commit()

        async with factory() as newer_session:
            newer_event = await newer_session.get(HostexWebhookEvent, event_id)
            assert newer_event is not None
            newer_event.status = "failed"
            newer_event.last_error_code = "newer_failure"
            await newer_session.commit()

        completed = await repository.mark_event_completed(stale_event)
        await worker_session.commit()

        await worker_session.refresh(stale_event)
        assert completed is False
        assert stale_event.status == "failed"
        assert stale_event.last_error_code == "newer_failure"

    await engine.dispose()


@pytest.mark.asyncio
async def test_pending_task_unique_race_preserves_outer_transaction(monkeypatch) -> None:
    """AI 任务来源键竞争应返回已有任务，且不能破坏外层事务。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        customer = Customer(display_name="并发客户")
        session.add(customer)
        await session.flush()
        existing = BusinessTask(
            source_message_id="task-race-message",
            task_type=BusinessTaskType.SUPPLIES,
            status=BusinessTaskStatus.PENDING_CONFIRMATION,
            customer_id=customer.id,
            description="竞争方任务",
        )
        session.add(existing)
        await session.commit()
        existing_id = existing.id
        customer_id = customer.id

    async with factory() as session:
        session.add(
            AuditLog(
                actor_employee_id=None,
                action="task_outer_marker",
                target_type="test",
                target_id="task-race",
                details={},
            )
        )
        original_scalar = session.scalar
        scalar_calls = 0

        async def scalar_after_race(statement, *args, **kwargs):
            """第一次查询模拟未命中，冲突后读取竞争方已提交的任务。"""
            nonlocal scalar_calls
            scalar_calls += 1
            if scalar_calls == 1:
                return None
            return await original_scalar(statement, *args, **kwargs)

        monkeypatch.setattr(session, "scalar", scalar_after_race)
        task = await SQLAlchemyOperationsRepository(session).create_pending_confirmation(
            customer_id=customer_id,
            source_message_id="task-race-message",
            task_type=BusinessTaskType.SUPPLIES,
            description="本 worker 任务",
        )
        await session.commit()

        assert task.id == existing_id
        assert await session.scalar(
            select(AuditLog.id).where(AuditLog.action == "task_outer_marker")
        ) is not None
        assert await session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.action == "ai_task_suggested"
            )
        ) == 0

    await engine.dispose()


@pytest.mark.asyncio
async def test_executable_task_rejects_unknown_property_or_date() -> None:
    """数据库必须拒绝缺少执行地点或日期的可执行任务。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        session.add(
            BusinessTask(
                source_message_id="msg-invalid",
                task_type=BusinessTaskType.SUPPLIES,
                status=BusinessTaskStatus.PENDING_ASSIGNMENT,
                property_id=None,
                service_date=None,
                description="补矿泉水",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_handoff_audit_does_not_store_chat_body() -> None:
    """人工接管审计只保存原因和内部主键，不复制客人消息。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        repository = SQLAlchemyOperationsRepository(session)
        await repository.record_handoff(
            conversation_id=7,
            customer_id=9,
            reason="refund",
        )
        await session.commit()

        audit = await session.scalar(
            select(AuditLog).where(AuditLog.action == "conversation_handoff")
        )

        assert audit is not None
        assert audit.target_id == "7"
        assert audit.details == {
            "customer_id": 9,
            "reason": "refund",
        }
        assert "聊天正文" not in str(audit.details)

    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_assignment_uses_state_machine_and_safe_audits() -> None:
    """真实分派必须经过待分派和已分派，并且审计不复制任务正文。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        admin = Employee(
            wecom_userid="admin",
            name="管理员",
            role=EmployeeRole.ADMIN,
        )
        staff = Employee(
            wecom_userid="staff",
            name="执行员工",
            role=EmployeeRole.STAFF,
        )
        property_profile = PropertyProfile(id=101, title="长江中心")
        pending = BusinessTask(
            source_message_id="msg-assignment",
            task_type=BusinessTaskType.SUPPLIES,
            status=BusinessTaskStatus.PENDING_CONFIRMATION,
            description="补矿泉水，敏感正文不得进入审计",
        )
        session.add_all([admin, staff, property_profile, pending])
        await session.flush()
        repository = SQLAlchemyOperationsRepository(session)
        service = TaskPageService(
            repository,
            BusinessTaskService(repository),
        )

        assigned = await service.assign(
            pending.id,
            admin,
            assigned_employee_id=staff.id,
            property_id=101,
            service_date=date(2026, 8, 2),
        )
        await session.commit()

        audits = list(
            (
                await session.scalars(
                    select(AuditLog)
                    .where(AuditLog.target_id == str(pending.id))
                    .order_by(AuditLog.id)
                )
            ).all()
        )

        assert assigned.status is BusinessTaskStatus.ASSIGNED
        assert assigned.assigned_employee_id == staff.id
        assert [item.action for item in audits] == [
            "business_task_assignment_prepared",
            "business_task_status_changed",
            "business_task_status_changed",
        ]
        assert "敏感正文" not in str([item.details for item in audits])

    await engine.dispose()


@pytest.mark.asyncio
async def test_hostex_event_and_reservation_upsert_are_idempotent() -> None:
    """重复 Webhook 只入队一次，订单更新不得新增重复订单。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        repository = SQLAlchemyOperationsRepository(session)
        first_event = await repository.record_hostex_event(
            event_key="event-1",
            event_type="reservation_updated",
            reservation_code="R-1",
            payload={"unknown_future_field": "ignored"},
        )
        second_event = await repository.record_hostex_event(
            event_key="event-1",
            event_type="reservation_updated",
            reservation_code="R-1",
            payload={"unknown_future_field": "ignored"},
        )
        confirmed = Reservation(
            reservation_code="R-1",
            stay_code="S-1",
            property_id=101,
            check_in_date=date(2026, 8, 1),
            check_out_date=date(2026, 8, 2),
            status="confirmed",
            created_at="2026-07-31T00:00:00Z",
        )
        cancelled = confirmed.model_copy(update={"status": "cancelled"})
        first_order = await repository.upsert_reservation(confirmed)
        second_order = await repository.upsert_reservation(cancelled)
        await session.commit()

        job_count = await session.scalar(
            select(func.count(Job.id)).where(Job.job_type == "hostex_event")
        )
        order_count = await session.scalar(select(func.count(StayOrder.id)))

        assert first_event is True
        assert second_event is False
        assert job_count == 1
        assert first_order.id == second_order.id
        assert second_order.status == "cancelled"
        assert order_count == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_reservation_upsert_records_first_checkout_observation_once() -> None:
    """订单首次进入退房终态时记当天，重复同步不得漂移观察日。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    observed_dates = iter((date(2026, 8, 14), date(2026, 8, 15)))

    async with factory() as session:
        repository = SQLAlchemyOperationsRepository(
            session,
            local_date_provider=lambda: next(observed_dates),
        )
        checked_out = Reservation(
            reservation_code="R-CHECKOUT-ONCE",
            stay_code="S-CHECKOUT-ONCE",
            property_id=201,
            check_in_date=date(2026, 8, 12),
            check_out_date=date(2026, 8, 14),
            status=" Checked_Out ",
            created_at="2026-08-12T00:00:00Z",
        )

        first = await repository.upsert_reservation(checked_out)
        assert first.checkout_observed_on == date(2026, 8, 14)

        second = await repository.upsert_reservation(
            checked_out.model_copy(update={"status": "completed"})
        )
        assert second.checkout_observed_on == date(2026, 8, 14)

    await engine.dispose()


@pytest.mark.asyncio
async def test_reservation_upsert_clears_and_rerecords_checkout_observation() -> None:
    """订单恢复有效状态时清空观察日，再次退房时记录新的武汉日期。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    observed_dates = iter((date(2026, 8, 14), date(2026, 8, 18)))

    async with factory() as session:
        repository = SQLAlchemyOperationsRepository(
            session,
            local_date_provider=lambda: next(observed_dates),
        )
        reservation = Reservation(
            reservation_code="R-CHECKOUT-AGAIN",
            stay_code="S-CHECKOUT-AGAIN",
            property_id=202,
            check_in_date=date(2026, 8, 12),
            check_out_date=date(2026, 8, 14),
            status="checked_out",
            created_at="2026-08-12T00:00:00Z",
        )

        first = await repository.upsert_reservation(reservation)
        assert first.checkout_observed_on == date(2026, 8, 14)

        restored = await repository.upsert_reservation(
            reservation.model_copy(update={"status": "confirmed"})
        )
        assert restored.checkout_observed_on is None

        completed_again = await repository.upsert_reservation(
            reservation.model_copy(update={"status": "completed"})
        )
        assert completed_again.checkout_observed_on == date(2026, 8, 18)

    await engine.dispose()


@pytest.mark.asyncio
async def test_cancelled_sync_does_not_erase_checkout_observation() -> None:
    """取消等排除状态不是恢复入住，不得抹去已记录的退房观察日。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        repository = SQLAlchemyOperationsRepository(
            session,
            local_date_provider=lambda: date(2026, 8, 14),
        )
        reservation = Reservation(
            reservation_code="R-CHECKOUT-CANCELLED",
            stay_code="S-CHECKOUT-CANCELLED",
            property_id=203,
            check_in_date=date(2026, 8, 12),
            check_out_date=date(2026, 8, 14),
            status="completed",
            created_at="2026-08-12T00:00:00Z",
        )

        await repository.upsert_reservation(reservation)
        cancelled = await repository.upsert_reservation(
            reservation.model_copy(update={"status": "cancelled"})
        )

        assert cancelled.checkout_observed_on == date(2026, 8, 14)

    await engine.dispose()


@pytest.mark.asyncio
async def test_future_completed_reservation_does_not_record_impossible_checkout() -> None:
    """未来尚未入住的异常终态订单不得伪造当天为实际退房观察日。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        repository = SQLAlchemyOperationsRepository(
            session,
            local_date_provider=lambda: date(2026, 8, 14),
        )
        reservation = Reservation(
            reservation_code="R-FUTURE-COMPLETED",
            stay_code="S-FUTURE-COMPLETED",
            property_id=204,
            check_in_date=date(2026, 8, 20),
            check_out_date=date(2026, 8, 22),
            status="completed",
            created_at="2026-08-12T00:00:00Z",
        )

        order = await repository.upsert_reservation(reservation)

        assert order.checkout_observed_on is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_hostex_event_unique_race_preserves_outer_transaction(monkeypatch) -> None:
    """Webhook 事件与任务竞争应整体回滚候选写入，并保留外层事务。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        session.add(
            HostexWebhookEvent(
                event_key="hostex-race-event",
                event_type="reservation_updated",
                reservation_code="R-RACE",
                payload={"source": "competitor"},
            )
        )
        session.add(
            Job(
                job_type="hostex_event",
                dedupe_key="hostex-event:hostex-race-event",
                payload={"event_key": "hostex-race-event"},
                available_at=datetime.now(UTC),
            )
        )
        await session.commit()

    async with factory() as session:
        session.add(
            AuditLog(
                actor_employee_id=None,
                action="hostex_outer_marker",
                target_type="test",
                target_id="hostex-race",
                details={},
            )
        )
        original_scalar = session.scalar
        scalar_calls = 0

        async def scalar_after_race(statement, *args, **kwargs):
            """第一次查询模拟未命中，让保存点处理事件和任务的联合竞争。"""
            nonlocal scalar_calls
            scalar_calls += 1
            if scalar_calls == 1:
                return None
            return await original_scalar(statement, *args, **kwargs)

        monkeypatch.setattr(session, "scalar", scalar_after_race)
        created = await SQLAlchemyOperationsRepository(session).record_hostex_event(
            event_key="hostex-race-event",
            event_type="reservation_updated",
            reservation_code="R-RACE",
            payload={"source": "current-worker"},
        )
        await session.commit()

        assert created is False
        assert await session.scalar(
            select(AuditLog.id).where(AuditLog.action == "hostex_outer_marker")
        ) is not None
        assert await session.scalar(
            select(func.count(HostexWebhookEvent.id)).where(
                HostexWebhookEvent.event_key == "hostex-race-event"
            )
        ) == 1
        assert await session.scalar(
            select(func.count(Job.id)).where(
                Job.dedupe_key == "hostex-event:hostex-race-event"
            )
        ) == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_hostex_savepoint_does_not_swallow_outer_integrity_error() -> None:
    """保存点建立前的外层约束错误不得被误判成重复 Webhook。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        session.add(
            BusinessTask(
                source_message_id="invalid-outer-task",
                task_type=BusinessTaskType.SUPPLIES,
                status=BusinessTaskStatus.PENDING_ASSIGNMENT,
                property_id=None,
                service_date=None,
                description="缺少执行字段",
            )
        )

        with pytest.raises(IntegrityError):
            await SQLAlchemyOperationsRepository(session).record_hostex_event(
                event_key="hostex-new-event",
                event_type="reservation_updated",
                reservation_code="R-NEW",
                payload={},
            )

    await engine.dispose()


@pytest.mark.asyncio
async def test_checklist_attachment_and_room_state_use_safe_audits() -> None:
    """检查证据与房态变更落库，审计不得复制图片或任务正文。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        staff = Employee(
            wecom_userid="room-staff",
            name="执行员工",
            role=EmployeeRole.STAFF,
        )
        room = PropertyProfile(id=101, title="长江中心")
        task = BusinessTask(
            task_type=BusinessTaskType.CLEANING,
            status=BusinessTaskStatus.PENDING_INSPECTION,
            property_id=101,
            service_date=date(2026, 8, 2),
            assigned_employee_id=None,
            description="敏感任务正文",
        )
        session.add_all([staff, room])
        await session.flush()
        task.assigned_employee_id = staff.id
        session.add(task)
        await session.flush()
        repository = SQLAlchemyOperationsRepository(session)

        with pytest.raises(PermissionError):
            await repository.update_task_checklist(
                task_id=task.id,
                employee_id=staff.id + 1,
                checklist={"clean": True, "supplies": True, "damage": True},
            )
        await repository.update_task_checklist(
            task_id=task.id,
            employee_id=staff.id,
            checklist={"clean": True, "supplies": True, "damage": True},
        )
        attachment = await repository.add_task_attachment(
            task_id=task.id,
            file_id="a" * 32 + ".png",
            uploaded_by=staff.id,
        )
        state = await repository.set_room_status(
            101,
            RoomOperationalStatus.READY,
            staff.id,
        )
        await session.commit()

        audits = list(
            (
                await session.scalars(
                    select(AuditLog).where(
                        AuditLog.target_type.in_(
                            ["business_task", "room_operational_state"]
                        )
                    )
                )
            ).all()
        )
        stored_attachment = await session.get(TaskAttachment, attachment.id)
        stored_state = await session.get(RoomOperationalState, 101)

        assert stored_attachment is not None
        assert stored_state is not None
        assert stored_state.status is RoomOperationalStatus.READY
        assert state.version == 1
        assert await repository.has_photo_attachment(task.id) is True
        assert "敏感任务正文" not in str([item.details for item in audits])
        assert "PNG" not in str([item.details for item in audits])

    await engine.dispose()


@pytest.mark.asyncio
async def test_ready_does_not_overwrite_maintenance_room() -> None:
    """保洁证据不能把维修中的房间直接覆盖为可入住。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        session.add(PropertyProfile(id=101, title="维修房"))
        session.add(
            RoomOperationalState(
                property_id=101,
                status=RoomOperationalStatus.MAINTENANCE,
                version=3,
            )
        )
        await session.flush()
        repository = SQLAlchemyOperationsRepository(session)

        with pytest.raises(ValueError, match="维修"):
            await repository.set_room_status(
                101,
                RoomOperationalStatus.READY,
                1,
            )

    await engine.dispose()


@pytest.mark.asyncio
async def test_task_lifecycle_expires_cancelled_order_task_with_audit() -> None:
    """取消订单的未开始任务应安全失效，并保留可核验关闭审计。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 29, 8, tzinfo=UTC)

    async with factory() as session:
        room = PropertyProfile(id=301, title="取消订单房间")
        order = StayOrder(
            hostex_reservation_code="cancelled-lifecycle",
            stay_code="cancelled-lifecycle",
            property_id=room.id,
            check_in_date=date(2026, 8, 30),
            check_out_date=date(2026, 8, 31),
            status="cancelled",
        )
        session.add_all([room, order])
        await session.flush()
        task = BusinessTask(
            dedupe_key="turnover:301:2026-08-31",
            task_type=BusinessTaskType.CLEANING,
            status=BusinessTaskStatus.PENDING_ASSIGNMENT,
            order_id=order.id,
            property_id=room.id,
            service_date=order.check_out_date,
            description="退房后周转保洁",
        )
        session.add(task)
        await session.flush()

        result = await TaskLifecycleService(
            SQLAlchemyOperationsRepository(session)
        ).sweep(now=now, limit=100)
        await session.commit()

        stored = await session.get(BusinessTask, task.id)
        audit = await session.scalar(
            select(AuditLog).where(AuditLog.action == "business_task_expired")
        )

    assert result.expired == 1
    assert stored is not None
    assert stored.status is BusinessTaskStatus.EXPIRED
    assert stored.closure_reason_code is TaskClosureReason.ORDER_CANCELLED
    assert stored.closure_source is TaskClosureSource.SYSTEM
    assert stored.closed_at == now
    assert audit is not None
    assert audit.details["reason"] == "order_cancelled"
    await engine.dispose()


@pytest.mark.asyncio
async def test_task_queue_excludes_expired_by_default_and_allows_history_filter() -> None:
    """默认队列只展示开放任务，管理员仍可显式查看失效历史。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        room = PropertyProfile(id=401, title="任务队列房间")
        session.add(room)
        await session.flush()
        active = BusinessTask(
            task_type=BusinessTaskType.MAINTENANCE,
            status=BusinessTaskStatus.PENDING_ASSIGNMENT,
            property_id=room.id,
            service_date=date(2026, 8, 28),
            description="待处理维修",
        )
        expired = BusinessTask(
            task_type=BusinessTaskType.CLEANING,
            status=BusinessTaskStatus.EXPIRED,
            property_id=room.id,
            service_date=date(2026, 8, 27),
            description="已失效保洁",
        )
        session.add_all([active, expired])
        await session.flush()
        repository = SQLAlchemyOperationsRepository(session)

        open_items = await repository.list_all_open(offset=0, limit=10)
        expired_items = await repository.list_all_open(
            offset=0,
            limit=10,
            status=BusinessTaskStatus.EXPIRED,
        )
        overdue_items = await repository.list_all_open(
            offset=0,
            limit=10,
            overdue_before=date(2026, 8, 29),
        )

    assert [item.id for item in open_items] == [active.id]
    assert [item.id for item in expired_items] == [expired.id]
    assert [item.id for item in overdue_items] == [active.id]
    await engine.dispose()


@pytest.mark.asyncio
async def test_archive_only_accepts_terminal_tasks_and_hides_them() -> None:
    """归档只接受终态任务，且归档后不再出现在默认列表。

    失效任务此前只能无限堆积；归档必须可逆，且不能被用来把没做的活藏起来。
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        session.add(PropertyProfile(id=101, title="测试房间"))
        await session.flush()
        repository = SQLAlchemyOperationsRepository(session)

        expired = BusinessTask(
            task_type=BusinessTaskType.CLEANING,
            status=BusinessTaskStatus.EXPIRED,
            property_id=101,
            service_date=date(2026, 8, 1),
            description="已失效",
        )
        assigned = BusinessTask(
            task_type=BusinessTaskType.CLEANING,
            status=BusinessTaskStatus.ASSIGNED,
            property_id=101,
            service_date=date(2026, 8, 2),
            description="进行中",
        )
        session.add_all([expired, assigned])
        await session.flush()

        # 开放中的任务不得归档
        with pytest.raises(OperationRefused):
            await repository.archive_task(assigned.id, 1)

        archived = await repository.archive_task(expired.id, 1)
        assert archived.archived_at is not None
        assert archived.archived_by_employee_id == 1

        default_ids = {
            task.id
            for task in await repository.list_all_open(offset=0, limit=50)
        }
        assert expired.id not in default_ids
        assert assigned.id in default_ids

        archived_ids = {
            task.id
            for task in await repository.list_all_open(
                offset=0,
                limit=50,
                status=BusinessTaskStatus.EXPIRED,
                archived=True,
            )
        }
        assert archived_ids == {expired.id}

        restored = await repository.restore_task(expired.id, 1)
        assert restored.archived_at is None


@pytest.mark.asyncio
async def test_bulk_archive_covers_filter_and_skips_open_tasks() -> None:
    """批量归档以筛选条件为选择范围，且绝不触碰开放中的任务。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        session.add(PropertyProfile(id=101, title="测试房间"))
        await session.flush()
        repository = SQLAlchemyOperationsRepository(session)

        session.add_all(
            [
                BusinessTask(
                    task_type=BusinessTaskType.CLEANING,
                    status=BusinessTaskStatus.EXPIRED,
                    property_id=101,
                    service_date=date(2026, 8, index),
                    description=f"失效 {index}",
                )
                for index in range(1, 4)
            ]
            + [
                BusinessTask(
                    task_type=BusinessTaskType.CLEANING,
                    status=BusinessTaskStatus.PENDING_ASSIGNMENT,
                    property_id=101,
                    service_date=date(2026, 8, 9),
                    description="仍待分派",
                )
            ]
        )
        await session.flush()

        archived = await repository.archive_matching(
            1, status=BusinessTaskStatus.EXPIRED
        )
        assert archived == 3

        remaining = await repository.list_all_open(offset=0, limit=50)
        assert [task.status for task in remaining] == [
            BusinessTaskStatus.PENDING_ASSIGNMENT
        ]

        # 批量只写一条含数量的汇总审计，不为每条任务各写一条
        summaries = list(
            await session.scalars(
                select(AuditLog).where(AuditLog.action == "business_task_archived")
            )
        )
        assert len(summaries) == 1
        assert summaries[0].details["count"] == 3


@pytest.mark.asyncio
async def test_archived_view_lists_tasks_without_extra_status_filter() -> None:
    """只勾选「查看已归档」就必须列出全部已归档任务。

    归档只接受终态任务，而默认列表在未选状态时会附加「仅开放态」条件；
    两者相与恒为空，导致归档视图看起来永远是空的。
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        session.add(PropertyProfile(id=101, title="测试房间"))
        await session.flush()
        repository = SQLAlchemyOperationsRepository(session)

        session.add_all(
            [
                BusinessTask(
                    task_type=BusinessTaskType.CLEANING,
                    status=status,
                    property_id=101,
                    service_date=date(2026, 8, index + 1),
                    description=f"终态 {index}",
                )
                for index, status in enumerate(
                    (
                        BusinessTaskStatus.EXPIRED,
                        BusinessTaskStatus.CANCELLED,
                        BusinessTaskStatus.COMPLETED,
                    )
                )
            ]
        )
        await session.flush()
        assert await repository.archive_matching(1) == 3

        # 不带任何状态筛选，只看归档
        archived = await repository.list_all_open(
            offset=0, limit=50, archived=True
        )

        assert {task.status for task in archived} == {
            BusinessTaskStatus.EXPIRED,
            BusinessTaskStatus.CANCELLED,
            BusinessTaskStatus.COMPLETED,
        }


@pytest.mark.asyncio
async def test_archive_selected_rejects_whole_batch_when_open_task_included() -> None:
    """勾选中混入开放态任务时整批拒绝，不静默跳过。

    静默跳过会让用户以为全部归档成功、实际漏了几条，比直接报错更难发现。
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        session.add(PropertyProfile(id=101, title="测试房间"))
        await session.flush()
        repository = SQLAlchemyOperationsRepository(session)

        expired = BusinessTask(
            task_type=BusinessTaskType.CLEANING,
            status=BusinessTaskStatus.EXPIRED,
            property_id=101,
            service_date=date(2026, 8, 1),
            description="已失效",
        )
        assigned = BusinessTask(
            task_type=BusinessTaskType.CLEANING,
            status=BusinessTaskStatus.ASSIGNED,
            property_id=101,
            service_date=date(2026, 8, 2),
            description="进行中",
        )
        session.add_all([expired, assigned])
        await session.flush()

        with pytest.raises(OperationRefused) as refused:
            await repository.archive_selected([expired.id, assigned.id], 1)
        # 消息必须点名受阻的任务编号，否则用户不知道该取消勾选哪几条
        assert str(assigned.id) in str(refused.value)

        # 整批拒绝：终态那条也不能被归档
        await session.refresh(expired)
        assert expired.archived_at is None

        assert await repository.archive_selected([expired.id], 1) == 1

        with pytest.raises(OperationRefused):
            await repository.archive_selected([], 1)

        with pytest.raises(LookupError):
            await repository.archive_selected([999999], 1)


@pytest.mark.asyncio
async def test_purge_task_requires_archive_and_keeps_audit() -> None:
    """只有已归档任务可彻底删除，审计记录必须保留。

    删除是本项目第一个不可逆操作：状态在仓储内重新读取校验，不信任调用方；
    审计本就应当比它描述的实体活得久。
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        # 开启外键强制，否则级联删除不会被真正验证
        await session.execute(text("PRAGMA foreign_keys=ON"))
        actor = Employee(wecom_userid="admin", name="管理员", role=EmployeeRole.ADMIN)
        session.add_all([PropertyProfile(id=101, title="测试房间"), actor])
        await session.flush()
        repository = SQLAlchemyOperationsRepository(session)

        task = BusinessTask(
            task_type=BusinessTaskType.CLEANING,
            status=BusinessTaskStatus.EXPIRED,
            property_id=101,
            service_date=date(2026, 8, 1),
            description="待删除",
        )
        session.add(task)
        await session.flush()
        session.add(
            TaskAttachment(
                task_id=task.id,
                private_file_id="abcdef0123456789",
                kind="photo",
            )
        )
        await session.flush()

        # 未归档时拒绝删除
        with pytest.raises(OperationRefused):
            await repository.purge_task(task.id, actor.id)

        await repository.archive_task(task.id, actor.id)
        assert await repository.attachment_file_ids(task.id) == ["abcdef0123456789"]

        await repository.purge_task(task.id, actor.id)
        await session.commit()

        assert await session.get(BusinessTask, task.id) is None
        # 附件行随外键级联消失
        remaining = list(
            await session.scalars(
                select(TaskAttachment).where(TaskAttachment.task_id == task.id)
            )
        )
        assert remaining == []
        # 审计保留，且记录了附件数量
        purged = list(
            await session.scalars(
                select(AuditLog).where(AuditLog.action == "business_task_purged")
            )
        )
        assert len(purged) == 1
        assert purged[0].details["attachments"] == 1


@pytest.mark.asyncio
async def test_bulk_purge_validates_before_touching_any_file() -> None:
    """混入未归档任务时整批拒绝，且校验必须发生在删除磁盘文件之前。

    先删文件再发现某条不该删，照片已经找不回来了。这里断言校验会在返回任何
    待删文件编号之前失败。
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        await session.execute(text("PRAGMA foreign_keys=ON"))
        actor = Employee(wecom_userid="admin", name="管理员", role=EmployeeRole.ADMIN)
        session.add_all([PropertyProfile(id=101, title="测试房间"), actor])
        await session.flush()
        repository = SQLAlchemyOperationsRepository(session)

        def make(description: str) -> BusinessTask:
            """构造一条终态任务。"""
            return BusinessTask(
                task_type=BusinessTaskType.CLEANING,
                status=BusinessTaskStatus.EXPIRED,
                property_id=101,
                service_date=date(2026, 8, 1),
                description=description,
            )

        archived = make("已归档")
        open_task = make("未归档")
        session.add_all([archived, open_task])
        await session.flush()
        session.add(
            TaskAttachment(
                task_id=archived.id,
                private_file_id="fedcba9876543210",
                kind="photo",
            )
        )
        await session.flush()
        await repository.archive_task(archived.id, actor.id)

        # 混入未归档：整批拒绝，且消息点名具体编号
        with pytest.raises(OperationRefused) as refused:
            await repository.require_purgeable([archived.id, open_task.id])
        assert str(open_task.id) in str(refused.value)

        # 空选择同样拒绝
        with pytest.raises(OperationRefused):
            await repository.require_purgeable([])

        # 只选已归档时才返回待删文件
        assert await repository.require_purgeable([archived.id]) == [
            "fedcba9876543210"
        ]

        assert await repository.purge_selected([archived.id], actor.id) == 1
        await session.commit()
        assert await session.get(BusinessTask, archived.id) is None
        # 未归档那条完好无损
        assert await session.get(BusinessTask, open_task.id) is not None


@pytest.mark.asyncio
async def test_bulk_assign_validation_rejects_incomplete_or_wrong_status() -> None:
    """批量分派前整体校验：状态不符或缺房间日期的整批拒绝并点名。

    校验必须先整体做完，否则前几条已改状态才发现后面某条不合格，任务会停在
    半分派状态。返回值携带每条自己的房间与日期——批量覆盖这两项才是危险操作。
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        session.add_all(
            [
                PropertyProfile(id=101, title="房间甲"),
                PropertyProfile(id=102, title="房间乙"),
            ]
        )
        await session.flush()
        repository = SQLAlchemyOperationsRepository(session)

        ready_a = BusinessTask(
            task_type=BusinessTaskType.CLEANING,
            status=BusinessTaskStatus.PENDING_ASSIGNMENT,
            property_id=101,
            service_date=date(2026, 8, 1),
            description="甲",
        )
        ready_b = BusinessTask(
            task_type=BusinessTaskType.CLEANING,
            status=BusinessTaskStatus.PENDING_ASSIGNMENT,
            property_id=102,
            service_date=date(2026, 8, 5),
            description="乙",
        )
        no_property = BusinessTask(
            task_type=BusinessTaskType.MAINTENANCE,
            status=BusinessTaskStatus.PENDING_CONFIRMATION,
            description="缺房间",
        )
        already_done = BusinessTask(
            task_type=BusinessTaskType.CLEANING,
            status=BusinessTaskStatus.EXPIRED,
            property_id=101,
            service_date=date(2026, 8, 1),
            description="已失效",
        )
        session.add_all([ready_a, ready_b, no_property, already_done])
        await session.flush()

        with pytest.raises(OperationRefused) as missing_fields:
            await repository.require_assignable([ready_a.id, no_property.id])
        assert str(no_property.id) in str(missing_fields.value)

        with pytest.raises(OperationRefused) as wrong_status:
            await repository.require_assignable([ready_a.id, already_done.id])
        assert str(already_done.id) in str(wrong_status.value)

        with pytest.raises(OperationRefused):
            await repository.require_assignable([])

        # 合格时各自带回自己的房间与日期，不做统一覆盖
        assert await repository.require_assignable([ready_b.id, ready_a.id]) == [
            (ready_a.id, 101, date(2026, 8, 1)),
            (ready_b.id, 102, date(2026, 8, 5)),
        ]


@pytest.mark.asyncio
async def test_purged_turnover_is_not_recreated_by_the_next_sync() -> None:
    """永久删除的周转任务不会被下一次订单同步造回来。

    删除会把去重依据一并删掉，而 create_turnover 只查现存任务行，于是同一订单
    再同步一次就重新生成待分派任务，已经处理完并被清理的历史工作回流到运营待办。
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        session.add(PropertyProfile(id=101, title="测试房间"))
        session.add(
            Employee(id=1, wecom_userid="admin", name="管理员", role=EmployeeRole.ADMIN)
        )
        await session.flush()
        repository = SQLAlchemyOperationsRepository(session)

        created = await repository.create_turnover(
            property_id=101,
            service_date=date(2026, 8, 1),
        )
        assert created is not None
        created.status = BusinessTaskStatus.COMPLETED
        created.archived_at = datetime(2026, 8, 5, tzinfo=UTC)
        await session.flush()

        await repository.purge_task(created.id, 1)
        await session.commit()

        # 同一来源再同步一次：不重建，也不报错。
        again = await repository.create_turnover(
            property_id=101,
            service_date=date(2026, 8, 1),
        )
        assert again is None

        # 真正不同的服务日仍然照常创建。
        other = await repository.create_turnover(
            property_id=101,
            service_date=date(2026, 8, 2),
        )
        assert other is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_purge_mark_expires_and_stops_blocking() -> None:
    """墓碑只在保留期内挡住重建，过期后同一房间同一天可以重新排活。

    否则删过一次就等于永久封禁某个房间的某一天，一年后真的需要清扫也不会生成。
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        session.add(PropertyProfile(id=101, title="测试房间"))
        await session.flush()
        repository = SQLAlchemyOperationsRepository(session)
        session.add(
            PurgedTaskMark(
                dedupe_key="turnover:101:2026-08-01",
                purged_at=datetime.now(UTC)
                - timedelta(days=PURGED_MARK_RETENTION_DAYS + 1),
            )
        )
        await session.flush()

        recreated = await repository.create_turnover(
            property_id=101,
            service_date=date(2026, 8, 1),
        )

        assert recreated is not None

    await engine.dispose()


async def _purge_setup() -> tuple[object, async_sessionmaker]:
    """创建开启外键的内存库，并准备房间与管理员。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.execute(text("PRAGMA foreign_keys=ON"))
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all(
            [
                PropertyProfile(id=101, title="测试房间"),
                Employee(id=1, wecom_userid="admin", name="管理员", role=EmployeeRole.ADMIN),
            ]
        )
        await session.commit()
    return engine, factory


def _archived(
    *,
    archived_at: datetime,
    dedupe_key: str | None = None,
    service_date: date = date(2026, 8, 1),
) -> BusinessTask:
    """构造一条已归档的终态任务。"""
    return BusinessTask(
        dedupe_key=dedupe_key,
        task_type=BusinessTaskType.CLEANING,
        status=BusinessTaskStatus.COMPLETED,
        property_id=101,
        service_date=service_date,
        description="已归档",
        archived_at=archived_at,
    )


async def _purge_through(entry: str, session, task_ids: list[int]) -> None:
    """按指定入口永久删除任务：单条、人工批量或自动归档清理。"""
    repository = SQLAlchemyOperationsRepository(session)
    if entry == "single":
        for task_id in task_ids:
            await repository.purge_task(task_id, 1)
    elif entry == "manual_batch":
        assert await repository.purge_selected(task_ids, 1) == len(task_ids)
    else:
        assert await SQLAlchemyRetentionRepository(session).purge_archived_tasks() == len(
            task_ids
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["single", "manual_batch", "retention"])
async def test_every_purge_entry_leaves_a_tombstone_that_blocks_turnover(
    entry: str,
) -> None:
    """三种删除入口都给系统任务写原业务键墓碑，同来源同步不再重建。

    周转任务没有附件也要写；人工任务没有业务键，不写。用新会话在提交后验证，
    不同房间日期照常创建。
    """
    engine, factory = await _purge_setup()
    # 自动清理只处理归档满 180 天的任务；另两种入口不看归档时长。
    archived_at = datetime.now(UTC) - timedelta(days=200)

    async with factory() as session:
        turnover = _archived(archived_at=archived_at, dedupe_key="turnover:101:2026-08-01")
        manual = _archived(archived_at=archived_at)
        session.add_all([turnover, manual])
        await session.commit()

    async with factory() as session:
        await _purge_through(entry, session, [turnover.id, manual.id])
        await session.commit()

    async with factory() as session:
        marks = list(await session.scalars(select(PurgedTaskMark.dedupe_key)))
        assert marks == ["turnover:101:2026-08-01"]
        repository = SQLAlchemyOperationsRepository(session)
        assert await repository.create_turnover(
            property_id=101,
            service_date=date(2026, 8, 1),
        ) is None
        assert await repository.create_turnover(
            property_id=101,
            service_date=date(2026, 8, 2),
        ) is not None
        await session.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_bulk_purge_rolls_back_whole_batch_when_any_task_is_ineligible() -> None:
    """混入未归档或缺失任务时整批拒绝：删除、墓碑、审计都不留下。"""
    engine, factory = await _purge_setup()
    archived_at = datetime(2026, 8, 5, tzinfo=UTC)

    async with factory() as session:
        eligible = _archived(archived_at=archived_at, dedupe_key="turnover:101:2026-08-01")
        open_task = BusinessTask(
            dedupe_key="turnover:101:2026-08-02",
            task_type=BusinessTaskType.CLEANING,
            status=BusinessTaskStatus.COMPLETED,
            property_id=101,
            service_date=date(2026, 8, 2),
            description="未归档",
        )
        session.add_all([eligible, open_task])
        await session.commit()

    for selection, error in (
        ([eligible.id, open_task.id], OperationRefused),
        ([eligible.id, 99_999], LookupError),
    ):
        async with factory() as session:
            with pytest.raises(error):
                await SQLAlchemyOperationsRepository(session).purge_selected(selection, 1)
            await session.rollback()

    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(BusinessTask)) == 2
        assert await session.scalar(select(func.count()).select_from(PurgedTaskMark)) == 0
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 0

    await engine.dispose()


@pytest.mark.asyncio
async def test_bulk_purge_refuses_partial_delete_even_if_precheck_is_bypassed(
    monkeypatch,
) -> None:
    """删除语句自己带归档条件并核对返回集合：少删一条也整批失败回滚。

    这里让锁定复核失效，模拟复核与删除之间状态被改掉，验证最后一道防线不会
    把删了一部分当作成功。重复编号只计一次。
    """
    engine, factory = await _purge_setup()
    archived_at = datetime(2026, 8, 5, tzinfo=UTC)

    async with factory() as session:
        first = _archived(archived_at=archived_at, dedupe_key="turnover:101:2026-08-01")
        second = _archived(
            archived_at=archived_at,
            dedupe_key="turnover:101:2026-08-02",
            service_date=date(2026, 8, 2),
        )
        open_task = BusinessTask(
            task_type=BusinessTaskType.CLEANING,
            status=BusinessTaskStatus.COMPLETED,
            property_id=101,
            service_date=date(2026, 8, 3),
            description="未归档",
        )
        session.add_all([first, second, open_task])
        await session.commit()

    async def skip_check(self, task_ids):
        """模拟复核被绕过。"""
        return []

    with monkeypatch.context() as patched:
        patched.setattr(SQLAlchemyOperationsRepository, "require_purgeable", skip_check)
        async with factory() as session:
            with pytest.raises(OperationRefused):
                await SQLAlchemyOperationsRepository(session).purge_selected(
                    [first.id, open_task.id],
                    1,
                )
            await session.rollback()

    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(BusinessTask)) == 3
        assert await session.scalar(select(func.count()).select_from(PurgedTaskMark)) == 0
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 0

    async with factory() as session:
        purged = await SQLAlchemyOperationsRepository(session).purge_selected(
            [second.id, first.id, second.id],
            1,
        )
        await session.commit()
        audit = await session.scalar(
            select(AuditLog).where(AuditLog.action == "business_task_purged")
        )

    assert purged == 2
    assert audit is not None
    assert audit.details == {"count": 2, "task_ids": sorted([first.id, second.id])}

    await engine.dispose()


@pytest.mark.asyncio
async def test_bulk_purge_rereads_archive_state_already_loaded_in_session(
    tmp_path: Path,
) -> None:
    """会话里早已加载的旧 archived_at 不能决定删除：复核必须读最新行版本。

    另一个会话已经把任务恢复出归档并提交，本会话的身份映射里仍是旧对象；
    批量删除必须据最新状态整批拒绝。
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ops.sqlite3'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all(
            [
                PropertyProfile(id=101, title="测试房间"),
                Employee(id=1, wecom_userid="admin", name="管理员", role=EmployeeRole.ADMIN),
            ]
        )
        await session.flush()
        task = _archived(archived_at=datetime(2026, 8, 5, tzinfo=UTC))
        session.add(task)
        await session.commit()

    async with factory() as stale, factory() as other:
        loaded = await stale.get(BusinessTask, task.id)
        assert loaded is not None and loaded.archived_at is not None
        await SQLAlchemyOperationsRepository(other).restore_task(task.id, 1)
        await other.commit()

        # 断言由锁定复核本身拒绝（而不是靠 DELETE 条件兜底），才能证明复核读到
        # 的是最新行版本。
        with pytest.raises(OperationRefused, match="只有已归档的任务可以永久删除"):
            await SQLAlchemyOperationsRepository(stale).require_purgeable([task.id])
        await stale.rollback()

    async with factory() as session:
        assert await session.get(BusinessTask, task.id) is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_tombstone_refresh_never_moves_backwards() -> None:
    """同键重复写墓碑只保留一行，时间只前移不后退；空键被忽略。"""
    engine, factory = await _purge_setup()
    earlier = datetime(2026, 9, 1, tzinfo=UTC)
    later = datetime(2026, 9, 10, tzinfo=UTC)

    async with factory() as session:
        repository = SQLAlchemyOperationsRepository(session)
        await repository.mark_purged_many(["turnover:101:2026-08-01", None, ""], now=later)
        await repository.mark_purged_many(["turnover:101:2026-08-01"], now=earlier)
        await session.commit()

    async with factory() as session:
        marks = list(await session.scalars(select(PurgedTaskMark)))
        assert len(marks) == 1
        assert marks[0].purged_at.replace(tzinfo=UTC) == later
        newest = datetime(2026, 9, 20, tzinfo=UTC)
        await SQLAlchemyOperationsRepository(session).mark_purged_many(
            ["turnover:101:2026-08-01", "turnover:101:2026-08-01"],
            now=newest,
        )
        await session.commit()
        refreshed = await session.scalar(
            select(PurgedTaskMark).execution_options(populate_existing=True)
        )
        assert refreshed is not None
        assert refreshed.purged_at.replace(tzinfo=UTC) == newest

    await engine.dispose()


@pytest.mark.asyncio
async def test_tombstone_just_inside_retention_still_blocks() -> None:
    """离过期还差一分钟的墓碑仍然挡住重建，也不会被顺手清掉。"""
    engine, factory = await _purge_setup()

    async with factory() as session:
        session.add(
            PurgedTaskMark(
                dedupe_key="turnover:101:2026-08-01",
                purged_at=datetime.now(UTC)
                - timedelta(days=PURGED_MARK_RETENTION_DAYS)
                + timedelta(minutes=1),
            )
        )
        await session.commit()

    async with factory() as session:
        assert await SQLAlchemyOperationsRepository(session).create_turnover(
            property_id=101,
            service_date=date(2026, 8, 1),
        ) is None
        await session.commit()
        assert await session.scalar(select(func.count()).select_from(PurgedTaskMark)) == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_tombstones_do_not_suppress_credential_review_tasks() -> None:
    """凭证复核类任务不读墓碑：即使有同键墓碑，必要的人工待办照常生成。"""
    engine, factory = await _purge_setup()

    async with factory() as session:
        repository = SQLAlchemyOperationsRepository(session)
        await repository.mark_purged_many(
            ["credential-failure:7", "credential-review:101:2026-08-01"],
            now=datetime.now(UTC),
        )
        failure = await repository.create_credential_failure_review(
            delivery_id=7,
            reason="timeout",
        )
        review = await repository.create_credential_review(
            property_id=101,
            local_date=date(2026, 8, 1),
            order_ids=[1, 2],
        )
        await session.commit()

    assert failure.dedupe_key == "credential-failure:7"
    assert review.dedupe_key == "credential-review:101:2026-08-01"

    await engine.dispose()
