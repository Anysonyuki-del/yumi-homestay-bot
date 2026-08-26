from datetime import UTC, date, datetime, timedelta
from io import BytesIO

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import (
    BusinessTaskStatus,
    CredentialDeliveryStatus,
    CustomerIdentityProvider,
    EmployeeRole,
    MessageOrigin,
    RoomOperationalStatus,
)
from homestay_bot.domain.models import (
    AuditLog,
    Base,
    BusinessTask,
    Conversation,
    CredentialDelivery,
    CredentialDeliveryPart,
    Customer,
    CustomerContextSummary,
    CustomerIdentity,
    Employee,
    Job,
    Message,
    RoomCredential,
    StayOrder,
)
from homestay_bot.integrations.hostex_client import Reservation
from homestay_bot.repositories.context import SQLAlchemyContextRepository
from homestay_bot.repositories.credentials import (
    SQLAlchemyCredentialDeliveryRepository,
)
from homestay_bot.repositories.jobs import SQLAlchemyJobRepository
from homestay_bot.repositories.lifecycle_reminders import (
    SQLAlchemyLifecycleReminderRepository,
)
from homestay_bot.repositories.operations import (
    SQLAlchemyOperationsRepository,
)
from homestay_bot.services.business_task_service import BusinessTaskService
from homestay_bot.services.context_retention import (
    ContextRetentionService,
    ContextSummaryResult,
)
from homestay_bot.services.credential_delivery import (
    CredentialDeliveryService,
    CredentialPartSender,
)
from homestay_bot.services.hostex_sync import HostexSyncService
from homestay_bot.services.lifecycle_reminders import LifecycleReminderService
from homestay_bot.services.private_file_storage import PrivateFileStorage
from homestay_bot.services.room_readiness_service import RoomReadinessService
from homestay_bot.services.sensitive_data import SensitiveDataCipher
from homestay_bot.services.task_page_service import TaskPageService

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00"
    b"\x90wS\xde"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


class HostexStub:
    """返回一笔固定百居易订单。"""

    def __init__(self, reservation: Reservation) -> None:
        """保存测试订单。"""
        self.reservation = reservation

    async def list_reservations(self, query):
        """按事件订单号返回唯一结果。"""
        assert query.reservation_code == self.reservation.reservation_code
        return [self.reservation]


class ReminderSenderStub:
    """生命周期计划测试不执行真实企业微信写入。"""

    async def send_text(self, open_kfid, external_userid, content):
        """若被意外调用则返回固定编号。"""
        return "lifecycle-message"


class CredentialWeComStub:
    """记录三个凭证部件的安全发送顺序。"""

    def __init__(self) -> None:
        """初始化文本、素材和图片调用列表。"""
        self.texts: list[str] = []
        self.images: list[str] = []

    async def send_text(self, open_kfid, external_userid, content):
        """记录指南或密码正文并返回平台编号。"""
        assert open_kfid == "wk-phase-one"
        assert external_userid == "wm-phase-one"
        self.texts.append(content)
        return f"text-{len(self.texts)}"

    async def upload_temporary_image(self, content, *, content_type):
        """验证二维码来自私有 PNG 文件。"""
        assert content == PNG_BYTES
        assert content_type == "image/png"
        return "media-phase-one"

    async def send_image(self, open_kfid, external_userid, media_id):
        """记录二维码素材发送并返回平台编号。"""
        assert media_id == "media-phase-one"
        self.images.append(media_id)
        return "image-1"


class SummaryStub:
    """为七天上下文测试返回不含身份的固定摘要。"""

    async def summarize(self, *, tier, existing_summary, messages):
        """按层级合并测试消息。"""
        return ContextSummaryResult(
            summary=f"{tier}：" + "；".join(item.content for item in messages),
            unresolved_items=[],
        )


