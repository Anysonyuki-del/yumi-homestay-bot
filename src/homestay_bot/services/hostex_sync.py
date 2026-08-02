from collections.abc import Awaitable, Callable
from datetime import date
from typing import Protocol

from homestay_bot.domain.models import BusinessTask, HostexWebhookEvent, StayOrder
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

    async def mark_event_completed(self, event: HostexWebhookEvent) -> bool:
        """仅在事件状态未变化时标记处理完成。"""

    async def create_turnover(
        self,
        *,
        property_id: int,
        service_date: date,
        order_id: int,
    ) -> BusinessTask:
        """幂等创建订单退房日的周转保洁任务。"""

    async def reconcile_reservations(
        self, reservations: list[Reservation]
    ) -> int:
        """批量 upsert 对账窗口订单。"""


class LifecycleSchedulePort(Protocol):
    """定义订单同步后登记生命周期提醒的边界。"""

    async def schedule_for_order(self, order_id: int) -> list[object]:
        """为一笔有效订单幂等登记提醒。"""


class HostexSyncService:
    """把百居易事件和定时查询统一转换为本地订单。"""

    def __init__(
        self,
        hostex: HostexReservationPort,
        operations: OperationsSyncPort,
        *,
        lifecycle: LifecycleSchedulePort | None = None,
        before_external: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """注入百居易客户端、运营仓储、事务边界和生命周期调度器。"""
        self._hostex = hostex
        self._operations = operations
        self._lifecycle = lifecycle
        self._before_external = before_external

    async def handle_event(self, event_key: str) -> None:
        """精确查询事件订单并完成幂等 upsert。"""
        event = await self._operations.require_pending_event(event_key)
        if self._before_external is not None:
            # 事件读取可能持有行锁；提交快照后再访问百居易，缩短锁和连接占用。
            await self._before_external()
        matches = await self._hostex.list_reservations(
            ReservationQuery(reservation_code=event.reservation_code)
        )
        if len(matches) != 1:
            raise HostexSyncConflict(event.event_key, len(matches))
        await self._sync_reservation(matches[0])
        await self._operations.mark_event_completed(event)

    async def reconcile(self, start_date: date, end_date: date) -> int:
        """补回日期窗口内遗漏的 Webhook 订单。"""
        reservations = await self._hostex.list_reservations(
            ReservationQuery(
                start_check_in_date=start_date,
                end_check_in_date=end_date,
            )
        )
        for reservation in reservations:
            await self._sync_reservation(reservation)
        return len(reservations)

    async def _sync_reservation(self, reservation: Reservation) -> StayOrder:
        """写入一笔订单，并为未取消订单创建唯一周转保洁任务。"""
        order = await self._operations.upsert_reservation(reservation)
        if reservation.status.lower() not in {"cancelled", "canceled"}:
            await self._operations.create_turnover(
                property_id=reservation.property_id,
                service_date=reservation.check_out_date,
                order_id=order.id,
            )
        if self._lifecycle is not None:
            # 取消订单也必须进入同一入口，由生命周期服务撤销既有提醒。
            await self._lifecycle.schedule_for_order(order.id)
        return order
