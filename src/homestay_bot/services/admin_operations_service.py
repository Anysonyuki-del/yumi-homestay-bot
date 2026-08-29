"""管理员待关注事项与房间近期运营的安全页面投影。"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import (
    BusinessTaskStatus,
    ComplaintReviewStatus,
    CredentialDeliveryStatus,
    CustomerMergeStatus,
    ReminderStatus,
    RoomOccupancyStatus,
    RoomOperationalStatus,
)
from homestay_bot.repositories.admin_operations import (
    ActiveRoomRecord,
    AttentionKind,
    AttentionRecord,
    AttentionStatus,
    RoomTaskCountRecord,
    SQLAlchemyAdminOperationsRepository,
    StayRecord,
)

WUHAN_TIMEZONE = ZoneInfo("Asia/Shanghai")
ATTENTION_STATUS_TEXT: dict[AttentionStatus, str] = {
    ComplaintReviewStatus.READY_FOR_REVIEW: "等待人工复核",
    ComplaintReviewStatus.EDITING: "正在人工编辑",
    ComplaintReviewStatus.DELIVERY_FAILED: "回复投递失败",
    ComplaintReviewStatus.ANALYSIS_FAILED: "分析失败",
    ComplaintReviewStatus.RETURNED: "已退回重新分析",
    CredentialDeliveryStatus.NEEDS_REVIEW: "需要安全复核",
    CredentialDeliveryStatus.MANUAL_FOLLOWUP: "需要人工跟进",
    ReminderStatus.MANUAL_FOLLOWUP: "需要人工跟进",
    CustomerMergeStatus.PENDING: "等待管理员复核",
    BusinessTaskStatus.PENDING_CONFIRMATION: "等待管理员确认",
}


@dataclass(frozen=True, slots=True)
class AttentionItem:
    """表示待处理中心的一项安全、可跳转行动。"""

    kind: AttentionKind
    record_id: int
    status: AttentionStatus
    title: str
    summary: str
    target_url: str
    property_id: int | None
    room_title: str | None
    updated_at: datetime
    related_count: int = 1


@dataclass(frozen=True, slots=True)
class RoomDayOperation:
    """表示一个房间在某日的入住、退房及占用事实。"""

    local_date: date
    arrival_count: int
    departure_count: int
    occupied: bool


@dataclass(frozen=True, slots=True)
class RoomOperationItem:
    """表示房间今日运营状态与下一步概览。"""

    property_id: int
    room_number: str | None
    room_title: str
    status: RoomOperationalStatus
    today_arrival_count: int
    today_departure_count: int
    open_task_count: int
    next_arrival: date | None
    occupancy_status: RoomOccupancyStatus = RoomOccupancyStatus.UNKNOWN
    overdue_task_count: int = 0
    next_departure: date | None = None
    next_action: str = "暂无近期运营动作"
    source_stale: bool = True

    @property
    def today_arrival(self) -> bool:
        """返回今日是否至少有一笔入住。"""
        return self.today_arrival_count > 0

    @property
    def today_departure(self) -> bool:
        """返回今日是否至少有一笔退房。"""
        return self.today_departure_count > 0


@dataclass(frozen=True, slots=True)
class SevenDayRoomItem:
    """表示一个房间近期运营时间轴；保留类名以兼容既有调用方。"""

    property_id: int
    room_number: str | None
    room_title: str
    days: tuple[RoomDayOperation, ...]


@dataclass(frozen=True, slots=True)
class OperationsSnapshot:
    """表示一次一致读取形成的完整运营台快照。"""

    local_date: date
    attention_items: tuple[AttentionItem, ...]
    rooms: tuple[RoomOperationItem, ...]
    seven_day_rooms: tuple[SevenDayRoomItem, ...]
    horizon_days: int = 3
    source_synced_at: datetime | None = None
    source_stale: bool = True

    @property
    def attention_count(self) -> int:
        """返回汇总卡片背后的真实待处理事项数量。"""
        return sum(item.related_count for item in self.attention_items)


class AdminOperationsRepositoryPort(Protocol):
    """定义运营页面服务使用的固定批量查询接口。"""

    async def prepare_consistent_read(self) -> None:
        """在业务查询前固定只读快照。"""

    async def list_attention(self) -> tuple[AttentionRecord, ...]:
        """返回分领域人工事项。"""

    async def list_active_rooms(self) -> tuple[ActiveRoomRecord, ...]:
        """返回启用房间与当前房态。"""

    async def list_room_stays(
        self,
        start_date: date,
        end_date: date,
    ) -> tuple[StayRecord, ...]:
        """返回与近期运营窗口相交的有效订单日期。"""

    async def list_open_task_counts(
        self,
        local_date: date,
    ) -> tuple[RoomTaskCountRecord, ...]:
        """返回各房间未完成任务数。"""


class AdminOperationsService:
    """把本地批量查询转换为无敏感字段的运营页面模型。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        repository: AdminOperationsRepositoryPort | None = None,
    ) -> None:
        """注入请求期短会话或便于测试的只读仓储。"""
        self._repository = repository or SQLAlchemyAdminOperationsRepository(session)

    @staticmethod
    def _attention_item(
        record: AttentionRecord,
        *,
        related_count: int = 1,
    ) -> AttentionItem:
        """按领域状态生成安全中文文案和可靠页面入口。"""
        room_context = f"，关联房间：{record.room_title}" if record.room_title else ""
        status_text = ATTENTION_STATUS_TEXT[record.status]
        if record.kind == "complaint":
            title = "客诉需要处理"
            summary = f"客诉 #{record.record_id}：{status_text}"
            target_url = f"/employee/complaints/{record.record_id}"
        elif record.kind == "credential":
            title = "入住凭证需要跟进"
            summary = (
                f"该房源共有 {related_count} 项凭证投递需要跟进{room_context}"
                if related_count > 1
                else f"凭证投递 #{record.record_id}：{status_text}{room_context}"
            )
            target_url = (
                f"/employee/tasks?property_id={record.property_id}"
                if record.property_id is not None
                else "/employee/tasks"
            )
        elif record.kind == "reminder":
            title = "入住提醒需要跟进"
            summary = (
                f"该房源共有 {related_count} 项入住提醒需要跟进{room_context}"
                if related_count > 1
                else f"提醒 #{record.record_id}：{status_text}{room_context}"
            )
            target_url = (
                f"/employee/tasks?property_id={record.property_id}"
                if record.property_id is not None
                else "/employee/tasks"
            )
        elif record.kind == "customer_merge":
            title = "客户档案合并待复核"
            summary = f"合并建议 #{record.record_id}：{status_text}"
            target_url = f"/employee/customers/merge/{record.record_id}"
        else:
            title = "业务任务待确认"
            summary = (
                f"共有 {related_count} 项业务任务等待确认{room_context}"
                if related_count > 1
                else f"任务 #{record.record_id}：{status_text}{room_context}"
            )
            if related_count == 1:
                target_url = f"/employee/tasks/{record.record_id}"
            elif record.property_id is not None:
                target_url = f"/employee/tasks?property_id={record.property_id}"
            else:
                target_url = "/employee/tasks"
        return AttentionItem(
            kind=record.kind,
            record_id=record.record_id,
            status=record.status,
            title=title,
            summary=summary,
            target_url=target_url,
            property_id=record.property_id,
            room_title=record.room_title,
            updated_at=record.updated_at,
            related_count=related_count,
        )

    @classmethod
    def _attention_items(
        cls,
        records: tuple[AttentionRecord, ...],
    ) -> tuple[AttentionItem, ...]:
        """按房源和事项类型归并重复工作，投诉与合并仍保持独立。"""
        grouped: dict[
            tuple[AttentionKind, int | None, AttentionStatus],
            list[AttentionRecord],
        ] = defaultdict(list)
        items: list[AttentionItem] = []
        for record in records:
            if record.kind in {"credential", "reminder", "task"}:
                grouped[(record.kind, record.property_id, record.status)].append(
                    record
                )
            else:
                items.append(cls._attention_item(record))
        for records_in_group in grouped.values():
            latest = max(records_in_group, key=lambda item: item.updated_at)
            items.append(
                cls._attention_item(
                    latest,
                    related_count=len(records_in_group),
                )
            )
        return tuple(
            sorted(items, key=lambda item: item.updated_at, reverse=True)
        )

    @staticmethod
    def _room_items(
        local_date: date,
        rooms: tuple[ActiveRoomRecord, ...],
        stays: tuple[StayRecord, ...],
        task_counts: tuple[RoomTaskCountRecord, ...],
        *,
        horizon_days: int,
        source_stale: bool,
    ) -> tuple[tuple[RoomOperationItem, ...], tuple[SevenDayRoomItem, ...]]:
        """在内存中一次构造房间行动摘要及近期时间轴。"""
        stays_by_room: dict[int, list[StayRecord]] = defaultdict(list)
        for stay in stays:
            stays_by_room[stay.property_id].append(stay)
        counts_by_room = {item.property_id: item for item in task_counts}
        room_items: list[RoomOperationItem] = []
        matrix_items: list[SevenDayRoomItem] = []
        days = tuple(
            local_date + timedelta(days=offset)
            for offset in range(-2, horizon_days + 1)
        )

        for room in rooms:
            room_stays = stays_by_room.get(room.property_id, [])
            today_arrivals = sum(stay.check_in_date == local_date for stay in room_stays)
            today_departures = sum(stay.check_out_date == local_date for stay in room_stays)
            occupied_today = any(
                stay.check_in_date <= local_date < stay.check_out_date
                for stay in room_stays
            )
            future_arrivals = [
                stay.check_in_date for stay in room_stays if stay.check_in_date >= local_date
            ]
            future_departures = [
                stay.check_out_date for stay in room_stays if stay.check_out_date >= local_date
            ]
            task_count = counts_by_room.get(
                room.property_id,
                RoomTaskCountRecord(room.property_id, 0, 0),
            )
            occupancy_status = AdminOperationsService._occupancy_status(
                source_stale=source_stale,
                arrivals=today_arrivals,
                departures=today_departures,
                occupied=occupied_today,
            )
            next_arrival = min(future_arrivals, default=None)
            next_departure = min(future_departures, default=None)
            room_items.append(
                RoomOperationItem(
                    property_id=room.property_id,
                    room_number=room.room_number,
                    room_title=room.room_title,
                    status=room.status,
                    today_arrival_count=today_arrivals,
                    today_departure_count=today_departures,
                    open_task_count=task_count.count,
                    next_arrival=next_arrival,
                    occupancy_status=occupancy_status,
                    overdue_task_count=task_count.overdue_count,
                    next_departure=next_departure,
                    next_action=AdminOperationsService._next_action(
                        source_stale=source_stale,
                        operational_status=room.status,
                        arrivals=today_arrivals,
                        departures=today_departures,
                        occupied=occupied_today,
                        task_count=task_count,
                        next_arrival=next_arrival,
                    ),
                    source_stale=source_stale,
                )
            )
            matrix_items.append(
                SevenDayRoomItem(
                    property_id=room.property_id,
                    room_number=room.room_number,
                    room_title=room.room_title,
                    days=tuple(
                        RoomDayOperation(
                            local_date=day,
                            arrival_count=sum(stay.check_in_date == day for stay in room_stays),
                            departure_count=sum(stay.check_out_date == day for stay in room_stays),
                            occupied=any(
                                stay.check_in_date <= day < stay.check_out_date
                                for stay in room_stays
                            ),
                        )
                        for day in days
                    ),
                )
            )
        return tuple(room_items), tuple(matrix_items)

    @staticmethod
    def _occupancy_status(
        *,
        source_stale: bool,
        arrivals: int,
        departures: int,
        occupied: bool,
    ) -> RoomOccupancyStatus:
        """按当日订单事实推导入住状态，同步过旧时明确返回未知。"""
        if source_stale:
            return RoomOccupancyStatus.UNKNOWN
        if arrivals and departures:
            return RoomOccupancyStatus.TURNOVER_TODAY
        if arrivals:
            return RoomOccupancyStatus.ARRIVING_TODAY
        if departures:
            return RoomOccupancyStatus.DEPARTING_TODAY
        if occupied:
            return RoomOccupancyStatus.OCCUPIED
        return RoomOccupancyStatus.VACANT

    @staticmethod
    def _next_action(
        *,
        source_stale: bool,
        operational_status: RoomOperationalStatus,
        arrivals: int,
        departures: int,
        occupied: bool,
        task_count: RoomTaskCountRecord,
        next_arrival: date | None,
    ) -> str:
        """根据确定性事实给出单一优先行动，不替代员工经营判断。"""
        if source_stale:
            return "先确认百居易实时房态"
        if task_count.overdue_count:
            return f"优先处理 {task_count.overdue_count} 项逾期任务"
        if arrivals and departures:
            return "安排退房周转并核对今日入住"
        if departures:
            return "安排退房检查与周转"
        if arrivals:
            return (
                "核对入住资料并接待"
                if operational_status is RoomOperationalStatus.READY
                else "优先完成房间准备并接待入住"
            )
        if operational_status is RoomOperationalStatus.MAINTENANCE:
            return "跟进维修并确认房间可用性"
        if task_count.count:
            return f"推进 {task_count.count} 项开放任务"
        if occupied:
            return "关注在住服务"
        if next_arrival is not None:
            return f"{next_arrival.month}月{next_arrival.day}日前完成房间准备"
        return "暂无近期运营动作"

    @staticmethod
    def _source_is_stale(
        observed_at: datetime,
        source_synced_at: datetime | None,
    ) -> bool:
        """以六小时窗口判断本地房态来源能否代表近期同步结果。"""
        if source_synced_at is None:
            return True
        aware_source = (
            source_synced_at.replace(tzinfo=UTC)
            if source_synced_at.tzinfo is None
            else source_synced_at
        )
        age = observed_at - aware_source.astimezone(UTC)
        return not timedelta(0) <= age <= timedelta(hours=6)

    async def snapshot(
        self,
        now: datetime | None = None,
        *,
        horizon_days: int = 3,
        source_synced_at: datetime | None = None,
    ) -> OperationsSnapshot:
        """按武汉本地日界线生成一致、只读且可直接渲染的运营快照。"""
        if horizon_days not in {3, 7, 14}:
            raise ValueError("近期房态范围仅支持 3、7 或 14 天")
        await self._repository.prepare_consistent_read()
        observed_at = now or datetime.now(UTC)
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        local_date = observed_at.astimezone(WUHAN_TIMEZONE).date()
        source_stale = self._source_is_stale(observed_at, source_synced_at)
        attention = await self._repository.list_attention()
        rooms = await self._repository.list_active_rooms()
        stays = await self._repository.list_room_stays(
            local_date - timedelta(days=2),
            local_date + timedelta(days=horizon_days + 1),
        )
        task_counts = await self._repository.list_open_task_counts(local_date)
        room_items, matrix_items = self._room_items(
            local_date,
            rooms,
            stays,
            task_counts,
            horizon_days=horizon_days,
            source_stale=source_stale,
        )
        return OperationsSnapshot(
            local_date=local_date,
            attention_items=self._attention_items(attention),
            rooms=room_items,
            seven_day_rooms=matrix_items,
            horizon_days=horizon_days,
            source_synced_at=source_synced_at,
            source_stale=source_stale,
        )