@pytest.mark.asyncio
async def test_phase_one_order_to_ready_and_credentials_flow(
    tmp_path,
) -> None:
    """Webhook、任务、可入住和三项凭证应形成一条完整安全链路。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    today = date(2026, 8, 2)
    now = datetime(2026, 8, 2, 9, tzinfo=UTC)
    cipher = SensitiveDataCipher(Fernet.generate_key().decode("ascii"))
    storage = PrivateFileStorage(tmp_path / "private")
    stored_qr = await storage.save_image(
        BytesIO(PNG_BYTES),
        "image/png",
        1024,
    )

    async with factory() as session:
        operations = SQLAlchemyOperationsRepository(session)
        first_event = await operations.record_hostex_event(
            event_key="phase-one-event",
            event_type="reservation_updated",
            reservation_code="R-PHASE-ONE",
            payload={"reservation_code": "R-PHASE-ONE"},
        )
        duplicate_event = await operations.record_hostex_event(
            event_key="phase-one-event",
            event_type="reservation_updated",
            reservation_code="R-PHASE-ONE",
            payload={"reservation_code": "R-PHASE-ONE"},
        )
        reservation = Reservation(
            reservation_code="R-PHASE-ONE",
            stay_code="S-PHASE-ONE",
            property_id=101,
            check_in_date=today,
            check_out_date=today + timedelta(days=1),
            status="confirmed",
            guest_name="一期测试客户",
            created_at="2026-08-01T00:00:00Z",
        )
        lifecycle = LifecycleReminderService(
            SQLAlchemyLifecycleReminderRepository(session),
            SQLAlchemyJobRepository(session),
            ReminderSenderStub(),
            BusinessTaskService(operations),
            now_provider=lambda: now,
        )
        sync = HostexSyncService(
            HostexStub(reservation),
            operations,
            lifecycle=lifecycle,
        )

        await sync.handle_event("phase-one-event")
        assert first_event is True
        assert duplicate_event is False

        order = await session.scalar(
            select(StayOrder).where(
                StayOrder.hostex_reservation_code == "R-PHASE-ONE"
            )
        )
        assert order is not None
        customer_id = order.customer_id
        assert customer_id is not None
        conversation = Conversation(
            customer_id=customer_id,
            open_kfid="wk-phase-one",
            external_userid="wm-phase-one",
        )
        staff = Employee(
            wecom_userid="phase-one-staff",
            name="执行员工",
            role=EmployeeRole.STAFF,
        )
        admin = Employee(
            wecom_userid="phase-one-admin",
            name="管理员",
            role=EmployeeRole.ADMIN,
        )
        session.add_all([conversation, staff, admin])
        await session.flush()
        session.add_all(
            [
                CustomerIdentity(
                    customer_id=customer_id,
                    provider=CustomerIdentityProvider.WECOM_KF,
                    external_id="wm-phase-one",
                    is_verified=True,
                ),
                Message(
                    conversation_id=conversation.id,
                    external_message_id="guest-phase-one",
                    origin=MessageOrigin.GUEST,
                    message_type="text",
                    content="我已经到附近了",
                    sent_at=now - timedelta(hours=1),
                ),
                RoomCredential(
                    property_id=101,
                    version=1,
                    password_ciphertext=cipher.encrypt(
                        "839201",
                        purpose="room_password",
                    ),
                    guide_ciphertext=cipher.encrypt(
                        "请按入住指南操作",
                        purpose="checkin_guide",
                    ),
                    qr_file_id=stored_qr.file_id,
                    is_active=True,
                ),
            ]
        )
        await session.flush()

        task = await session.scalar(
            select(BusinessTask).where(
                BusinessTask.order_id == order.id,
                BusinessTask.description == "退房后周转保洁",
            )
        )
        assert task is not None
        task_service = BusinessTaskService(operations)
        pages = TaskPageService(operations, task_service)
        await pages.assign(
            task.id,
            admin,
            assigned_employee_id=staff.id,
            property_id=101,
            service_date=today,
        )
        await pages.transition(task.id, staff, "in_progress")
        await operations.update_task_checklist(
            task_id=task.id,
            employee_id=staff.id,
            checklist={
                "clean": True,
                "supplies": True,
                "damage": True,
            },
        )
        await operations.add_task_attachment(
            task_id=task.id,
            file_id="phase-one-photo.png",
            uploaded_by=staff.id,
        )
        await pages.transition(task.id, staff, "pending_inspection")

        credential_repository = SQLAlchemyCredentialDeliveryRepository(
            session
        )
        credential_service = CredentialDeliveryService(
            credential_repository,
            SQLAlchemyJobRepository(session),
            today=lambda: today,
            now=lambda: now,
        )
        readiness = RoomReadinessService(
            operations,
            operations,
            credential_service,
        )
        state = await readiness.mark_ready(task.id, staff)
        await session.flush()

        part_jobs = list(
            (
                await session.scalars(
                    select(Job)
                    .where(Job.job_type == "credential_send_part")
                    .order_by(Job.id)
                )
            ).all()
        )
        wecom = CredentialWeComStub()
        sender = CredentialPartSender(
            credential_repository,
            wecom,
            cipher,
            storage,
            today=lambda: today,
            now=lambda: now,
        )
        for job in part_jobs:
            await sender.handle(job.payload)
        await session.commit()

        delivery = await session.scalar(select(CredentialDelivery))
        parts = list(
            (
                await session.scalars(
                    select(CredentialDeliveryPart).order_by(
                        CredentialDeliveryPart.id
                    )
                )
            ).all()
        )
        task_count = await session.scalar(
            select(func.count(BusinessTask.id)).where(
                BusinessTask.order_id == order.id,
                BusinessTask.description == "退房后周转保洁",
            )
        )
        audit_text = str(
            [
                audit.details
                for audit in (
                    await session.scalars(select(AuditLog))
                ).all()
            ]
        )

        assert state.status is RoomOperationalStatus.READY
        assert task.status is BusinessTaskStatus.PENDING_INSPECTION
        assert task_count == 1
        assert len(part_jobs) == 3
        assert delivery is not None
        assert delivery.status is CredentialDeliveryStatus.SENT
        assert all(
            part.status is CredentialDeliveryStatus.SENT
            for part in parts
        )
        assert wecom.texts == [
            "请按入住指南操作",
            "门锁密码：839201",
        ]
        assert wecom.images == ["media-phase-one"]
        assert "839201" not in str([job.payload for job in part_jobs])
        assert "839201" not in audit_text

    await engine.dispose()


@pytest.mark.asyncio
async def test_two_customer_contexts_remain_isolated_across_seven_days() -> None:
    """维护一位客户摘要时不得读取或清理另一位客户的消息。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 2, 9, tzinfo=UTC)

    async with factory() as session:
        first = Customer(display_name="客户甲")
        second = Customer(display_name="客户乙")
        session.add_all([first, second])
        await session.flush()
        first_conversation = Conversation(
            customer_id=first.id,
            open_kfid="wk-1",
            external_userid="wm-1",
        )
        second_conversation = Conversation(
            customer_id=second.id,
            open_kfid="wk-2",
            external_userid="wm-2",
        )
        session.add_all([first_conversation, second_conversation])
        await session.flush()
        first_old = Message(
            conversation_id=first_conversation.id,
            external_message_id="first-old",
            origin=MessageOrigin.GUEST,
            message_type="text",
            content="客户甲七天前偏好安静",
            sent_at=now - timedelta(days=8),
        )
        second_old = Message(
            conversation_id=second_conversation.id,
            external_message_id="second-old",
            origin=MessageOrigin.GUEST,
            message_type="text",
            content="客户乙七天前需要停车",
            sent_at=now - timedelta(days=8),
        )
        session.add_all([first_old, second_old])
        await session.flush()
        service = ContextRetentionService(
            SQLAlchemyContextRepository(session),
            SummaryStub(),
        )

        await service.maintain_customer(first.id, now)
        await session.commit()

        first_summary = await session.scalar(
            select(CustomerContextSummary).where(
                CustomerContextSummary.customer_id == first.id
            )
        )
        second_summary = await session.scalar(
            select(CustomerContextSummary).where(
                CustomerContextSummary.customer_id == second.id
            )
        )

        assert first_summary is not None
        assert "客户甲" in first_summary.long_summary
        assert "客户乙" not in first_summary.long_summary
        assert first_old.content is None
        assert second_old.content == "客户乙七天前需要停车"
        assert second_summary is None

    await engine.dispose()
