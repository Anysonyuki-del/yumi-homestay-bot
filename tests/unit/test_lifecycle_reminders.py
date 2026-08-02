from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from homestay_bot.domain.enums import ReminderStatus, ReminderType
from homestay_bot.services.lifecycle_reminders import (
    LifecycleReminderService,
    ReminderSendContext,
    ReminderSendUnavailable,
    TourismReminderWeatherProvider,
)
from homestay_bot.worker import RetrySafeJobError


class ReminderRepositoryStub:
    """记录提醒计划和状态变化。"""

    def __init__(self) -> None:
        """初始化一笔武汉订单和可发送上下文。"""
        self.order = SimpleNamespace(
            id=11,
            customer_id=7,
            property_id=101,
            check_in_date=date(2026, 8, 2),
            check_out_date=date(2026, 8, 3),
            status="confirmed",
        )
        self.reminders: list[SimpleNamespace] = []
        self.accepted: list[tuple[int, str]] = []
        self.manual: list[tuple[int, str]] = []
        self.cancelled_order_ids: list[int] = []
        self.active_schedule_keys: list[
            tuple[ReminderType, date]
        ] = []
        self.context: ReminderSendContext | None = None

    async def require_order(self, order_id):
        """返回固定有效订单。"""
        assert order_id == 11
        return self.order

    async def ensure_reminder(
        self,
        *,
        order_id,
        reminder_type,
        scheduled_local_date,
        scheduled_at,
    ):
        """按计划类型创建测试提醒。"""
        item = SimpleNamespace(
            id=len(self.reminders) + 1,
            order_id=order_id,
            reminder_type=reminder_type,
            scheduled_local_date=scheduled_local_date,
            scheduled_at=scheduled_at,
            status=ReminderStatus.SCHEDULED,
        )
        self.reminders.append(item)
        return item

    async def require_send_context(self, reminder_id):
        """返回当前提醒的安全发送上下文。"""
        assert self.context is not None
        return self.context

    async def mark_platform_accepted(self, reminder_id, message_id):
        """记录企业微信平台已受理。"""
        self.accepted.append((reminder_id, message_id))

    async def find_by_message_id(self, message_id):
        """按平台消息编号返回提醒。"""
        if message_id != "msg-accepted":
            return None
        return SimpleNamespace(id=1, order_id=11)

    async def mark_manual_followup(self, reminder_id, reason):
        """记录转人工状态。"""
        self.manual.append((reminder_id, reason))

    async def cancel_for_order(self, order_id):
        """记录取消订单的提醒撤销。"""
        self.cancelled_order_ids.append(order_id)
        return 4

    async def cancel_obsolete_for_order(self, order_id, active_keys):
        """记录改期后仍有效的提醒键。"""
        assert order_id == self.order.id
        self.active_schedule_keys = list(active_keys)
        return 0


class JobQueueStub:
    """记录四个定时任务。"""

    def __init__(self) -> None:
        """初始化任务列表。"""
        self.items: list[dict[str, object]] = []

    async def enqueue(
        self,
        job_type,
        payload,
        *,
        available_at,
        dedupe_key,
    ):
        """记录提醒入队参数。"""
        self.items.append(
            {
                "job_type": job_type,
                "payload": payload,
                "available_at": available_at,
                "dedupe_key": dedupe_key,
            }
        )


class SenderStub:
    """记录客人提醒并返回平台消息编号。"""

    def __init__(self, error=None) -> None:
        """配置可选发送错误。"""
        self.error = error
        self.calls: list[dict[str, str]] = []

    async def send_text(self, open_kfid, external_userid, content):
        """记录安全目的地和提醒正文。"""
        self.calls.append(
            {
                "open_kfid": open_kfid,
                "external_userid": external_userid,
                "content": content,
            }
        )
        if self.error is not None:
            raise self.error
        return "msg-accepted"


class TaskStub:
    """记录系统创建的人工联系任务。"""

    def __init__(self) -> None:
        """初始化任务列表。"""
        self.items: list[tuple[int, str]] = []

    async def create_manual_contact(self, reminder, reason):
        """按提醒记录人工联系原因。"""
        self.items.append((reminder.id, reason))


