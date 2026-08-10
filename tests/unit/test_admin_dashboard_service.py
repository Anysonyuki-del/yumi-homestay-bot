from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import (
    ApprovalStatus,
    BusinessTaskStatus,
    BusinessTaskType,
    RoomOperationalStatus,
)
from homestay_bot.domain.models import (
    Base,
    BookingApproval,
    BusinessTask,
    Conversation,
    PropertyProfile,
    RoomOperationalState,
    StayOrder,
)
from homestay_bot.services.admin_dashboard_service import AdminDashboardService


async def _factory() -> tuple[object, async_sessionmaker[AsyncSession]]:
    """创建每个测试独立的内存数据库。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def test_snapshot_uses_wuhan_day_and_only_returns_safe_room_facts() -> None:
    """UTC 前一日傍晚应按武汉次日聚合，且不返回客人或平台标识。"""
    engine, factory = await _factory()
    async with factory() as session:
        room = PropertyProfile(id=7, room_number="0701", title="江景大床房", is_active=True)
        session.add_all(
            [
                room,
                RoomOperationalState(property_id=7, status=RoomOperationalStatus.READY),
                StayOrder(
                    hostex_reservation_code="sensitive-reservation",
                    stay_code="sensitive-stay-code",
                    property_id=7,
                    check_in_date=datetime(2026, 8, 11).date(),
                    check_out_date=datetime(2026, 8, 12).date(),
                    status="confirmed",
                ),
                BusinessTask(
                    task_type=BusinessTaskType.CLEANING,
                    status=BusinessTaskStatus.PENDING_ASSIGNMENT,
                    property_id=7,
                    service_date=datetime(2026, 8, 11).date(),
                    description="请联系 guest-secret",
                ),
            ]
        )
        conversation = Conversation(open_kfid="kf-secret", external_userid="uid-secret")
        session.add(conversation)
        await session.flush()
        session.add(
            BookingApproval(
                approval_code="approval-secret",
                conversation_id=conversation.id,
                status=ApprovalStatus.PENDING,
                check_in_date=datetime(2026, 8, 11).date(),
                check_out_date=datetime(2026, 8, 12).date(),
                number_of_guests=2,
                guest_name="敏感姓名",
                guest_mobile="13800000000",
                room_type_preference="大床房",
            )
        )
        await session.commit()

        snapshot = await AdminDashboardService(session).snapshot(
            datetime(2026, 8, 10, 16, 30, tzinfo=UTC)
        )

    assert snapshot.local_date.isoformat() == "2026-08-11"
    assert snapshot.check_in_count == 1
    assert snapshot.check_out_count == 0
    assert snapshot.pending_task_count == 1
    assert snapshot.pending_approval_count == 1
    assert snapshot.room_status_counts[RoomOperationalStatus.READY] == 1
    assert snapshot.arrivals[0].room_title == "江景大床房"
    assert "secret" not in repr(snapshot)
    assert "13800000000" not in repr(snapshot)
    await engine.dispose()  # type: ignore[attr-defined]


async def test_snapshot_handles_empty_database() -> None:
    """没有运营数据时也应返回完整零值快照。"""
    engine, factory = await _factory()
    async with factory() as session:
        snapshot = await AdminDashboardService(session).snapshot(
            datetime(2026, 8, 11, tzinfo=UTC)
        )

    assert snapshot.check_in_count == 0
    assert snapshot.check_out_count == 0
    assert snapshot.active_room_count == 0
    assert snapshot.pending_task_count == 0
    assert snapshot.pending_approval_count == 0
    assert snapshot.manual_attention_count == 0
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "terminal_status",
    ["canceled", "cancelled", "declined", "expired", "deleted"],
)
async def test_snapshot_excludes_each_normalized_terminal_stay_status(
    terminal_status: str,
) -> None:
    """今日动线不得计入大小写或空白变体的终止订单。"""
    engine, factory = await _factory()
    async with factory() as session:
        session.add(PropertyProfile(id=7, title="测试房", is_active=True))
        await session.flush()
        session.add_all(
            [
                StayOrder(
                    hostex_reservation_code=f"terminal-{terminal_status}",
                    stay_code=f"terminal-{terminal_status}",
                    property_id=7,
                    check_in_date=datetime(2026, 8, 11).date(),
                    check_out_date=datetime(2026, 8, 11).date(),
                    status=f"  {terminal_status.upper()}  ",
                ),
                StayOrder(
                    hostex_reservation_code=f"active-{terminal_status}",
                    stay_code=f"active-{terminal_status}",
                    property_id=7,
                    check_in_date=datetime(2026, 8, 11).date(),
                    check_out_date=datetime(2026, 8, 11).date(),
                    status="confirmed",
                ),
            ]
        )
        await session.commit()

        snapshot = await AdminDashboardService(session).snapshot(
            datetime(2026, 8, 10, 16, 30, tzinfo=UTC)
        )

    assert snapshot.check_in_count == 1
    assert snapshot.check_out_count == 1
    await engine.dispose()  # type: ignore[attr-defined]
