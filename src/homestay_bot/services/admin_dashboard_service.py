"""管理员总览的只读聚合服务。"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import (
    ApprovalStatus,
    BusinessTaskStatus,
    ComplaintReviewStatus,
    CredentialDeliveryStatus,
    CustomerMergeStatus,
    ReminderStatus,
    RoomOperationalStatus,
)
from homestay_bot.domain.models import (
    BookingApproval,
    BusinessTask,
    ComplaintReview,
    CredentialDelivery,
    CustomerMergeSuggestion,
    LifecycleReminder,
    PropertyProfile,
    RoomOperationalState,
    StayOrder,
)
from homestay_bot.repositories.admin_dashboard import SQLAlchemyAdminDashboardRepository

WUHAN_TIMEZONE = ZoneInfo("Asia/Shanghai")
TERMINAL_STAY_STATUSES = ("canceled", "cancelled", "declined", "expired", "deleted")


@dataclass(frozen=True, slots=True)
class StaySummary:
    """总览允许展示的一笔入住事实，不携带客户或平台标识。"""

    room_title: str


@dataclass(frozen=True, slots=True)
class Snapshot:
    """一次一致的后台运营快照。"""

    local_date: date
    arrivals: tuple[StaySummary, ...]
    departures: tuple[StaySummary, ...]
    room_status_counts: dict[RoomOperationalStatus, int]
    pending_task_count: int
    pending_approval_count: int
    manual_attention_count: int

    @property
    def check_in_count(self) -> int:
        """返回今日入住数。"""
        return len(self.arrivals)

    @property
    def check_out_count(self) -> int:
        """返回今日退房数。"""
        return len(self.departures)

    @property
    def active_room_count(self) -> int:
        """返回当前启用房间总数。"""
        return sum(self.room_status_counts.values())

    @classmethod
    def empty(cls, local_date: date) -> "Snapshot":
        """构造适合新部署和空数据库的完整零值快照。"""
        return cls(
            local_date=local_date,
            arrivals=(),
            departures=(),
            room_status_counts={status: 0 for status in RoomOperationalStatus},
            pending_task_count=0,
            pending_approval_count=0,
            manual_attention_count=0,
        )


class ConsistentReadPort(Protocol):
    """定义总览多查询前的一致读准备接口。"""

    async def prepare_consistent_read(self) -> None:
        """在第一项业务查询前固定数据库读快照。"""


class AdminDashboardService:
    """通过单个只读数据库会话聚合小型民宿关键运营事实。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        consistent_read: ConsistentReadPort | None = None,
    ) -> None:
        """保存请求期数据库会话与一致读准备器。"""
        self._session = session
        self._consistent_read = consistent_read or SQLAlchemyAdminDashboardRepository(session)

    async def _stays_for(self, local_date: date, *, arrival: bool) -> tuple[StaySummary, ...]:
        """读取今日入住或退房，仅投影房源标题。"""
        date_column = StayOrder.check_in_date if arrival else StayOrder.check_out_date
        rows = await self._session.execute(
            select(PropertyProfile.title)
            .join(StayOrder, StayOrder.property_id == PropertyProfile.id)
            .where(
                date_column == local_date,
                func.lower(func.trim(StayOrder.status)).not_in(TERMINAL_STAY_STATUSES),
            )
            .order_by(PropertyProfile.title, StayOrder.id)
        )
        return tuple(StaySummary(room_title=title) for title in rows.scalars())

    async def _room_counts(self) -> dict[RoomOperationalStatus, int]:
        """统计启用房间状态；尚无运营记录的房间归为未开始。"""
        rows = await self._session.execute(
            select(RoomOperationalState.status, func.count(PropertyProfile.id))
            .select_from(PropertyProfile)
            .outerjoin(
                RoomOperationalState,
                RoomOperationalState.property_id == PropertyProfile.id,
            )
            .where(PropertyProfile.is_active.is_(True))
            .group_by(RoomOperationalState.status)
        )
        counts = {status: 0 for status in RoomOperationalStatus}
        for status, count in rows:
            effective_status = status or RoomOperationalStatus.NOT_STARTED
            counts[effective_status] += int(count)
        return counts

    async def _count_pending_tasks(self) -> int:
        """统计仍需要运营处理的任务。"""
        result = await self._session.scalar(
            select(func.count(BusinessTask.id)).where(
                BusinessTask.status.not_in(
                    (BusinessTaskStatus.COMPLETED, BusinessTaskStatus.CANCELLED)
                )
            )
        )
        return int(result or 0)

    async def _count_pending_approvals(self) -> int:
        """统计尚未形成终态的预订审批。"""
        result = await self._session.scalar(
            select(func.count(BookingApproval.id)).where(
                BookingApproval.status.in_(
                    (
                        ApprovalStatus.PENDING,
                        ApprovalStatus.CREATING,
                        ApprovalStatus.CONFLICT,
                        ApprovalStatus.NEEDS_REVIEW,
                    )
                )
            )
        )
        return int(result or 0)

    async def _count_manual_attention(self) -> int:
        """合计客诉、凭证、提醒及客户合并中明确需要人工判断的事项。"""
        complaint_count = await self._session.scalar(
            select(func.count(ComplaintReview.id)).where(
                ComplaintReview.status.in_(
                    (
                        ComplaintReviewStatus.READY_FOR_REVIEW,
                        ComplaintReviewStatus.EDITING,
                        ComplaintReviewStatus.DELIVERY_FAILED,
                        ComplaintReviewStatus.ANALYSIS_FAILED,
                    )
                )
            )
        )
        credential_count = await self._session.scalar(
            select(func.count(CredentialDelivery.id)).where(
                CredentialDelivery.status.in_(
                    (
                        CredentialDeliveryStatus.NEEDS_REVIEW,
                        CredentialDeliveryStatus.MANUAL_FOLLOWUP,
                    )
                )
            )
        )
        reminder_count = await self._session.scalar(
            select(func.count(LifecycleReminder.id)).where(
                LifecycleReminder.status == ReminderStatus.MANUAL_FOLLOWUP
            )
        )
        merge_count = await self._session.scalar(
            select(func.count(CustomerMergeSuggestion.id)).where(
                CustomerMergeSuggestion.status == CustomerMergeStatus.PENDING
            )
        )
        return sum(
            int(value or 0)
            for value in (complaint_count, credential_count, reminder_count, merge_count)
        )

    async def snapshot(self, now: datetime | None = None) -> Snapshot:
        """按 Asia/Shanghai 当日边界生成只读快照。"""
        await self._consistent_read.prepare_consistent_read()
        observed_at = now or datetime.now(UTC)
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        local_date = observed_at.astimezone(WUHAN_TIMEZONE).date()
        return Snapshot(
            local_date=local_date,
            arrivals=await self._stays_for(local_date, arrival=True),
            departures=await self._stays_for(local_date, arrival=False),
            room_status_counts=await self._room_counts(),
            pending_task_count=await self._count_pending_tasks(),
            pending_approval_count=await self._count_pending_approvals(),
            manual_attention_count=await self._count_manual_attention(),
        )
