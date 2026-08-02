from datetime import UTC, date, datetime

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import (
    BusinessTaskStatus,
    BusinessTaskType,
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
    CustomerIdentity,
    Employee,
    Job,
    Message,
    PropertyProfile,
    RoomCredential,
    RoomOperationalState,
    StayOrder,
)
from homestay_bot.repositories.credentials import (
    SQLAlchemyCredentialDeliveryRepository,
)
from homestay_bot.repositories.jobs import SQLAlchemyJobRepository
from homestay_bot.services.credential_delivery import CredentialDeliveryService
from homestay_bot.services.sensitive_data import SensitiveDataCipher


@pytest.mark.asyncio
async def test_credential_exception_unique_race_preserves_outer_transaction(
    monkeypatch,
) -> None:
    """凭证异常任务唯一键竞争不能破坏 worker 的外层事务。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        session.add(PropertyProfile(id=901, title="并发测试房间"))
        existing = BusinessTask(
            dedupe_key="credential-exception:77:send_result_uncertain",
            task_type=BusinessTaskType.MANUAL_CONTACT,
            status=BusinessTaskStatus.PENDING_CONFIRMATION,
            property_id=901,
            description="竞争方异常任务",
        )
        session.add(existing)
        await session.commit()

    async with factory() as session:
        session.add(
            AuditLog(
                actor_employee_id=None,
                action="credential_outer_marker",
                target_type="test",
                target_id="credential-race",
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
        await SQLAlchemyCredentialDeliveryRepository(session).record_exception(
            order_id=None,
            property_id=901,
            source_task_id=77,
            reason="send_result_uncertain",
        )
        await session.commit()

        assert await session.scalar(
            select(AuditLog.id).where(
                AuditLog.action == "credential_outer_marker"
            )
        ) is not None
        assert await session.scalar(
            select(func.count(BusinessTask.id)).where(
                BusinessTask.dedupe_key
                == "credential-exception:77:send_result_uncertain"
            )
        ) == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_real_repository_creates_only_three_safe_part_jobs() -> None:
    """真实仓储重复评估时只保留三项任务且队列不含凭证明文。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    cipher = SensitiveDataCipher(Fernet.generate_key().decode("ascii"))

    async with factory() as session:
        admin = Employee(
            wecom_userid="credential-admin",
            name="管理员",
            role=EmployeeRole.ADMIN,
        )
        customer = Customer(display_name="入住客户")
        room = PropertyProfile(id=101, title="长江中心")
        session.add_all([admin, customer, room])
        await session.flush()
        conversation = Conversation(
            customer_id=customer.id,
            open_kfid="wk-1",
            external_userid="wm-1",
        )
        identity = CustomerIdentity(
            customer_id=customer.id,
            provider=CustomerIdentityProvider.WECOM_KF,
            external_id="wm-1",
            is_verified=True,
        )
        order = StayOrder(
            hostex_reservation_code="R-CREDENTIAL-1",
            stay_code="S-CREDENTIAL-1",
            customer_id=customer.id,
            property_id=101,
            check_in_date=date(2026, 8, 2),
            check_out_date=date(2026, 8, 3),
            status="confirmed",
        )
        credential = RoomCredential(
            property_id=101,
            version=3,
            password_ciphertext=cipher.encrypt(
                "839201",
                purpose="room_password",
            ),
            guide_ciphertext=cipher.encrypt(
                "入住指南正文",
                purpose="checkin_guide",
            ),
            qr_file_id="a" * 32 + ".png",
            is_active=True,
        )
        session.add_all(
            [
                conversation,
                identity,
                order,
                credential,
                RoomOperationalState(
                    property_id=101,
                    status=RoomOperationalStatus.READY,
                    version=1,
                ),
            ]
        )
        await session.flush()
        session.add(
            Message(
                conversation_id=conversation.id,
                external_message_id="guest-window-1",
                origin=MessageOrigin.GUEST,
                message_type="text",
                content="我到了",
                sent_at=datetime(2026, 8, 2, 8, tzinfo=UTC),
            )
        )
        await session.flush()
        repository = SQLAlchemyCredentialDeliveryRepository(session)
        service = CredentialDeliveryService(
            repository,
            SQLAlchemyJobRepository(session),
            today=lambda: date(2026, 8, 2),
            now=lambda: datetime(2026, 8, 2, 9, tzinfo=UTC),
        )

        first = await service.evaluate(
            order_id=order.id,
            expected_property_id=101,
            source_task_id=9,
        )
        second = await service.evaluate(
            order_id=order.id,
            expected_property_id=101,
            source_task_id=9,
        )
        await session.commit()

        delivery_count = await session.scalar(
            select(func.count(CredentialDelivery.id))
        )
        parts = list(
            (
                await session.scalars(
                    select(CredentialDeliveryPart).order_by(
                        CredentialDeliveryPart.id
                    )
                )
            ).all()
        )
        jobs = list(
            (
                await session.scalars(
                    select(Job).where(
                        Job.job_type == "credential_send_part"
                    )
                )
            ).all()
        )
        audits = list(
            (
                await session.scalars(
                    select(AuditLog).where(
                        AuditLog.action.like("credential%")
                    )
                )
            ).all()
        )

        assert first is not None
        assert second is not None
        assert first.id == second.id
        assert delivery_count == 1
        assert [item.part_type for item in parts] == [
            "guide",
            "password",
            "qr",
        ]
        assert all(
            item.status is CredentialDeliveryStatus.PENDING
            for item in parts
        )
        assert len(jobs) == 3
        assert all(set(item.payload) == {"part_id"} for item in jobs)
        serialized = str(
            [item.payload for item in jobs]
            + [item.details for item in audits]
        )
        assert "839201" not in serialized
        assert "入住指南正文" not in serialized
        assert "wm-1" not in serialized

    await engine.dispose()
