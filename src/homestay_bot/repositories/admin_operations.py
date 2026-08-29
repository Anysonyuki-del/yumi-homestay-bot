"""管理员运营台所需的本地只读批量查询。"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import (
    BusinessTaskStatus,
    ComplaintReviewStatus,
    CredentialDeliveryStatus,
    CustomerMergeStatus,
    ReminderStatus,
    RoomOperationalStatus,
)
from homestay_bot.domain.models import (
    BusinessTask,
    ComplaintReview,
    CredentialDelivery,
    CustomerMergeSuggestion,
    LifecycleReminder,
    PropertyProfile,
    RoomCredential,
    RoomOperationalState,
    StayOrder,
)
from homestay_bot.repositories.admin_dashboard import SQLAlchemyAdminDashboardRepository

TERMINAL_STAY_STATUSES = ("canceled", "cancelled", "declined", "expired", "deleted")
type AttentionKind = Literal[
    "complaint", "credential", "reminder", "customer_merge", "task"
]
type AttentionStatus = (
    ComplaintReviewStatus
    | CredentialDeliveryStatus
    | ReminderStatus
    | CustomerMergeStatus
    | BusinessTaskStatus
)


@dataclass(frozen=True, slots=True)
class AttentionRecord:
    """保存一种待处理领域对象的最小安全投影。"""

    kind: AttentionKind
    record_id: int
    status: AttentionStatus
    property_id: int | None
    room_title: str | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ActiveRoomRecord:
    """保存启用房间及其当前运营状态。"""

    property_id: int
    room_number: str | None
    room_title: str
    status: RoomOperationalStatus


@dataclass(frozen=True, slots=True)
class StayRecord:
    """保存七日矩阵及下一笔入住所需的订单日期事实。"""

    property_id: int
    check_in_date: date
    check_out_date: date


@dataclass(frozen=True, slots=True)
class RoomTaskCountRecord:
    """保存单个启用房间的未完成任务数。"""

    property_id: int
    count: int


class SQLAlchemyAdminOperationsRepository:
    """以固定次数查询聚合运营台数据，查询数不随房间数量增长。"""

    def __init__(self, session: AsyncSession) -> None:
        """保存请求期短会话并复用后台一致读准备器。"""
        self._session = session
        self._consistent_read = SQLAlchemyAdminDashboardRepository(session)

    async def prepare_consistent_read(self) -> None:
        """在首项业务查询前固定只读数据库快照。"""
        await self._consistent_read.prepare_consistent_read()

    async def list_attention(self) -> tuple[AttentionRecord, ...]:
        """分领域读取人工事项，保留原始枚举状态且不读取敏感正文。"""
        records: list[AttentionRecord] = []

        complaint_rows = await self._session.execute(
            select(
                ComplaintReview.id,
                ComplaintReview.status,
                ComplaintReview.updated_at,
            )
            .where(
                ComplaintReview.status.in_(
                    (
                        ComplaintReviewStatus.READY_FOR_REVIEW,
                        ComplaintReviewStatus.EDITING,
                        ComplaintReviewStatus.DELIVERY_FAILED,
                        ComplaintReviewStatus.ANALYSIS_FAILED,
                        ComplaintReviewStatus.RETURNED,
                    )
                )
            )
            .order_by(ComplaintReview.updated_at, ComplaintReview.id)
        )
        records.extend(
            AttentionRecord("complaint", record_id, status, None, None, updated_at)
            for record_id, status, updated_at in complaint_rows
        )

        credential_rows = await self._session.execute(
            select(
                CredentialDelivery.id,
                CredentialDelivery.status,
                PropertyProfile.id,
                PropertyProfile.title,
                CredentialDelivery.updated_at,
            )
            .join(RoomCredential, RoomCredential.id == CredentialDelivery.credential_id)
            .join(PropertyProfile, PropertyProfile.id == RoomCredential.property_id)
            .where(
                CredentialDelivery.status.in_(
                    (
                        CredentialDeliveryStatus.NEEDS_REVIEW,
                        CredentialDeliveryStatus.MANUAL_FOLLOWUP,
                    )
                )
            )
            .order_by(CredentialDelivery.updated_at, CredentialDelivery.id)
        )
        records.extend(
            AttentionRecord(
                "credential",
                record_id,
                status,
                property_id,
                room_title,
                updated_at,
            )
            for record_id, status, property_id, room_title, updated_at in credential_rows
        )

        reminder_rows = await self._session.execute(
            select(
                LifecycleReminder.id,
                LifecycleReminder.status,
                PropertyProfile.id,
                PropertyProfile.title,
                LifecycleReminder.updated_at,
            )
            .join(StayOrder, StayOrder.id == LifecycleReminder.order_id)
            .join(PropertyProfile, PropertyProfile.id == StayOrder.property_id)
            .where(LifecycleReminder.status == ReminderStatus.MANUAL_FOLLOWUP)
            .order_by(LifecycleReminder.updated_at, LifecycleReminder.id)
        )
        records.extend(
            AttentionRecord(
                "reminder",
                record_id,
                status,
                property_id,
                room_title,
                updated_at,
            )
            for record_id, status, property_id, room_title, updated_at in reminder_rows
        )

        merge_rows = await self._session.execute(
            select(
                CustomerMergeSuggestion.id,
                CustomerMergeSuggestion.status,
                CustomerMergeSuggestion.updated_at,
            )
            .where(CustomerMergeSuggestion.status == CustomerMergeStatus.PENDING)
            .order_by(CustomerMergeSuggestion.updated_at, CustomerMergeSuggestion.id)
        )
        records.extend(
            AttentionRecord("customer_merge", record_id, status, None, None, updated_at)
            for record_id, status, updated_at in merge_rows
        )

        task_rows = await self._session.execute(
            select(
                BusinessTask.id,
                BusinessTask.status,
                PropertyProfile.id,
                PropertyProfile.title,
                BusinessTask.updated_at,
            )
            .outerjoin(PropertyProfile, PropertyProfile.id == BusinessTask.property_id)
            .where(BusinessTask.status == BusinessTaskStatus.PENDING_CONFIRMATION)
            .order_by(BusinessTask.updated_at, BusinessTask.id)
        )
        records.extend(
            AttentionRecord("task", record_id, status, property_id, room_title, updated_at)
            for record_id, status, property_id, room_title, updated_at in task_rows
        )
        return tuple(records)

    async def list_active_rooms(self) -> tuple[ActiveRoomRecord, ...]:
        """批量读取全部启用房间，缺失房态统一投影为未开始。"""
        rows = await self._session.execute(
            select(
                PropertyProfile.id,
                PropertyProfile.room_number,
                PropertyProfile.title,
                RoomOperationalState.status,
            )
            .outerjoin(
                RoomOperationalState,
                RoomOperationalState.property_id == PropertyProfile.id,
            )
            .where(PropertyProfile.is_active.is_(True))
            .order_by(PropertyProfile.title, PropertyProfile.id)
        )
        return tuple(
            ActiveRoomRecord(
                property_id=property_id,
                room_number=room_number,
                room_title=room_title,
                status=status or RoomOperationalStatus.NOT_STARTED,
            )
            for property_id, room_number, room_title, status in rows
        )

    async def list_current_and_future_stays(self, local_date: date) -> tuple[StayRecord, ...]:
        """批量读取启用房间当前及未来有效订单，供七日与下一入住共用。"""
        rows = await self._session.execute(
            select(
                StayOrder.property_id,
                StayOrder.check_in_date,
                StayOrder.check_out_date,
            )
            .join(PropertyProfile, PropertyProfile.id == StayOrder.property_id)
            .where(
                PropertyProfile.is_active.is_(True),
                StayOrder.check_out_date > local_date,
                func.lower(func.trim(StayOrder.status)).not_in(TERMINAL_STAY_STATUSES),
            )
            .order_by(StayOrder.property_id, StayOrder.check_in_date, StayOrder.id)
        )
        return tuple(
            StayRecord(
                property_id=property_id,
                check_in_date=check_in_date,
                check_out_date=check_out_date,
            )
            for property_id, check_in_date, check_out_date in rows
        )

    async def list_open_task_counts(self) -> tuple[RoomTaskCountRecord, ...]:
        """按启用房间批量统计未完成且未取消的任务。"""
        rows = await self._session.execute(
            select(BusinessTask.property_id, func.count(BusinessTask.id))
            .join(PropertyProfile, PropertyProfile.id == BusinessTask.property_id)
            .where(
                PropertyProfile.is_active.is_(True),
                BusinessTask.status.not_in(
                    (BusinessTaskStatus.COMPLETED, BusinessTaskStatus.CANCELLED)
                ),
            )
            .group_by(BusinessTask.property_id)
        )
        return tuple(
            RoomTaskCountRecord(property_id=property_id, count=int(count))
            for property_id, count in rows
            if property_id is not None
        )