class WeatherStub:
    """返回固定天气摘要或模拟查询失败。"""

    def __init__(self, *, error=None) -> None:
        """保存可选异常并记录查询参数。"""
        self.error = error
        self.calls: list[tuple[str, date]] = []

    async def forecast(self, district, target_date):
        """返回不含链接的短天气摘要。"""
        self.calls.append((district, target_date))
        if self.error is not None:
            raise self.error
        return "入住当天有阵雨，气温约 26 至 32℃，建议带伞。"


class TourismSearchStub:
    """记录天气适配器传给联网旅游搜索的参数。"""

    def __init__(self) -> None:
        """初始化调用列表。"""
        self.calls = []

    async def search(self, **fields):
        """返回固定联网天气摘要。"""
        self.calls.append(fields)
        return "有阵雨，建议带伞。"


def build_service(
    repository: ReminderRepositoryStub,
    *,
    sender: SenderStub | None = None,
    tasks: TaskStub | None = None,
    weather: WeatherStub | None = None,
    now: datetime | None = None,
) -> tuple[LifecycleReminderService, JobQueueStub, SenderStub, TaskStub]:
    """装配可观察的生命周期服务。"""
    jobs = JobQueueStub()
    selected_sender = sender or SenderStub()
    selected_tasks = tasks or TaskStub()
    service = LifecycleReminderService(
        repository,
        jobs,
        selected_sender,
        selected_tasks,
        weather=weather,
        now_provider=lambda: now
        or datetime(2026, 8, 1, 9, tzinfo=UTC),
    )
    return service, jobs, selected_sender, selected_tasks


@pytest.mark.asyncio
async def test_delivery_commits_context_before_external_send() -> None:
    """提醒读取锁应在调用天气或企业微信前释放。"""
    sequence: list[str] = []

    async def commit_before_external() -> None:
        """记录外部调用前提交。"""
        sequence.append("committed")

    class RecordingSender(SenderStub):
        """记录企业微信调用顺序。"""

        async def send_text(self, open_kfid, external_userid, content):
            """记录发送发生在提交之后。"""
            sequence.append("network")
            return await super().send_text(open_kfid, external_userid, content)

    repository = ReminderRepositoryStub()
    repository.context = ReminderSendContext(
        reminder=SimpleNamespace(reminder_type=ReminderType.ARRIVAL_DAY),
        order=repository.order,
        property_title="春和景明",
        district="洪山区",
        address_hint="附近",
        parking_instructions="请联系管家",
        open_kfid="wk-1",
        external_userid="wm-1",
        last_guest_at=datetime(2026, 8, 1, 8, tzinfo=UTC),
        sent_count=0,
    )
    sender = RecordingSender()
    service = LifecycleReminderService(
        repository,
        JobQueueStub(),
        sender,
        TaskStub(),
        now_provider=lambda: datetime(2026, 8, 1, 9, tzinfo=UTC),
        before_external=commit_before_external,
    )

    await service.deliver(11)

    assert sequence == ["committed", "network"]


@pytest.mark.asyncio
async def test_schedule_uses_four_wuhan_local_times_and_dedupe_keys() -> None:
    """四个提醒必须按武汉时间换算 UTC，并使用订单级幂等键。"""
    repository = ReminderRepositoryStub()
    service, jobs, _, _ = build_service(repository)

    reminders = await service.schedule_for_order(11)

    assert [item.reminder_type for item in reminders] == [
        ReminderType.PRE_ARRIVAL,
        ReminderType.ARRIVAL_DAY,
        ReminderType.CHECKOUT,
        ReminderType.THANK_YOU,
    ]
    assert [item.scheduled_at for item in reminders] == [
        datetime(2026, 8, 1, 10, tzinfo=UTC),
        datetime(2026, 8, 2, 2, tzinfo=UTC),
        datetime(2026, 8, 3, 1, tzinfo=UTC),
        datetime(2026, 8, 3, 6, tzinfo=UTC),
    ]
    assert [item["dedupe_key"] for item in jobs.items] == [
        "lifecycle:11:pre_arrival:2026-08-01",
        "lifecycle:11:arrival_day:2026-08-02",
        "lifecycle:11:checkout:2026-08-03",
        "lifecycle:11:thank_you:2026-08-03",
    ]
    assert repository.active_schedule_keys == [
        (ReminderType.PRE_ARRIVAL, date(2026, 8, 1)),
        (ReminderType.ARRIVAL_DAY, date(2026, 8, 2)),
        (ReminderType.CHECKOUT, date(2026, 8, 3)),
        (ReminderType.THANK_YOU, date(2026, 8, 3)),
    ]


