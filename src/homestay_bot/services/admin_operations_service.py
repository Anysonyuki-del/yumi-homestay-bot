"""管理员待关注事项与房间近期运营的安全页面投影。"""

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
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
TIMELINE_PAST_DAYS = 2
# 民宿营业节奏：12:00 计划退房，12:00–15:00 周转清洁，15:00 起入住。全部按武汉本地时间。
CHECK_OUT_TIME = time(12, 0, tzinfo=WUHAN_TIMEZONE)
CHECK_IN_TIME = time(15, 0, tzinfo=WUHAN_TIMEZONE)
_TURNOVER_SCHEDULE = "12:00 计划退房 → 12:00–15:00 计划清洁 → 15:00 起入住"
_CHECK_OUT_SCHEDULE = "12:00 计划退房 → 12:00–15:00 计划清洁"
_CHECK_IN_SCHEDULE = "15:00 起入住"
# 今日周转优先级：同日进出最紧急，空置和房态未知最不紧急。
_OCCUPANCY_TURNOVER_RANK: dict[RoomOccupancyStatus, int] = {
    RoomOccupancyStatus.TURNOVER_TODAY: 0,
    RoomOccupancyStatus.ARRIVING_TODAY: 1,
    RoomOccupancyStatus.DEPARTING_TODAY: 2,
    RoomOccupancyStatus.OCCUPIED: 3,
    RoomOccupancyStatus.VACANT: 4,
    RoomOccupancyStatus.UNKNOWN: 4,
}
# 运营准备优先级：维修最需要人工介入，就绪和在住无需今日准备。
_READINESS_RANK: dict[RoomOperationalStatus, int] = {
    RoomOperationalStatus.MAINTENANCE: 0,
    RoomOperationalStatus.CLEANING: 1,
    RoomOperationalStatus.PENDING_INSPECTION: 1,
    RoomOperationalStatus.NOT_STARTED: 2,
    RoomOperationalStatus.OCCUPIED: 3,
    RoomOperationalStatus.READY: 3,
}
# 需要今天人工介入的运营准备状态；与排序权重表分开维护，避免调整权重时静默改变分组。
_ATTENTION_READINESS: frozenset[RoomOperationalStatus] = frozenset(
    {
        RoomOperationalStatus.MAINTENANCE,
        RoomOperationalStatus.CLEANING,
        RoomOperationalStatus.PENDING_INSPECTION,
    }
)
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
    # 归并后的分组要带上它覆盖的全部记录编号：批量处置按编号提交，
    # 不靠「再查一次同样的筛选」，避免提交时的集合与页面上看到的不是同一批。
    record_ids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class RoomDayOperation:
    """表示一个房间在某日的入住、退房及占用事实。"""

    local_date: date
    arrival_count: int
    departure_count: int
    occupied: bool


@dataclass(frozen=True, slots=True)
class RoomStayInterval:
    """住宿条最小单位：一笔订单的区间与身份，供时间轴与倒计时同源。

    同名不同客是不同订单；相邻订单即使同一客户也保留订单边界，不在此改写连住数据。
    """

    order_id: int
    customer_id: int | None
    guest_name: str | None
    check_in: date
    check_out: date
    checkout_verified: bool = False

    @property
    def nights(self) -> int:
        """晚数=退房日−入住日，占两个日期格不代表两晚。"""
        return (self.check_out - self.check_in).days


@dataclass(frozen=True, slots=True)
class RoomEvent:
    """一次退房或入住事件，绑定同一订单的身份与计划绝对时刻。

    target 为服务端按 Asia/Shanghai 构造的计划绝对时刻（退房日 12:00 或入住日
    15:00）；倒计时文本由前端按经过时间计算。ambiguous 为真时无法唯一确定订单，
    不暴露具体姓名，页面显示「有多笔安排」。
    """

    kind: str
    order_id: int | None
    customer_id: int | None
    guest_name: str | None
    target: datetime
    verified: bool = False
    ambiguous: bool = False
    # 服务端构造的绝对时间文案与初始状态词：模板不解析中文日期；无 JS 时也要有
    # 「绝对时间 + 状态」，不至于只剩空倒计时。实时的「还有 X」由前端按经过时间算。
    target_label: str = ""
    status_word: str = ""


