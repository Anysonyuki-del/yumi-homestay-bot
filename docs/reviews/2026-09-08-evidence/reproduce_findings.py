"""只使用临时文件、内存数据库和假接口复核审查发现。"""

import ast
import asyncio
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from unit.test_credential_delivery import DeliveryRepositoryStub, JobQueueStub, valid_context

from homestay_bot import application
from homestay_bot.db import create_engine, create_session_factory
from homestay_bot.domain.enums import (
    BusinessTaskStatus,
    ConversationMode,
    CredentialDeliveryStatus,
    EmployeeRole,
    JobStatus,
    MessageOrigin,
)
from homestay_bot.domain.models import (
    Base,
    BusinessTask,
    Conversation,
    CredentialDelivery,
    CredentialDeliveryPart,
    Employee,
    Job,
    Message,
    PropertyProfile,
    RoomCredential,
    TaskAttachment,
)
from homestay_bot.integrations.hostex_client import Reservation
from homestay_bot.repositories.jobs import SQLAlchemyJobRepository
from homestay_bot.repositories.operations import SQLAlchemyOperationsRepository
from homestay_bot.services.admin_operations_service import AdminOperationsService
from homestay_bot.services.credential_delivery import (
    CredentialDeliveryService,
    CredentialSafetyRules,
)
from homestay_bot.services.hostex_sync import HostexSyncService
from homestay_bot.services.private_file_storage import PrivateFileStorage
from homestay_bot.services.room_readiness_service import RoomReadinessService
from homestay_bot.services.task_lifecycle_service import TaskLifecycleService

TODAY = date(2026, 8, 2)
NOW = datetime(2026, 8, 2, 9, tzinfo=UTC)


async def new_store():
    """创建启用真实外键约束的隔离数据库。"""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, create_session_factory(engine)


def reservation(check_in, check_out):
    """构造固定订单，日期是当前检查的唯一变量。"""
    return Reservation(
        reservation_code="AUDIT-ORDER",
        stay_code="AUDIT-STAY",
        property_id=101,
        check_in_date=check_in,
        check_out_date=check_out,
        status="confirmed",
        guest_name="虚构测试客户",
        created_at="2026-08-01T00:00:00Z",
    )


async def turnover_and_reschedule_and_purge():
    """验证退房保洁关联、改期后旧任务残留以及删除后的任务重建。"""
    engine, factory = await new_store()
    async with factory() as session:
        repo = SQLAlchemyOperationsRepository(session)
        sync = HostexSyncService(None, repo, task_lifecycle=TaskLifecycleService(repo))
        original = reservation(TODAY - timedelta(days=1), TODAY)
        order = await sync._sync_reservation(original)
        task = await session.scalar(select(BusinessTask))
        assert task.order_id == order.id and task.service_date == order.check_out_date
        staff = Employee(wecom_userid="audit-staff", name="虚构员工", role=EmployeeRole.ADMIN)
        session.add(staff)
        await session.flush()
        task.status = BusinessTaskStatus.PENDING_INSPECTION
        task.assigned_employee_id = staff.id
        task.checklist = {"clean": True, "supplies": True, "damage": True}
        session.add(
            TaskAttachment(
                task_id=task.id,
                private_file_id="audit-photo.png",
                kind="photo",
                uploaded_by=staff.id,
            )
        )
        await session.flush()

        context = replace(
            valid_context(), check_in_date=order.check_in_date, check_out_date=order.check_out_date
        )

        # 除订单日期外，凭证、会话归属、窗口及房态均满足，隔离订单选择的影响。
        class ContextRepo(DeliveryRepositoryStub):
            async def load_context_for_update(self, order_id):
                """记录可入住操作实际使用的订单。"""
                self.selected_order_id = order_id
                return self.context

        delivery_repo, jobs = ContextRepo(context), JobQueueStub()
        await RoomReadinessService(
            repo,
            repo,
            CredentialDeliveryService(
                delivery_repo,
                jobs,
                today=lambda: TODAY,
                now=lambda: NOW,
            ),
        ).mark_ready(task.id, staff)
        assert delivery_repo.selected_order_id == order.id
        assert delivery_repo.exceptions[0]["reason"] == "not_checkin_day" and not jobs.items
        print("turnover_credentials: departing_order_selected, reason=not_checkin_day, send_jobs=0")

        # 去除执行证据，验证最适合自动撤销的待分派旧计划仍被保留。
        task.status = BusinessTaskStatus.PENDING_ASSIGNMENT
        task.assigned_employee_id = None
        task.checklist = {}
        for attachment in (await session.scalars(select(TaskAttachment))).all():
            await session.delete(attachment)
        await session.flush()
        updated = reservation(TODAY - timedelta(days=1), TODAY + timedelta(days=2))
        await sync._sync_reservation(updated)
        tasks = list(
            await session.scalars(select(BusinessTask).order_by(BusinessTask.service_date))
        )
        assert len(tasks) == 2 and all(
            t.status is BusinessTaskStatus.PENDING_ASSIGNMENT for t in tasks
        )
        print(
            "reschedule: both_old_and_new_turnover_tasks_pending",
            [str(t.service_date) for t in tasks],
        )

        current_task = tasks[-1]
        current_task.status = BusinessTaskStatus.COMPLETED
        current_task.archived_at = NOW
        await session.flush()
        await repo.purge_task(current_task.id, staff.id)
        await session.commit()
        await sync._sync_reservation(updated)
        resurrected = await session.scalar(
            select(BusinessTask).where(BusinessTask.service_date == updated.check_out_date)
        )
        assert (
            resurrected is not None and resurrected.status is BusinessTaskStatus.PENDING_ASSIGNMENT
        )
        print("purge_reconcile: deleted_completed_task_recreated_as_pending_assignment")
    await engine.dispose()


