from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import func, select, text, update
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
from homestay_bot.repositories.admin_dashboard import SQLAlchemyAdminDashboardRepository
from homestay_bot.services.admin_dashboard_service import AdminDashboardService, Snapshot


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
                guest_name_ciphertext=b"encrypted-name",
                guest_mobile_ciphertext=b"encrypted-mobile",
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


class ConsistentReadSpy:
    """记录一致读准备动作。"""

    def __init__(self, events: list[str]) -> None:
        """共享调用顺序列表。"""
        self.events = events

    async def prepare_consistent_read(self) -> None:
        """记录事务准备。"""
        self.events.append("prepare")


class OrderedDashboardService(AdminDashboardService):
    """用可观察的零值步骤隔离查询顺序。"""

    def __init__(self, events: list[str]) -> None:
        """注入一致读 spy，并以空会话占位。"""
        super().__init__(cast(AsyncSession, object()), consistent_read=ConsistentReadSpy(events))
        self.events = events

    async def _stays_for(self, local_date: date, *, arrival: bool):
        """记录入住或退房查询。"""
        self.events.append("arrival" if arrival else "departure")
        return ()

    async def _room_counts(self):
        """记录房态查询。"""
        self.events.append("rooms")
        return {status: 0 for status in RoomOperationalStatus}

    async def _count_pending_tasks(self) -> int:
        """记录待办查询。"""
        self.events.append("tasks")
        return 0

    async def _count_pending_approvals(self) -> int:
        """记录审批查询。"""
        self.events.append("approvals")
        return 0

    async def _count_manual_attention(self) -> int:
        """记录人工事项查询。"""
        self.events.append("manual")
        return 0

    async def _count_overdue_tasks(self, local_date: date) -> int:
        """记录逾期任务查询。"""
        self.events.append("overdue")
        return 0


async def test_snapshot_prepares_consistent_read_before_any_query() -> None:
    """一致读事务设置必须是总览服务的第一项数据库动作。"""
    events: list[str] = []

    await OrderedDashboardService(events).snapshot(datetime(2026, 8, 11, tzinfo=UTC))

    assert events == [
        "prepare",
        "arrival",
        "departure",
        "rooms",
        "tasks",
        "approvals",
        "manual",
        "overdue",
    ]


async def test_sqlite_consistent_read_keeps_snapshot_during_concurrent_commit(
    tmp_path: Path,
) -> None:
    """SQLite WAL 下其他连接提交后，读事务仍应看到同一旧快照。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'snapshot.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.execute(text("PRAGMA journal_mode=WAL"))
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as seed:
        seed.add(PropertyProfile(id=7, title="测试房", is_active=True))
        await seed.flush()
        seed.add(
            StayOrder(
                hostex_reservation_code="snapshot-order",
                stay_code="snapshot-order",
                property_id=7,
                check_in_date=date(2026, 8, 11),
                check_out_date=date(2026, 8, 12),
                status="confirmed",
            )
        )
        await seed.commit()

    async with factory() as reader, factory() as writer:
        await SQLAlchemyAdminDashboardRepository(reader).prepare_consistent_read()
        arrivals = await reader.scalar(
            select(func.count(StayOrder.id)).where(StayOrder.check_in_date == date(2026, 8, 11))
        )
        await writer.execute(
            update(StayOrder).values(check_out_date=date(2026, 8, 11))
        )
        await writer.commit()
        departures = await reader.scalar(
            select(func.count(StayOrder.id)).where(StayOrder.check_out_date == date(2026, 8, 11))
        )

    assert arrivals == 1
    assert departures == 0
    await engine.dispose()


class _Dialect:
    """提供测试所需的数据库方言名。"""

    name = "postgresql"


class _Bind:
    """提供测试所需的 PostgreSQL bind。"""

    dialect = _Dialect()


class PostgreSQLSessionSpy:
    """记录 PostgreSQL 一致读的第一条 SQL。"""

    def __init__(self) -> None:
        """初始化语句记录。"""
        self.statements: list[str] = []

    def in_transaction(self) -> bool:
        """模拟尚未自动开启事务的新会话。"""
        return False

    def get_bind(self) -> _Bind:
        """返回 PostgreSQL 方言。"""
        return _Bind()

    async def execute(self, statement: Any) -> None:
        """记录待执行 SQL 文本。"""
        self.statements.append(str(statement))


async def test_postgresql_consistent_read_sets_read_only_repeatable_read_first() -> None:
    """PostgreSQL 新事务第一句必须设置短只读可重复读。"""
    session = PostgreSQLSessionSpy()

    await SQLAlchemyAdminDashboardRepository(
        cast(AsyncSession, session)
    ).prepare_consistent_read()

    assert session.statements == [
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
    ]


async def test_snapshot_counts_overdue_open_tasks_as_a_separate_risk() -> None:
    """总览必须单独暴露逾期开放任务数，终态任务不得计入。"""
    engine, factory = await _factory()
    local_date = date(2026, 8, 11)
    async with factory() as session:
        session.add_all(
            [
                PropertyProfile(id=7, room_number="0701", title="江景大床房", is_active=True),
                BusinessTask(
                    task_type=BusinessTaskType.CLEANING,
                    status=BusinessTaskStatus.ASSIGNED,
                    property_id=7,
                    service_date=local_date - timedelta(days=1),
                    description="逾期保洁",
                ),
                BusinessTask(
                    task_type=BusinessTaskType.CLEANING,
                    status=BusinessTaskStatus.EXPIRED,
                    property_id=7,
                    service_date=local_date - timedelta(days=3),
                    description="已失效任务",
                ),
                BusinessTask(
                    task_type=BusinessTaskType.CLEANING,
                    status=BusinessTaskStatus.PENDING_ASSIGNMENT,
                    property_id=7,
                    service_date=local_date,
                    description="今日任务",
                ),
            ]
        )
        await session.commit()

        snapshot = await AdminDashboardService(session).snapshot(
            datetime(2026, 8, 11, 2, tzinfo=UTC)
        )

    assert snapshot.local_date == local_date
    assert snapshot.pending_task_count == 2
    assert snapshot.overdue_task_count == 1
    assert Snapshot.empty(local_date).overdue_task_count == 0
    await engine.dispose()  # type: ignore[attr-defined]