@dataclass(frozen=True, slots=True)
class RoomEvents:
    """一间房当前的退房／入住事件、计划周转间隔与住宿条集合。"""

    checkout: RoomEvent | None
    checkin: RoomEvent | None
    turnover_gap_minutes: int | None
    is_consecutive: bool
    intervals: tuple[RoomStayInterval, ...]


@dataclass(frozen=True, slots=True)
class RoomPrimaryStatus:
    """房间主状态的展示结论：无 PII 的状态词 + 单独的客人名字段。

    状态词本身不含姓名，客人名单列在 guest_name，由模板拼「状态 · 客人」；这样
    状态词可复用、可断言，姓名只在需要时出现在页面上。
    """

    label: str
    tone: str = "neutral"
    guest_name: str | None = None
    next_guest_name: str | None = None
    schedule_line: str = ""
    schedule_phase: str = ""
    is_consecutive: bool = False
    checkout_verified: bool = False


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
    # 与 next_action 同源的去向；确实没有可靠入口时为 None，页面不渲染空按钮。
    next_action_url: str | None = None
    # 按钮文案与去向同源：去任务列表说「去处理」，去房间详情说「查看房间准备情况」。
    next_action_label: str = "去处理"
    source_stale: bool = True
    # 主状态展示结论（无 PII）与客人名分列；模板拼「状态 · 客人」。
    primary_status: str = "今日无订单占用"
    primary_tone: str = "neutral"
    guest_name: str | None = None
    next_guest_name: str | None = None
    schedule_line: str = ""
    schedule_phase: str = ""
    is_consecutive: bool = False
    # 住宿条与双事件（Spec §4/§5）：时间轴按订单区间画，退房/入住倒计时绑定同一订单。
    stay_intervals: tuple["RoomStayInterval", ...] = ()
    checkout_event: "RoomEvent | None" = None
    checkin_event: "RoomEvent | None" = None
    turnover_gap_minutes: int | None = None

    @property
    def today_arrival(self) -> bool:
        """返回今日是否至少有一笔入住。"""
        return self.today_arrival_count > 0

    @property
    def today_departure(self) -> bool:
        """返回今日是否至少有一笔退房。"""
        return self.today_departure_count > 0

    @property
    def needs_attention(self) -> bool:
        """判断房间今天是否需要人工介入；其余房间在页面上降为次级信息。"""
        return (
            self.source_stale
            or self.overdue_task_count > 0
            or self.today_arrival
            or self.today_departure
            or self.status in _ATTENTION_READINESS
        )


@dataclass(frozen=True, slots=True)
class TimelineBar:
    """住宿条在日期网格上的位置与身份（Spec §7）。

    start_col 为 1 起的 CSS Grid 起始列，span 为跨列数；left/right_continues 表示
    住宿真实起止超出当前窗口、需要前／后延续标记。身份随订单，姓名只出现一次。
    """

    order_id: int
    customer_id: int | None
    guest_name: str | None
    nights: int
    start_col: int
    span: int
    left_continues: bool
    right_continues: bool
    checkout_verified: bool


@dataclass(frozen=True, slots=True)
class SevenDayRoomItem:
    """表示一个房间近期运营时间轴；保留类名以兼容既有调用方。"""

    property_id: int
    room_number: str | None
    room_title: str
    days: tuple[RoomDayOperation, ...]
    bars: tuple[TimelineBar, ...] = ()
    total_columns: int = 0