async def deletion_commit_failure():
    """模拟数据库提交失败，检查已删照片无法随事务恢复。"""
    engine, factory = await new_store()
    with TemporaryDirectory(prefix="yumi-audit-files-") as temp:
        storage = PrivateFileStorage(Path(temp))
        file_id = "a" * 32 + ".png"
        path = Path(temp) / file_id
        path.write_bytes(b"synthetic test photo")
        async with factory() as session:
            staff = Employee(wecom_userid="audit-admin", name="虚构管理员", role=EmployeeRole.ADMIN)
            session.add(staff)
            await session.flush()
            task = BusinessTask(
                dedupe_key="audit-delete",
                task_type="cleaning",
                status=BusinessTaskStatus.CANCELLED,
                archived_at=NOW,
                description="虚构任务",
            )
            # 枚举必须使用模型枚举值，避免依赖字符串宽容行为。
            from homestay_bot.domain.enums import BusinessTaskType

            task.task_type = BusinessTaskType.CLEANING
            session.add(task)
            await session.flush()
            session.add(
                TaskAttachment(
                    task_id=task.id, private_file_id=file_id, kind="photo", uploaded_by=staff.id
                )
            )
            await session.commit()
            task_id = task.id

        class FailCommitSession(AsyncSession):
            async def commit(self):
                """在删除语句执行后模拟存储或连接提交故障。"""
                raise RuntimeError("injected commit failure")

        fail_factory = async_sessionmaker(engine, class_=FailCommitSession, expire_on_commit=False)
        service = application.SessionTaskPageService(fail_factory, storage, 1024)
        try:
            await service.purge(task_id, staff)
        except RuntimeError as error:
            assert str(error) == "injected commit failure"
        else:
            raise AssertionError("expected injected failure")
        async with factory() as session:
            assert await session.get(BusinessTask, task_id) is not None
            assert await session.scalar(select(func.count(TaskAttachment.id))) == 1
        assert not path.exists()
        print("purge_commit_failure: task_and_attachment_restored, photo_permanently_missing")
    await engine.dispose()