@pytest.mark.asyncio
async def test_cancelled_order_cancels_reminders_without_new_jobs() -> None:
    """取消订单必须撤销提醒，不能继续登记发送任务。"""
    repository = ReminderRepositoryStub()
    repository.order.status = "cancelled"
    service, jobs, _, _ = build_service(repository)

    reminders = await service.schedule_for_order(11)

    assert reminders == []
    assert repository.cancelled_order_ids == [11]
    assert jobs.items == []


def send_context(
    repository: ReminderRepositoryStub,
    *,
    last_guest_at: datetime,
    sent_count: int = 0,
) -> ReminderSendContext:
    """构造只属于当前订单客户的提醒发送上下文。"""
    reminder = SimpleNamespace(
        id=1,
        order_id=11,
        reminder_type=ReminderType.PRE_ARRIVAL,
        status=ReminderStatus.SCHEDULED,
    )
    context = ReminderSendContext(
        reminder=reminder,
        order=repository.order,
        property_title="长江中心",
        district="武昌区",
        address_hint="地铁站步行约 5 分钟",
        parking_instructions="到店前联系管家确认车位",
        open_kfid="wk-1",
        external_userid="wm-customer-7",
        last_guest_at=last_guest_at,
        sent_count=sent_count,
    )
    repository.context = context
    return context


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("last_guest_delta", "sent_count", "reason"),
    [
        (timedelta(hours=49), 0, "send_window_expired"),
        (timedelta(hours=1), 5, "send_count_limit"),
    ],
)
async def test_precheck_failure_creates_manual_contact_without_send(
    last_guest_delta,
    sent_count,
    reason,
) -> None:
    """48 小时或五条限制必须在发送前转人工。"""
    now = datetime(2026, 8, 1, 9, tzinfo=UTC)
    repository = ReminderRepositoryStub()
    send_context(
        repository,
        last_guest_at=now - last_guest_delta,
        sent_count=sent_count,
    )
    service, _, sender, tasks = build_service(repository, now=now)

    await service.deliver(1)

    assert sender.calls == []
    assert tasks.items == [(1, reason)]
    assert repository.manual == [(1, reason)]
    assert repository.accepted == []


@pytest.mark.asyncio
async def test_platform_acceptance_is_not_recorded_as_customer_delivery() -> None:
    """发送接口成功只表示平台受理，不得虚构客户已收到。"""
    now = datetime(2026, 8, 1, 9, tzinfo=UTC)
    repository = ReminderRepositoryStub()
    send_context(
        repository,
        last_guest_at=now - timedelta(hours=1),
    )
    service, _, sender, _ = build_service(repository, now=now)

    await service.deliver(1)

    assert repository.accepted == [(1, "msg-accepted")]
    assert repository.manual == []
    assert "http://" not in sender.calls[0]["content"]
    assert "https://" not in sender.calls[0]["content"]


@pytest.mark.asyncio
async def test_pre_arrival_prefers_weather_but_failure_keeps_fallback() -> None:
    """天气查询成功时展示摘要，失败时仍发送安全固定提示。"""
    now = datetime(2026, 8, 1, 9, tzinfo=UTC)
    repository = ReminderRepositoryStub()
    send_context(
        repository,
        last_guest_at=now - timedelta(hours=1),
    )
    weather = WeatherStub()
    service, _, sender, _ = build_service(
        repository,
        weather=weather,
        now=now,
    )

    await service.deliver(1)

    assert weather.calls == [("武昌区", date(2026, 8, 2))]
    assert "入住当天有阵雨" in sender.calls[0]["content"]

    failed_weather = WeatherStub(error=RuntimeError("weather unavailable"))
    failed_service, _, failed_sender, _ = build_service(
        repository,
        weather=failed_weather,
        now=now,
    )
    await failed_service.deliver(1)

    assert "武汉天气可能变化" in failed_sender.calls[0]["content"]


