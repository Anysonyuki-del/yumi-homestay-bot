from datetime import date
from types import SimpleNamespace

import pytest

from homestay_bot.integrations.hostex_client import Reservation
from homestay_bot.services.hostex_sync import HostexSyncConflict, HostexSyncService


def reservation(code: str = "R-1", status: str = "confirmed") -> Reservation:
    """构造百居易订单结果。"""
    return Reservation(
        reservation_code=code,
        stay_code="S-1",
        property_id=101,
        check_in_date=date(2026, 8, 1),
        check_out_date=date(2026, 8, 2),
        status=status,
        created_at="2026-07-31T00:00:00Z",
    )


class HostexStub:
    """按测试配置返回订单列表。"""

    def __init__(self, items: list[Reservation]) -> None:
        """保存固定订单。"""
        self.items = items
        self.queries = []

    async def list_reservations(self, query):
        """记录查询并返回订单。"""
        self.queries.append(query)
        return self.items


class OperationsStub:
    """模拟事件读取、订单 upsert 和完成标记。"""

    def __init__(self) -> None:
        """初始化固定待处理事件。"""
        self.event = SimpleNamespace(
            event_key="event-1",
            reservation_code="R-1",
        )
        self.upserts: list[Reservation] = []
        self.completed = False
        self.turnovers: list[tuple[int, date, int]] = []

    async def require_pending_event(self, event_key: str):
        """返回固定事件。"""
        assert event_key == "event-1"
        return self.event

    async def upsert_reservation(self, item: Reservation):
        """记录同步订单。"""
        self.upserts.append(item)
        return SimpleNamespace(id=1)

    async def create_turnover(
        self,
        *,
        property_id: int,
        service_date: date,
        order_id: int,
    ):
        """记录订单对应的周转保洁任务。"""
        self.turnovers.append((property_id, service_date, order_id))
        return SimpleNamespace(id=9)

    async def mark_event_completed(self, event) -> None:
        """记录事件已完成。"""
        self.completed = True

    async def reconcile_reservations(self, items: list[Reservation]):
        """返回同步数量。"""
        self.upserts.extend(items)
        return len(items)


class LifecycleStub:
    """记录订单同步后创建的生命周期计划。"""

    def __init__(self) -> None:
        """初始化订单编号列表。"""
        self.order_ids: list[int] = []

    async def schedule_for_order(self, order_id: int):
        """记录非取消订单的提醒计划。"""
        self.order_ids.append(order_id)
        return []


@pytest.mark.asyncio
async def test_hostex_event_upserts_exact_reservation() -> None:
    """事件必须精确命中一笔订单后才允许 upsert 和完成。"""
    hostex = HostexStub([reservation()])
    operations = OperationsStub()
    service = HostexSyncService(hostex, operations)

    await service.handle_event("event-1")

    assert operations.upserts[0].reservation_code == "R-1"
    assert operations.turnovers == [(101, date(2026, 8, 2), 1)]
    assert operations.completed is True


@pytest.mark.asyncio
async def test_hostex_event_releases_event_lock_before_network_call() -> None:
    """事件行锁应在调用百居易前提交释放，避免网络延迟占用数据库事务。"""
    sequence: list[str] = []

    async def commit_before_network() -> None:
        """记录外部调用前的事务边界。"""
        sequence.append("committed")

    class RecordingHostex(HostexStub):
        """记录百居易读取发生在提交之后。"""

        async def list_reservations(self, query):
            """记录网络调用顺序并返回订单。"""
            sequence.append("network")
            return await super().list_reservations(query)

    service = HostexSyncService(
        RecordingHostex([reservation()]),
        OperationsStub(),
        before_external=commit_before_network,
    )

    await service.handle_event("event-1")

    assert sequence == ["committed", "network"]


@pytest.mark.asyncio
async def test_hostex_event_conflict_does_not_guess_order() -> None:
    """零条或多条结果必须保留事件待复核，不能猜测订单。"""
    operations = OperationsStub()
    service = HostexSyncService(HostexStub([]), operations)

    with pytest.raises(HostexSyncConflict):
        await service.handle_event("event-1")

    assert operations.upserts == []
    assert operations.completed is False


@pytest.mark.asyncio
async def test_reconcile_backfills_webhook_gap() -> None:
    """定时对账应把日期窗口内订单交给仓储统一 upsert。"""
    hostex = HostexStub([reservation("R-MISSED")])
    operations = OperationsStub()
    service = HostexSyncService(hostex, operations)

    count = await service.reconcile(date(2026, 8, 1), date(2026, 8, 15))

    assert count == 1
    assert operations.upserts[0].reservation_code == "R-MISSED"
    assert operations.turnovers == [(101, date(2026, 8, 2), 1)]


@pytest.mark.asyncio
async def test_cancelled_reservation_does_not_create_turnover() -> None:
    """取消订单不得生成新的周转保洁任务。"""
    hostex = HostexStub([reservation(status="cancelled")])
    operations = OperationsStub()
    service = HostexSyncService(hostex, operations)

    await service.handle_event("event-1")

    assert operations.turnovers == []


@pytest.mark.asyncio
async def test_cancelled_reservation_updates_lifecycle_schedule() -> None:
    """取消订单仍需进入生命周期服务撤销既有提醒。"""
    lifecycle = LifecycleStub()
    service = HostexSyncService(
        HostexStub([reservation(status="cancelled")]),
        OperationsStub(),
        lifecycle=lifecycle,
    )

    await service.handle_event("event-1")

    assert lifecycle.order_ids == [1]


@pytest.mark.asyncio
async def test_synced_order_schedules_lifecycle_through_shared_path() -> None:
    """Webhook 和对账共用的同步路径应登记入住生命周期提醒。"""
    lifecycle = LifecycleStub()
    service = HostexSyncService(
        HostexStub([reservation()]),
        OperationsStub(),
        lifecycle=lifecycle,
    )

    await service.handle_event("event-1")

    assert lifecycle.order_ids == [1]