async def stale_final_send():
    """已存在更新的人工消息与接管模式时，让真实 worker 处理旧 final 出站。"""
    engine, factory = await new_store()
    async with factory() as session:
        conversation = Conversation(
            open_kfid="wk-audit", external_userid="wm-audit", mode=ConversationMode.HUMAN_ACTIVE
        )
        session.add(conversation)
        await session.flush()
        session.add_all(
            [
                Message(
                    conversation_id=conversation.id,
                    external_message_id="old-guest",
                    origin=MessageOrigin.GUEST,
                    message_type="text",
                    content="虚构旧问题",
                    sent_at=NOW - timedelta(minutes=2),
                ),
                Message(
                    conversation_id=conversation.id,
                    external_message_id="new-staff",
                    origin=MessageOrigin.SERVICER,
                    message_type="text",
                    content="虚构人工已接管回复",
                    sent_at=NOW - timedelta(minutes=1),
                ),
            ]
        )
        await application.TransactionalOutboxWeCom(
            session,
            source_message_id="old-guest",
            delivery_phase="final",
            source_guest_message_id="old-guest",
        ).send_text("wk-audit", "wm-audit", "虚构过时AI回复")
        await session.commit()
    sent = []

    class FakeWeCom:
        async def send_text(self, *args):
            """仅记录本地调用，绝不访问网络。"""
            sent.append(args)
            return "fake-accepted"

    class StopLoop(BaseException):
        """在一轮处理完成且无待办后退出循环。"""

    async def stop_sleep(_):
        """替代 worker 空闲等待。"""
        raise StopLoop()

    with patch.object(application.asyncio, "sleep", stop_sleep), suppress(StopLoop):
        await application._run_worker_loop(
            SimpleNamespace(state=SimpleNamespace()),
            factory=factory,
            handler=object(),
            wecom=FakeWeCom(),
            recover_stale=False,
        )
    assert len(sent) == 1 and sent[0][2] == "虚构过时AI回复"
    print("queued_final: stale_reply_sent_after_new_staff_message_and_human_takeover")
    await engine.dispose()


def utc_credential_date():
    """固定为武汉零点后、UTC 仍在昨日的时刻，验证默认时钟与业务时钟差异。"""

    class UtcDate(date):
        @classmethod
        def today(cls):
            """模拟 UTC 部署环境的系统日期。"""
            return date(2026, 8, 1)

    now = datetime(2026, 8, 1, 17, tzinfo=UTC)
    context = replace(valid_context(), last_guest_message_at=now)
    with patch("homestay_bot.services.credential_delivery.date", UtcDate):
        default_reason = CredentialSafetyRules(now=lambda: now).invalid_reason(context, 101)
    local_reason = CredentialSafetyRules(today=lambda: TODAY, now=lambda: now).invalid_reason(
        context, 101
    )
    assert default_reason == "not_checkin_day" and local_reason is None
    print("credential_clock: Wuhan_01:00_rejected_by_UTC_default, accepted_by_business_date")


async def main():
    """顺序运行无外联复现并保留每项断言结果。"""
    await turnover_and_reschedule_and_purge()
    await deletion_commit_failure()
    await stale_final_send()
    utc_credential_date()
    await reconciliation_gap()
    await credential_failure_tracking()


async def reconciliation_gap():
    """用遵守入店日期过滤条件的假 Hostex 复核漏单后错误的空置投影。"""
    engine, factory = await new_store()
    active_order = reservation(TODAY - timedelta(days=3), TODAY + timedelta(days=2))
    async with factory() as session:
        session.add(PropertyProfile(id=101, title="虚构房间", is_active=True))
        await session.commit()
    queries, beats = [], []

    class FakeHostex:
        async def list_reservations(self, query):
            """只返回符合查询入店日期窗的订单。"""
            queries.append(query)
            return (
                [active_order]
                if query.start_check_in_date
                <= active_order.check_in_date
                <= query.end_check_in_date
                else []
            )

    class StopLoop(BaseException):
        """退出成功同步后的等待。"""

    async def stop_sleep(_):
        """确保只执行一次对账。"""
        raise StopLoop()

    with patch.object(application.asyncio, "sleep", stop_sleep), suppress(StopLoop):
        await application._run_hostex_reconcile_loop(
            factory=factory,
            hostex=FakeHostex(),
            interval_seconds=900,
            today_provider=lambda: TODAY,
            heartbeat_now=lambda: NOW,
            sync_heartbeat=beats.append,
        )
    async with factory() as session:
        snapshot = await AdminOperationsService(session).snapshot(
            now=NOW, source_synced_at=beats[0]
        )
    assert len(queries) == 1 and beats == [NOW]
    assert snapshot.rooms[0].occupancy_status.value == "vacant" and not snapshot.source_stale
    print("reconcile_window: ongoing_order_excluded, fresh_heartbeat=True, room_display=vacant")
    await engine.dispose()