def _room_risk_sort_key(item: RoomOperationItem) -> tuple[int, int, int, int, int, str, int]:
    """按同步可信度、今日周转、逾期任务和运营准备给出确定性的风险优先顺序。"""
    return (
        0 if item.source_stale else 1,
        _OCCUPANCY_TURNOVER_RANK[item.occupancy_status],
        -item.overdue_task_count,
        _READINESS_RANK[item.status],
        -item.open_task_count,
        item.room_title,
        item.property_id,
    )


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

    @property
    def attention_rooms(self) -> tuple[RoomOperationItem, ...]:
        """返回今天需要人工介入的房间，保持已经排好的风险优先顺序。"""
        return tuple(room for room in self.rooms if room.needs_attention)

    @property
    def stable_rooms(self) -> tuple[RoomOperationItem, ...]:
        """返回今天无需介入的稳定房间，页面按次级信息折叠展示。"""
        return tuple(room for room in self.rooms if not room.needs_attention)

    @property
    def attention_room_count(self) -> int:
        """返回需要人工介入的房间数量。"""
        return len(self.attention_rooms)

    @property
    def timeline_past_days(self) -> int:
        """返回时间轴中已过去的天数，供页面如实描述覆盖范围。"""
        return TIMELINE_PAST_DAYS

    @property
    def timeline_start_date(self) -> date:
        """返回时间轴第一天；时间轴并非只覆盖未来若干天。"""
        return self.local_date - timedelta(days=TIMELINE_PAST_DAYS)

    @property
    def timeline_end_date(self) -> date:
        """返回时间轴最后一天。"""
        return self.local_date + timedelta(days=self.horizon_days)

    def timeline_for(self, property_id: int) -> SevenDayRoomItem | None:
        """按房间主键取时间轴，避免模板依赖两个序列的下标对齐。"""
        for item in self.seven_day_rooms:
            if item.property_id == property_id:
                return item
        return None


