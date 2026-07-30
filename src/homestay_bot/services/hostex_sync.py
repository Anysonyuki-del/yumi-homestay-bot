from datetime import date
from typing import Protocol

from homestay_bot.domain.models import HostexWebhookEvent, StayOrder
from homestay_bot.integrations.hostex_client import (
    Reservation,
    ReservationQuery,
)


class HostexSyncConflict(RuntimeError):
    """表示 Webhook 不能精确对应一笔百居易订单。"""

    def __init__(self, event_key: str, reservation_count: int) -> None:
        """保留事件键和结果数量供人工复核。"""
        super().__init__(
            f"Hostex event {event_key} matched {reservation_count} reservations"
        )
        self.event_key = event_key
        self.reservation_count = reservation_count


class HostexReservationPort(Protocol):
    """定义订单同步需要的百居易只读接口。"""

    async def list_reservations(
        self, query: ReservationQuery
    ) -> list[Reservation]:
        """按条件查询订单。"""


class OperationsSyncPort(Protocol):
    """定义百居易同步所需的运营仓储操作。"""

    async def require_pending_event(
        self, event_key: str
    ) -> HostexWebhookEvent:
        """读取一条待处理事件。"""

    async def upsert_reservation(self, reservation: Reservation) -> StayOrder:
        """幂等写入订单。"""

    async def mark_event_completed(self, event: HostexWebhookEvent) -> None:
        """标记事件完成。"""

    async def reconcile_reservations(
        self, reservations: list[Reservation]
    ) -> int:
        """批量 upsert 对账窗口订单。"""


class HostexSyncService:
    """把百居易事件和定时查询统一转换为本地订单。"""

    def __init__(
        self,
        hostex: HostexReservationPort,
        operations: OperationsSyncPort,
    ) -> None:
        """注入百居易只读客户端和运营仓储。"""
        self._hostex = hostex
        self._operations = operations

    async def handle_event(self, event_key: str) -> None:
        """精确查询事件订单并完成幂等 upsert。"""
        event = await self._operations.require_pending_event(event_key)
        matches = await self._hostex.list_reservations(
            ReservationQuery(reservation_code=event.reservation_code)
        )
        if len(matches) != 1:
            raise HostexSyncConflict(event.event_key, len(matches))
        await self._operations.upsert_reservation(matches[0])
        await self._operations.mark_event_completed(event)

    async def reconcile(self, start_date: date, end_date: date) -> int:
        """补回日期窗口内遗漏的 Webhook 订单。"""
        reservations = await self._hostex.list_reservations(
            ReservationQuery(
                start_check_in_date=start_date,
                end_check_in_date=end_date,
            )
        )
        return await self._operations.reconcile_reservations(reservations)
