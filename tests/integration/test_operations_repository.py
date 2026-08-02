from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import (
    BusinessTaskStatus,
    BusinessTaskType,
    EmployeeRole,
    RoomOperationalStatus,
)
from homestay_bot.domain.models import (
    AuditLog,
    Base,
    BusinessTask,
    Customer,
    Employee,
    HostexWebhookEvent,
    Job,
    PropertyProfile,
    RoomOperationalState,
    StayOrder,
    TaskAttachment,
)
from homestay_bot.integrations.hostex_client import Reservation
from homestay_bot.repositories.operations import SQLAlchemyOperationsRepository
from homestay_bot.services.business_task_service import BusinessTaskService
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
