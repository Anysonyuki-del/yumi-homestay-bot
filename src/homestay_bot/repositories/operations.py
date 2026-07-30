from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import (
    BusinessTaskStatus,
    BusinessTaskType,
    CustomerIdentityProvider,
)
from homestay_bot.domain.models import (
    BusinessTask,
    Customer,
    CustomerIdentity,
    HostexWebhookEvent,
    Job,
    PropertyProfile,
    StayOrder,
)
from homestay_bot.integrations.hostex_client import Reservation


class SQLAlchemyOperationsRepository:
    """提供运营模型的最小幂等写入入口。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前运营事务。"""
        self._session = session

    async def create_turnover(
        self,
        *,
        property_id: int,
        service_date: date,
        order_id: int | None = None,
    ) -> BusinessTask:
        """按房间和服务日幂等创建周转保洁任务。"""
        dedupe_key = f"turnover:{property_id}:{service_date.isoformat()}"
        existing = await self._session.scalar(
            select(BusinessTask).where(BusinessTask.dedupe_key == dedupe_key)
        )
        if existing is not None:
            return existing
        task = BusinessTask(
            dedupe_key=dedupe_key,
            task_type=BusinessTaskType.CLEANING,
            status=BusinessTaskStatus.PENDING_ASSIGNMENT,
            order_id=order_id,
            property_id=property_id,
            service_date=service_date,
            description="退房后周转保洁",
        )
        self._session.add(task)
        await self._session.flush()
        return task

    async def record_hostex_event(
        self,
        *,
        event_key: str,
        event_type: str,
        reservation_code: str | None,
        payload: dict[str, Any],
    ) -> bool:
        """同一事务保存首次事件和唯一后台任务。"""
        existing = await self._session.scalar(
            select(HostexWebhookEvent).where(
                HostexWebhookEvent.event_key == event_key
            )
        )
        if existing is not None:
            return False
        event = HostexWebhookEvent(
            event_key=event_key,
            event_type=event_type,
            reservation_code=reservation_code,
            payload=payload,
        )
        self._session.add(event)
        self._session.add(
            Job(
                job_type="hostex_event",
                dedupe_key=f"hostex-event:{event_key}",
                payload={"event_key": event_key},
                available_at=datetime.now(UTC),
            )
        )
        await self._session.flush()
        return True

    async def require_pending_event(self, event_key: str) -> HostexWebhookEvent:
        """锁定并返回待处理事件。"""
        event = await self._session.scalar(
            select(HostexWebhookEvent)
            .where(
                HostexWebhookEvent.event_key == event_key,
                HostexWebhookEvent.status == "pending",
            )
            .with_for_update()
        )
        if event is None:
            raise LookupError("百居易事件不存在或已经处理")
        return event

    async def upsert_reservation(self, reservation: Reservation) -> StayOrder:
        """按订单编号 upsert 房间、百居易客户身份和入住订单。"""
        property_profile = await self._session.get(
            PropertyProfile,
            reservation.property_id,
        )
        if property_profile is None:
            property_profile = PropertyProfile(
                id=reservation.property_id,
                title=f"百居易房间 {reservation.property_id}",
            )
            self._session.add(property_profile)

        identity = await self._session.scalar(
            select(CustomerIdentity).where(
                CustomerIdentity.provider == CustomerIdentityProvider.HOSTEX,
                CustomerIdentity.external_id == reservation.reservation_code,
            )
        )
        if identity is None:
            customer = Customer(
                display_name=reservation.guest_name or "百居易客户"
            )
            identity = CustomerIdentity(
                customer=customer,
                provider=CustomerIdentityProvider.HOSTEX,
                external_id=reservation.reservation_code,
                is_verified=True,
            )
            self._session.add_all([customer, identity])
            await self._session.flush()

        order = await self._session.scalar(
            select(StayOrder).where(
                StayOrder.hostex_reservation_code == reservation.reservation_code
            )
        )
        if order is None:
            order = StayOrder(
                hostex_reservation_code=reservation.reservation_code,
                stay_code=reservation.stay_code,
                customer_id=identity.customer_id,
                property_id=reservation.property_id,
                check_in_date=reservation.check_in_date,
                check_out_date=reservation.check_out_date,
                status=reservation.status,
            )
            self._session.add(order)
        else:
            order.stay_code = reservation.stay_code
            order.customer_id = identity.customer_id
            order.property_id = reservation.property_id
            order.check_in_date = reservation.check_in_date
            order.check_out_date = reservation.check_out_date
            order.status = reservation.status
        order.last_hostex_sync_at = datetime.now(UTC)
        await self._session.flush()
        return order

    async def mark_event_completed(self, event: HostexWebhookEvent) -> None:
        """标记事件处理完成。"""
        event.status = "completed"
        event.last_error_code = None
        await self._session.flush()

    async def reconcile_reservations(
        self,
        reservations: list[Reservation],
    ) -> int:
        """逐笔复用订单 upsert，返回对账数量。"""
        for reservation in reservations:
            await self.upsert_reservation(reservation)
        return len(reservations)