class AdminOperationsRepositoryPort(Protocol):
    """定义运营页面服务使用的固定批量查询接口。"""

    async def prepare_consistent_read(self) -> None:
        """在业务查询前固定只读快照。"""

    async def list_attention(self) -> tuple[AttentionRecord, ...]:
        """返回分领域人工事项。"""

    async def count_attention(self) -> int:
        """返回人工事项总数，与 list_attention 同源。"""

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
        record_ids: tuple[int, ...] = (),
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
            # 凭证投递不是任务，送进任务列表必然什么也看不到；房源凭证页签才是它的归宿。
            target_url = (
                f"/employee/properties/{record.property_id}?tab=credentials"
                if record.property_id is not None
                else "/employee/properties"
            )
        elif record.kind == "reminder":
            title = "入住提醒需要跟进"
            summary = (
                f"该房源共有 {related_count} 项入住提醒需要跟进{room_context}"
                if related_count > 1
                else f"提醒 #{record.record_id}：{status_text}{room_context}"
            )
            # 提醒同样不是任务。它就地在本页处置，不再把人送去一个没有它的列表。
            target_url = ""
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
                # 少了状态条件就会落到该房源的全部开放任务上，点进去看到的
                # 是一堆已分派任务，而用户点的是「待确认」。
                target_url = (
                    f"/employee/tasks?property_id={record.property_id}"
                    "&status_filter=pending_confirmation"
                )
            else:
                target_url = "/employee/tasks?status_filter=pending_confirmation"
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
            record_ids=record_ids or (record.record_id,),
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
                    record_ids=tuple(item.record_id for item in records_in_group),
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
        local_now: datetime,
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
            for offset in range(-TIMELINE_PAST_DAYS, horizon_days + 1)
        )

        for room in rooms:
            raw_stays = stays_by_room.get(room.property_id, [])
            # 先合并同一客人的相邻续住订单：主状态、事件、住宿条、下一步与到离店计数
            # 全部基于合并结果，避免同一位客人的连续入住被显示成换客周转。
            room_stays = AdminOperationsService._merge_consecutive_stays(raw_stays)
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
            primary = AdminOperationsService._primary_status(
                local_now=local_now,
                source_stale=source_stale,
                room_stays=room_stays,
            )
            events = AdminOperationsService._room_events(
                local_now=local_now, room_stays=room_stays
            )
            # 下一位客人名：取今日之后最早一笔到店对应的客人。
            future_arrival_stays = sorted(
                (s for s in room_stays if s.check_in_date > local_date),
                key=lambda s: s.check_in_date,
            )
            next_guest_name = (
                future_arrival_stays[0].guest_name if future_arrival_stays else None
            )
            next_step = AdminOperationsService._next_step(
                source_stale=source_stale,
                operational_status=room.status,
                arrivals=today_arrivals,
                departures=today_departures,
                occupied=occupied_today,
                task_count=task_count,
                next_arrival=next_arrival,
                property_id=room.property_id,
            )
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
                    next_action=next_step[0],
                    next_action_url=next_step[1],
                    next_action_label=next_step[2],
                    source_stale=source_stale,
                    primary_status=primary.label,
                    primary_tone=primary.tone,
                    guest_name=primary.guest_name,
                    next_guest_name=next_guest_name,
                    schedule_line=primary.schedule_line,
                    schedule_phase=primary.schedule_phase,
                    is_consecutive=primary.is_consecutive,
                    stay_intervals=events.intervals,
                    checkout_event=events.checkout,
                    checkin_event=events.checkin,
                    turnover_gap_minutes=events.turnover_gap_minutes,
                )
            )
            # 住宿条网格：窗口首日为 days[0]，每笔订单按日期偏移落到网格列；超出窗口
            # 的住宿裁切并标延续。checkout 日一列也纳入（当天 12:00 前仍占用）。
            window_start = days[0]
            total_columns = len(days)
            bars: list[TimelineBar] = []
            for interval in events.intervals:
                ci_off = (interval.check_in - window_start).days
                co_off = (interval.check_out - window_start).days
                start_col = max(ci_off, 0) + 1
                end_line = min(co_off, total_columns - 1) + 2
                bars.append(
                    TimelineBar(
                        order_id=interval.order_id,
                        customer_id=interval.customer_id,
                        guest_name=interval.guest_name,
                        nights=interval.nights,
                        start_col=start_col,
                        span=max(1, end_line - start_col),
                        left_continues=ci_off < 0,
                        right_continues=co_off > total_columns - 1,
                        checkout_verified=interval.checkout_verified,
                    )
                )
            matrix_items.append(
                SevenDayRoomItem(
                    property_id=room.property_id,
                    room_number=room.room_number,
                    room_title=room.room_title,
                    bars=tuple(bars),
                    total_columns=total_columns,
                    days=tuple(
                        RoomDayOperation(
                            local_date=day,
                            arrival_count=sum(stay.check_in_date == day for stay in room_stays),
                            departure_count=sum(
                                stay.check_out_date == day for stay in room_stays
                            ),
                            occupied=any(
                                stay.check_in_date <= day < stay.check_out_date
                                for stay in room_stays
                            ),
                        )
                        for day in days
                    ),
                )
            )
        ordered = sorted(
            zip(room_items, matrix_items, strict=True),
            key=lambda pair: _room_risk_sort_key(pair[0]),
        )
        return (
            tuple(room for room, _ in ordered),
            tuple(matrix for _, matrix in ordered),
        )

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
        return AdminOperationsService._next_step(
            source_stale=source_stale,
            operational_status=operational_status,
            arrivals=arrivals,
            departures=departures,
            occupied=occupied,
            task_count=task_count,
            next_arrival=next_arrival,
            property_id=0,
        )[0]

    @staticmethod
    def _merge_consecutive_stays(
        room_stays: list[StayRecord],
    ) -> list[StayRecord]:
        """把相邻的同一客人订单合并成一段连续住宿。

        百居易会把同一客人的续住拆成多笔订单，甚至各自一个客户号（去重漏合）。
        用户明确：连续入住不拆成两单。判据是「前一笔退房日 == 后一笔入住日」且
        「同一非空客户号，或同一非空姓名」——同房、日期相接、同名，视为同一客人
        续住。仍保留首笔订单号作为客户档案链接，不改写底层订单数据。
        """
        ordered = sorted(room_stays, key=lambda s: (s.check_in_date, s.order_id))
        merged: list[StayRecord] = []
        for stay in ordered:
            if merged:
                prev = merged[-1]
                same_guest = (
                    (prev.customer_id is not None and prev.customer_id == stay.customer_id)
                    or (bool(prev.guest_name) and prev.guest_name == stay.guest_name)
                )
                if same_guest and prev.check_out_date == stay.check_in_date:
                    merged[-1] = replace(
                        prev,
                        check_out_date=stay.check_out_date,
                        checkout_observed_on=stay.checkout_observed_on,
                    )
                    continue
            merged.append(stay)
        return merged

    @staticmethod
    def _room_events(
        *,
        local_now: datetime,
        room_stays: list[StayRecord],
    ) -> "RoomEvents":
        """按订单事实与当地时刻选出退房／入住事件、计划周转间隔与住宿条。

        规则见 Spec §5：退房取当前或今日离店的那笔订单（不含今日才到店的），入住
        取今日及以后最早一笔；候选多于一笔且无法唯一确定时标记 ambiguous；相邻订单
        同一客户为续住、不计算周转。姓名与日期一律取自同一订单对象，绝不跨订单拼接。
        """
        today = local_now.date()
        intervals = tuple(
            RoomStayInterval(
                order_id=s.order_id,
                customer_id=s.customer_id,
                guest_name=s.guest_name,
                check_in=s.check_in_date,
                check_out=s.check_out_date,
                checkout_verified=s.checkout_observed_on == s.check_out_date,
            )
            for s in sorted(room_stays, key=lambda s: (s.check_in_date, s.order_id))
        )

        def _noon(day: date) -> datetime:
            return datetime.combine(day, CHECK_OUT_TIME.replace(tzinfo=None), tzinfo=WUHAN_TIMEZONE)

        def _afternoon(day: date) -> datetime:
            return datetime.combine(day, CHECK_IN_TIME.replace(tzinfo=None), tzinfo=WUHAN_TIMEZONE)

        def _abs_label(kind: str, target: datetime) -> str:
            """服务端构造绝对时间文案；模板只渲染，不解析中文日期。"""
            delta_days = (target.date() - today).days
            if delta_days == 0:
                day_word = "今天"
            elif delta_days == 1:
                day_word = "明天"
            elif delta_days == -1:
                day_word = "昨天"
            else:
                day_word = f"{target.month}月{target.day}日"
            clock = f"{target.hour:02d}:{target.minute:02d}"
            if kind == "checkin":
                return f"{day_word} {clock} 起入住"
            return f"{day_word} {clock}（计划）"

        def _status_word(
            kind: str, target: datetime, *, verified: bool, ambiguous: bool
        ) -> str:
            """渲染时刻的初始状态词；实时「还有 X」由前端补足。"""
            if ambiguous:
                return "有多笔退房安排" if kind == "checkout" else "有多笔入住安排"
            if kind == "checkout":
                if verified:
                    return "已退房"
                if local_now < target:
                    return "距计划退房"
                return "退房待确认"
            if local_now < target:
                return "距可入住时间"
            return "到店待确认"

        def _fill(event: RoomEvent) -> RoomEvent:
            """补齐事件的绝对文案与初始状态词。"""
            return replace(
                event,
                target_label=_abs_label(event.kind, event.target),
                status_word=_status_word(
                    event.kind,
                    event.target,
                    verified=event.verified,
                    ambiguous=event.ambiguous,
                ),
            )

        # 退房候选：今日离店的订单，或在今日之前就开始、跨过今天的订单（不含今日才
        # 到店的，那属于入住而非退房）。
        checkout_candidates = [
            s for s in room_stays
            if s.check_out_date == today
            or (s.check_in_date < today < s.check_out_date)
        ]
        # 入住候选：今日及以后到店，取最早一笔；同一最早日期多笔即为歧义。
        arriving = sorted(
            (s for s in room_stays if s.check_in_date >= today),
            key=lambda s: (s.check_in_date, s.order_id),
        )

        checkout: RoomEvent | None = None
        if len(checkout_candidates) > 1:
            checkout = RoomEvent(
                kind="checkout",
                order_id=None,
                customer_id=None,
                guest_name=None,
                target=_noon(today),
                ambiguous=True,
            )
        elif checkout_candidates:
            c = checkout_candidates[0]
            checkout = RoomEvent(
                kind="checkout",
                order_id=c.order_id,
                customer_id=c.customer_id,
                guest_name=c.guest_name,
                target=_noon(c.check_out_date),
                verified=c.checkout_observed_on == c.check_out_date,
            )

        checkin: RoomEvent | None = None
        if arriving:
            earliest = arriving[0].check_in_date
            same_day = [s for s in arriving if s.check_in_date == earliest]
            if len(same_day) > 1:
                checkin = RoomEvent(
                    kind="checkin",
                    order_id=None,
                    customer_id=None,
                    guest_name=None,
                    target=_afternoon(earliest),
                    ambiguous=True,
                )
            else:
                a = same_day[0]
                checkin = RoomEvent(
                    kind="checkin",
                    order_id=a.order_id,
                    customer_id=a.customer_id,
                    guest_name=a.guest_name,
                    target=_afternoon(a.check_in_date),
                )

        # 续住：退房订单与入住订单相邻且同一非空客户；不计算周转间隔。
        is_consecutive = bool(
            checkout is not None
            and checkin is not None
            and not checkout.ambiguous
            and not checkin.ambiguous
            and checkout.customer_id is not None
            and checkout.customer_id == checkin.customer_id
        )

        turnover_gap_minutes: int | None = None
        if (
            checkout is not None
            and checkin is not None
            and not checkout.ambiguous
            and not checkin.ambiguous
            and not is_consecutive
            and checkout.target.date() == today
            and checkin.target.date() == today
        ):
            turnover_gap_minutes = int(
                (checkin.target - checkout.target).total_seconds() // 60
            )

        return RoomEvents(
            checkout=_fill(checkout) if checkout is not None else None,
            checkin=_fill(checkin) if checkin is not None else None,
            turnover_gap_minutes=turnover_gap_minutes,
            is_consecutive=is_consecutive,
            intervals=intervals,
        )

    @staticmethod
    def _primary_status(
        *,
        local_now: datetime,
        source_stale: bool,
        room_stays: list[StayRecord],
    ) -> "RoomPrimaryStatus":
        """按订单事实与当地时刻推导「一眼看懂」的主状态。

        规则由用户明确：下一位到店即视为上一位已退房；今日仅退房时过 15:00 默认
        按已退房进入下一轮；同一客人连续订单为续住而非周转；只有实际退房日期
        （checkout_observed_on）等于退房日才算已核验退房，否则一律「按计划」。
        同步过期时不做任何推断。
        """
        today = local_now.date()
        after_checkin_time = local_now.timetz() >= CHECK_IN_TIME
        if source_stale:
            return RoomPrimaryStatus(label="入住信息待核实", tone="warning")

        arrivals = [s for s in room_stays if s.check_in_date == today]
        departures = [s for s in room_stays if s.check_out_date == today]
        mid_stay = [
            s for s in room_stays if s.check_in_date < today < s.check_out_date
        ]

        def _verified(dep: StayRecord) -> bool:
            """实际退房日期等于退房日才算已核验。"""
            return dep.checkout_observed_on == dep.check_out_date

        if arrivals and departures:
            arr, dep = arrivals[0], departures[0]
            # 连住：同一位客人前后相接，不是不同客人的周转。
            if arr.customer_id is not None and arr.customer_id == dep.customer_id:
                return RoomPrimaryStatus(
                    label="续住",
                    tone="neutral",
                    guest_name=arr.guest_name,
                    is_consecutive=True,
                )
            # 不同客人：到店即视为上一位已退房，主行是到店客人。
            return RoomPrimaryStatus(
                label="今日到店",
                tone="info",
                guest_name=arr.guest_name,
                schedule_line=_TURNOVER_SCHEDULE,
                schedule_phase=AdminOperationsService._schedule_phase(local_now),
                checkout_verified=_verified(dep),
            )
        if arrivals:
            arr = arrivals[0]
            return RoomPrimaryStatus(
                label="今日到店",
                tone="info",
                guest_name=arr.guest_name,
                schedule_line=_CHECK_IN_SCHEDULE,
                schedule_phase=AdminOperationsService._schedule_phase(local_now),
            )
        if departures:
            dep = departures[0]
            verified = _verified(dep)
            if verified:
                return RoomPrimaryStatus(
                    label="已退房", tone="neutral", checkout_verified=True
                )
            if after_checkin_time:
                # 15:00 后未核验：默认按计划已退房进入下一轮。
                return RoomPrimaryStatus(label="按计划已退房", tone="neutral")
            return RoomPrimaryStatus(
                label="今日离店",
                tone="info",
                guest_name=dep.guest_name,
                schedule_line=_CHECK_OUT_SCHEDULE,
                schedule_phase=AdminOperationsService._schedule_phase(local_now),
            )
        if mid_stay:
            return RoomPrimaryStatus(
                label="住宿期内", tone="neutral", guest_name=mid_stay[0].guest_name
            )
        return RoomPrimaryStatus(label="今日无订单占用", tone="neutral")

    @staticmethod
    def _schedule_phase(local_now: datetime) -> str:
        """按当地时刻标出当前处于计划节奏的哪一段，供页面高亮。"""
        current = local_now.timetz()
        if current < CHECK_OUT_TIME:
            return "before_checkout"
        if current < CHECK_IN_TIME:
            return "cleaning"
        return "checkin"

    @staticmethod
    def _next_step(
        *,
        source_stale: bool,
        operational_status: RoomOperationalStatus,
        arrivals: int,
        departures: int,
        occupied: bool,
        task_count: RoomTaskCountRecord,
        next_arrival: date | None,
        property_id: int,
    ) -> tuple[str, str | None, str]:
        """同时给出「下一步是什么」「去哪做」和「按钮怎么说」，三者出自同一组分支。

        原先只产出一句文字，管家读完还得自己回到列表重新筛选才能动手。文字与
        去向分开算就会各说各话，因此放在同一个函数里；确实没有可靠去向时返回
        None，页面不渲染空按钮，而不是编一个看起来能点的链接。

        按钮文案也一并算出来：到店/离店当天却一项开放任务都没有时，去向是房间
        详情而不是任务列表，此时还写「去处理」就又成了一句兑现不了的话。
        """
        tasks_url = f"/employee/tasks?property_id={property_id}"
        room_url = f"/employee/properties/{property_id}"
        handle = "去处理"
        inspect = "查看房间准备情况"
        # 今日有到店或离店，却没有任何开放任务：进任务列表只会看到空结果。
        # 改去房间详情并说明任务尚未生成——不在这里替业务补造任务。
        no_open_tasks = task_count.count == 0
        if source_stale:
            # 同步不可信时没有能真正解决它的页面入口，不给按钮。
            return "先确认百居易实时房态", None, handle
        if task_count.overdue_count:
            return (
                f"优先处理 {task_count.overdue_count} 项逾期任务",
                f"{tasks_url}&overdue=true",
                handle,
            )
        if arrivals or departures:
            if arrivals and departures:
                reason = "安排退房周转并核对今日入住"
            elif departures:
                reason = "安排退房检查与周转"
            elif operational_status is RoomOperationalStatus.READY:
                reason = "核对入住资料并接待"
            else:
                reason = "优先完成房间准备并接待入住"
            if no_open_tasks:
                return reason, room_url, inspect
            return reason, tasks_url, handle
        if operational_status is RoomOperationalStatus.MAINTENANCE:
            return "跟进维修并确认房间可用性", room_url, inspect
        if task_count.count:
            return f"推进 {task_count.count} 项开放任务", tasks_url, handle
        if occupied:
            return "关注在住服务", room_url, inspect
        if next_arrival is not None:
            if no_open_tasks:
                return (
                    f"{next_arrival.month}月{next_arrival.day}日前完成房间准备",
                    room_url,
                    inspect,
                )
            return (
                f"{next_arrival.month}月{next_arrival.day}日前完成房间准备",
                tasks_url,
                handle,
            )
        return "暂无近期运营动作", None, handle

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
            local_date - timedelta(days=TIMELINE_PAST_DAYS),
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
            local_now=observed_at.astimezone(WUHAN_TIMEZONE),
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
