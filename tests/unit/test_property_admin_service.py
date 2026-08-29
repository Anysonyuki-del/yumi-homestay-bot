from datetime import date

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import (
    BusinessTaskStatus,
    BusinessTaskType,
    EmployeeRole,
    RoomOperationalStatus,
)
from homestay_bot.domain.models import (
    Base,
    BusinessTask,
    Employee,
    PropertyProfile,
    RoomCredential,
    RoomOperationalState,
    StayOrder,
)
from homestay_bot.services.property_admin_service import PropertyAdminService
from homestay_bot.services.sensitive_data import SensitiveDataCipher


@pytest.mark.asyncio
async def test_property_list_projects_local_operational_health() -> None:
    """列表聚合今日住宿、房态、开放任务、凭证和下一次入住。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    cipher = SensitiveDataCipher(Fernet.generate_key().decode("ascii"))

    async with factory() as session:
        admin = Employee(
            wecom_userid="property-overview-admin",
            name="管理员",
            role=EmployeeRole.ADMIN,
        )
        room = PropertyProfile(
            id=101,
            title="长江中心",
            room_number="101",
            room_type="江景大床房",
            district="武昌区",
            address_hint="地铁站附近",
            parking_instructions=None,
        )
        session.add_all([admin, room])
        await session.flush()
        session.add_all(
            [
                RoomOperationalState(
                    property_id=101,
                    status=RoomOperationalStatus.READY,
                ),
                StayOrder(
                    hostex_reservation_code="today-arrival",
                    stay_code="today-arrival",
                    property_id=101,
                    check_in_date=date(2026, 8, 29),
                    check_out_date=date(2026, 8, 31),
                    status="confirmed",
                ),
                StayOrder(
                    hostex_reservation_code="next-arrival",
                    stay_code="next-arrival",
                    property_id=101,
                    check_in_date=date(2026, 9, 8),
                    check_out_date=date(2026, 9, 10),
                    status="confirmed",
                ),
                BusinessTask(
                    task_type=BusinessTaskType.CLEANING,
                    status=BusinessTaskStatus.ASSIGNED,
                    property_id=101,
                    service_date=date(2026, 8, 29),
                    description="退房保洁",
                ),
                RoomCredential(
                    property_id=101,
                    version=4,
                    password_ciphertext=b"cipher-password",
                    guide_ciphertext=b"cipher-guide",
                    qr_file_id="a" * 32 + ".png",
                    is_active=True,
                ),
            ]
        )
        await session.flush()

        items = await PropertyAdminService(
            session,
            cipher,
            today=lambda: date(2026, 8, 29),
        ).list_all(admin)

        assert len(items) == 1
        item = items[0]
        assert item.room_number == "101"
        assert item.operational_status is RoomOperationalStatus.READY
        assert item.today_stay_labels == ("今日入住",)
        assert item.open_task_count == 1
        assert item.credential_version == 4
        assert item.profile_completeness == 83
        assert item.missing_profile_labels == ("停车说明",)
        assert item.next_check_in_date == date(2026, 9, 8)

    await engine.dispose()


@pytest.mark.asyncio
async def test_property_detail_never_projects_credential_plaintext() -> None:
    """详情投影只携带凭证元数据，不提供任何密文字段或解密结果。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    cipher = SensitiveDataCipher(Fernet.generate_key().decode("ascii"))

    async with factory() as session:
        admin = Employee(
            wecom_userid="property-detail-admin",
            name="管理员",
            role=EmployeeRole.ADMIN,
        )
        session.add_all(
            [
                admin,
                PropertyProfile(id=101, title="长江中心"),
                RoomCredential(
                    property_id=101,
                    version=2,
                    password_ciphertext=b"never-project-password",
                    guide_ciphertext=b"never-project-guide",
                    qr_file_id="b" * 32 + ".png",
                    is_active=True,
                ),
            ]
        )
        await session.flush()

        detail = await PropertyAdminService(
            session,
            cipher,
            today=lambda: date(2026, 8, 29),
        ).detail_for(101, admin)

        assert detail["credential"].version == 2
        assert not hasattr(detail["credential"], "password_ciphertext")
        assert not hasattr(detail["credential"], "guide_ciphertext")
        assert "never-project" not in repr(detail)

    await engine.dispose()