async def credential_failure_tracking():
    """执行原始异步失败回调和超时恢复，验证凭证状态是否有相应补偿。"""
    from homestay_bot.repositories.lifecycle_reminders import SQLAlchemyLifecycleReminderRepository
    from homestay_bot.services.business_task_service import BusinessTaskService
    from homestay_bot.services.lifecycle_reminders import LifecycleReminderService

    engine, factory = await new_store()
    async with factory() as session:
        order = await SQLAlchemyOperationsRepository(session).upsert_reservation(
            reservation(TODAY, TODAY + timedelta(days=1))
        )
        credential = RoomCredential(
            property_id=101,
            version=1,
            password_ciphertext=b"synthetic",
            guide_ciphertext=b"synthetic",
            qr_file_id="synthetic.png",
            is_active=True,
        )
        session.add(credential)
        await session.flush()
        delivery = CredentialDelivery(
            order_id=order.id, credential_id=credential.id, status=CredentialDeliveryStatus.SENT
        )
        session.add(delivery)
        await session.flush()
        parts = [
            CredentialDeliveryPart(
                delivery_id=delivery.id,
                part_type=kind,
                status=CredentialDeliveryStatus.SENT,
                external_message_id="fake-" + kind,
            )
            for kind in ("guide", "password", "qr")
        ]
        session.add_all(parts)
        await session.commit()
        delivery_id, part_id = delivery.id, parts[1].id

    def build_lifecycle(session, bundle):
        """组装只读失败处理所需的真实仓储，外部发送器不可用。"""
        return LifecycleReminderService(
            SQLAlchemyLifecycleReminderRepository(session),
            SQLAlchemyJobRepository(session),
            None,
            BusinessTaskService(SQLAlchemyOperationsRepository(session)),
        )

    # 仅取出原始嵌套回调，注入其两个闭包依赖，避免启动真实应用及外部客户端。
    tree = ast.parse(Path(application.__file__).read_text())
    callback = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "handle_send_failure"
    )
    namespace = dict(vars(application), factory=factory, build_lifecycle_service=build_lifecycle)
    exec(
        compile(ast.Module(body=[callback], type_ignores=[]), application.__file__, "exec"),
        namespace,
    )
    await namespace["handle_send_failure"](
        "fake-password", 4, SimpleNamespace(agent_id=1, duty_userids=("audit-staff",))
    )
    async with factory() as session:
        part = await session.get(CredentialDeliveryPart, part_id)
        delivery = await session.get(CredentialDelivery, delivery_id)
        assert (
            part.status is CredentialDeliveryStatus.SENT
            and delivery.status is CredentialDeliveryStatus.SENT
        )
        assert await session.scalar(select(func.count(BusinessTask.id))) == 0
        assert await session.scalar(select(func.count(Job.id))) == 0
        print(
            "credential_async_failure: fail_type=4 ignored, "
            "part_and_delivery_still_sent, manual_tasks=0"
        )
        part.status = CredentialDeliveryStatus.PENDING
        delivery.status = CredentialDeliveryStatus.PENDING
        repo = SQLAlchemyJobRepository(session)
        job = await repo.enqueue(
            "credential_send_part", {"part_id": part_id}, dedupe_key="audit-stale-credential"
        )
        job.status = JobStatus.RUNNING
        job.attempts = 1
        job.locked_at = NOW - timedelta(minutes=10)
        await session.commit()
        job_id = job.id
    async with factory() as session:
        await SQLAlchemyJobRepository(session).recover_stale(before=NOW - timedelta(minutes=5))
        await session.commit()
    async with factory() as session:
        job = await session.get(Job, job_id)
        part = await session.get(CredentialDeliveryPart, part_id)
        assert job.status is JobStatus.FAILED and part.status is CredentialDeliveryStatus.PENDING
        assert await session.scalar(select(func.count(BusinessTask.id))) == 0
        print("credential_stale_job: job=failed, part=pending, manual_tasks=0")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