@pytest.mark.asyncio
async def test_weather_adapter_passes_current_and_target_dates_to_search() -> None:
    """联网天气查询必须明确当前日期、入住日期和武汉区域。"""
    searcher = TourismSearchStub()
    provider = TourismReminderWeatherProvider(
        searcher,
        today_provider=lambda: date(2026, 7, 31),
    )

    summary = await provider.forecast(
        "武昌区",
        date(2026, 8, 2),
    )

    assert summary == "有阵雨，建议带伞。"
    assert searcher.calls[0]["queried_on"] == date(2026, 7, 31)
    assert "2026-08-02" in searcher.calls[0]["question"]
    assert "武昌区" in searcher.calls[0]["question"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_type", [4, 5, 6, 10])
async def test_async_send_failure_creates_manual_contact(
    fail_type,
) -> None:
    """企业微信异步失败事件必须按平台消息编号转人工。"""
    repository = ReminderRepositoryStub()
    service, _, _, tasks = build_service(repository)

    await service.handle_send_failure(
        "msg-accepted",
        fail_type=fail_type,
    )

    reason = f"wecom_fail_{fail_type}"
    assert tasks.items == [(1, reason)]
    assert repository.manual == [(1, reason)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reminder_type", "required", "forbidden"),
    [
        (
            ReminderType.ARRIVAL_DAY,
            "请告知预计到达时间",
            "二维码：http",
        ),
        (
            ReminderType.CHECKOUT,
            "证件、充电器",
            "好评",
        ),
        (
            ReminderType.THANK_YOU,
            "祝您一路顺风",
            "好评",
        ),
    ],
)
async def test_other_lifecycle_messages_follow_safe_content_rules(
    reminder_type,
    required,
    forbidden,
) -> None:
    """入住、退房和感谢提醒使用确定性文案且不索要好评。"""
    now = datetime(2026, 8, 1, 9, tzinfo=UTC)
    repository = ReminderRepositoryStub()
    context = send_context(
        repository,
        last_guest_at=now - timedelta(hours=1),
    )
    context.reminder.reminder_type = reminder_type
    service, _, sender, _ = build_service(repository, now=now)

    await service.deliver(1)

    content = sender.calls[0]["content"]
    assert required in content
    assert forbidden not in content
    assert "http://" not in content
    assert "https://" not in content


@pytest.mark.asyncio
async def test_connect_error_is_retry_safe_but_timeout_is_not_replayed() -> None:
    """连接未建立可重试，结果不明确的超时必须转人工。"""
    now = datetime(2026, 8, 1, 9, tzinfo=UTC)
    repository = ReminderRepositoryStub()
    send_context(
        repository,
        last_guest_at=now - timedelta(hours=1),
    )
    connect_sender = SenderStub(
        httpx.ConnectError("not connected")
    )
    connect_service, _, _, _ = build_service(
        repository,
        sender=connect_sender,
        now=now,
    )
    with pytest.raises(RetrySafeJobError):
        await connect_service.deliver(1)

    timeout_sender = SenderStub(httpx.ReadTimeout("uncertain"))
    timeout_tasks = TaskStub()
    timeout_service, _, _, _ = build_service(
        repository,
        sender=timeout_sender,
        tasks=timeout_tasks,
        now=now,
    )
    await timeout_service.deliver(1)

    assert timeout_tasks.items == [(1, "send_result_uncertain")]
    assert repository.accepted == []


@pytest.mark.asyncio
async def test_missing_verified_customer_context_creates_manual_contact() -> None:
    """订单未绑定可靠微信会话时不得猜测收件人。"""
    repository = ReminderRepositoryStub()
    reminder = SimpleNamespace(id=1, order_id=11)

    async def unavailable(reminder_id):
        """模拟仓储无法找到属于订单客户的已验证微信会话。"""
        raise ReminderSendUnavailable(
            reminder,
            "verified_wecom_conversation_missing",
        )

    repository.require_send_context = unavailable
    service, _, sender, tasks = build_service(repository)

    await service.deliver(1)

    assert sender.calls == []
    assert tasks.items == [
        (1, "verified_wecom_conversation_missing")
    ]
    assert repository.manual == [
        (1, "verified_wecom_conversation_missing")
    ]


@pytest.mark.asyncio
async def test_cancelled_reminder_job_finishes_without_manual_task() -> None:
    """撤销后遗留的定时任务应安静结束，不误报人工联系。"""
    repository = ReminderRepositoryStub()
    reminder = SimpleNamespace(id=1, order_id=11)

    async def cancelled(reminder_id):
        """模拟任务领取前提醒已经撤销。"""
        raise ReminderSendUnavailable(
            reminder,
            "reminder_not_scheduled",
            requires_manual=False,
        )

    repository.require_send_context = cancelled
    service, _, sender, tasks = build_service(repository)

    await service.deliver(1)

    assert sender.calls == []
    assert tasks.items == []
    assert repository.manual == []
