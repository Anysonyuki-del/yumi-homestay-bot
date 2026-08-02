import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import httpx

from homestay_bot.domain.enums import Language, ReminderType
from homestay_bot.integrations.wecom.api_client import WeComApiError
from homestay_bot.worker import RetrySafeJobError

WUHAN_TIMEZONE = ZoneInfo("Asia/Shanghai")
_URL_PATTERN = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)


@dataclass(frozen=True)
class ReminderSendContext:
    """保存一次主动提醒所需的最小订单、房源和会话事实。"""

    reminder: Any
    order: Any
    property_title: str
    district: str
    address_hint: str
    parking_instructions: str
    open_kfid: str
    external_userid: str
    last_guest_at: datetime
    sent_count: int


class ReminderSendUnavailable(LookupError):
    """表示提醒存在，但缺少可靠且属于订单客户的发送上下文。"""

    def __init__(
        self,
        reminder: Any,
        reason: str,
        *,
        requires_manual: bool = True,
    ) -> None:
        """保留内部提醒对象和固定原因码，不携带客户正文。"""
        super().__init__(reason)
        self.reminder = reminder
        self.reason = reason
        self.requires_manual = requires_manual


class ReminderRepository(Protocol):
    """定义生命周期计划和发送状态仓储。"""

    async def require_order(self, order_id: int) -> Any:
        """返回订单。"""

    async def ensure_reminder(
        self,
        *,
        order_id: int,
        reminder_type: ReminderType,
        scheduled_local_date: date,
        scheduled_at: datetime,
    ) -> Any:
        """幂等创建一项提醒。"""

    async def require_send_context(
        self,
        reminder_id: int,
    ) -> ReminderSendContext:
        """锁定提醒并返回客户隔离后的发送上下文。"""

    async def cancel_for_order(self, order_id: int) -> int:
        """撤销订单尚未发送的提醒。"""

    async def cancel_obsolete_for_order(
        self,
        order_id: int,
        active_keys: list[tuple[ReminderType, date]],
    ) -> int:
        """撤销订单改期前遗留的计划中提醒。"""

    async def mark_platform_accepted(
        self,
        reminder_id: int,
        message_id: str,
    ) -> None:
        """记录平台受理，不标记客户已收到。"""

    async def find_by_message_id(self, message_id: str) -> Any | None:
        """按企业微信消息编号查找提醒。"""

    async def mark_manual_followup(
        self,
        reminder_id: int,
        reason: str,
    ) -> None:
        """把提醒转为人工跟进。"""


class ReminderJobQueue(Protocol):
    """定义定时提醒任务入队接口。"""

    async def enqueue(
        self,
        job_type: str,
        payload: dict[str, Any],
        *,
        available_at: datetime,
        dedupe_key: str,
    ) -> Any:
        """持久化一项定时任务。"""


class ReminderSender(Protocol):
    """定义微信客服文本发送接口。"""

    async def send_text(
        self,
        open_kfid: str,
        external_userid: str,
        content: str,
    ) -> str:
        """发送文本并返回平台消息编号。"""


class ManualContactTasks(Protocol):
    """定义系统创建人工联系任务的边界。"""

    async def create_manual_contact(
        self,
        reminder: Any,
        reason: str,
    ) -> Any:
        """为未能自动送达的提醒创建幂等任务。"""


class ReminderWeatherProvider(Protocol):
    """定义入住前天气摘要查询边界。"""

    async def forecast(
        self,
        district: str,
        target_date: date,
    ) -> str:
        """返回指定武汉区域和日期的精简天气建议。"""


class TourismSearchPort(Protocol):
    """定义可复用的武汉联网查询边界。"""

    async def search(
        self,
        *,
        question: str,
        language: Language,
        queried_on: date,
    ) -> str:
        """执行带明确当前日期的武汉联网查询。"""


class TourismReminderWeatherProvider:
    """把现有武汉联网搜索适配为入住天气摘要。"""

    def __init__(
        self,
        searcher: TourismSearchPort,
        *,
        today_provider: Callable[[], date] | None = None,
    ) -> None:
        """注入联网搜索器和武汉本地日期。"""
        self._searcher = searcher
        self._today_provider = today_provider or (
            lambda: datetime.now(WUHAN_TIMEZONE).date()
        )

    async def forecast(
        self,
        district: str,
        target_date: date,
    ) -> str:
        """查询指定入住日天气，并要求短句、无链接的实用建议。"""
        area = district.strip() or "房源所在区域"
        return await self._searcher.search(
            question=(
                f"查询武汉{area}在{target_date.isoformat()}的天气。"
                "只用三句概括天气、气温和带伞或防晒建议，"
                "不要输出链接，也不要推荐景点。"
            ),
            language=Language.ZH,
            queried_on=self._today_provider(),
        )


class LifecycleReminderService:
    """调度四个武汉时区提醒，并执行安全发送与异步失败回退。"""

    _schedule = (
        (ReminderType.PRE_ARRIVAL, -1, time(18, 0)),
        (ReminderType.ARRIVAL_DAY, 0, time(10, 0)),
        (ReminderType.CHECKOUT, 0, time(9, 0)),
        (ReminderType.THANK_YOU, 0, time(14, 0)),
    )

    def __init__(
        self,
        reminders: ReminderRepository,
        jobs: ReminderJobQueue,
        sender: ReminderSender,
        tasks: ManualContactTasks,
        *,
        weather: ReminderWeatherProvider | None = None,
        now_provider: Callable[[], datetime] | None = None,
        before_external: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """注入持久化仓储、任务队列、发送器、事务边界和安全时钟。"""
        self._reminders = reminders
        self._jobs = jobs
        self._sender = sender
        self._tasks = tasks
        self._weather = weather
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._before_external = before_external

    async def schedule_for_order(self, order_id: int) -> list[Any]:
        """按订单入住退房日期幂等登记四个生命周期提醒。"""
        order = await self._reminders.require_order(order_id)
        if str(order.status).lower() in {"cancelled", "canceled"}:
            await self._reminders.cancel_for_order(order.id)
            return []
        schedule_fields: list[
            tuple[ReminderType, date, datetime]
        ] = []
        for reminder_type, day_offset, local_time in self._schedule:
            base_date = (
                order.check_in_date
                if reminder_type
                in {ReminderType.PRE_ARRIVAL, ReminderType.ARRIVAL_DAY}
                else order.check_out_date
            )
            local_date = base_date + timedelta(days=day_offset)
            scheduled_at = datetime.combine(
                local_date,
                local_time,
                tzinfo=WUHAN_TIMEZONE,
            ).astimezone(UTC)
            schedule_fields.append(
                (reminder_type, local_date, scheduled_at)
            )
        await self._reminders.cancel_obsolete_for_order(
            order.id,
            [
                (reminder_type, local_date)
                for reminder_type, local_date, _ in schedule_fields
            ],
        )
        created: list[Any] = []
        for reminder_type, local_date, scheduled_at in schedule_fields:
            reminder = await self._reminders.ensure_reminder(
                order_id=order.id,
                reminder_type=reminder_type,
                scheduled_local_date=local_date,
                scheduled_at=scheduled_at,
            )
            dedupe_key = (
                f"lifecycle:{order.id}:{reminder_type.value}:"
                f"{local_date.isoformat()}"
            )
            await self._jobs.enqueue(
                "lifecycle_send",
                {"reminder_id": reminder.id},
                available_at=scheduled_at,
                dedupe_key=dedupe_key,
            )
            created.append(reminder)
        return created

    async def deliver(self, reminder_id: int) -> None:
        """复核窗口和条数后发送，平台受理不等同客户收到。"""
        try:
            context = await self._reminders.require_send_context(
                reminder_id
            )
        except ReminderSendUnavailable as error:
            if error.requires_manual:
                await self._manual(error.reminder, error.reason)
            return
        now = self._now_provider().astimezone(UTC)
        guest_at = context.last_guest_at.astimezone(UTC)
        if now - guest_at > timedelta(hours=48):
            await self._manual(context.reminder, "send_window_expired")
            return
        if context.sent_count >= 5:
            await self._manual(context.reminder, "send_count_limit")
            return
        if self._before_external is not None:
            # 读取上下文可能持有提醒行锁；网络调用前提交快照并释放锁。
            await self._before_external()
        content = await self._build_content(context)
        try:
            message_id = await self._sender.send_text(
                context.open_kfid,
                context.external_userid,
                content,
            )
        except httpx.ConnectError as error:
            raise RetrySafeJobError("企业微信连接尚未建立") from error
        except httpx.TimeoutException:
            await self._manual(
                context.reminder,
                "send_result_uncertain",
            )
            return
        except httpx.RequestError:
            await self._manual(
                context.reminder,
                "send_result_uncertain",
            )
            return
        except WeComApiError as error:
            reason = (
                "send_count_limit"
                if error.error_code == 95001
                else f"wecom_error_{error.error_code}"
            )
            await self._manual(context.reminder, reason)
            return
        await self._reminders.mark_platform_accepted(
            reminder_id,
            message_id,
        )

    async def handle_send_failure(
        self,
        external_message_id: str,
        fail_type: int,
    ) -> None:
        """消费企业微信异步失败事件并幂等转人工。"""
        reminder = await self._reminders.find_by_message_id(
            external_message_id
        )
        if reminder is None:
            return
        await self._manual(reminder, f"wecom_fail_{fail_type}")

    async def _manual(self, reminder: Any, reason: str) -> None:
        """先建立幂等人工任务，再结束自动发送状态。"""
        await self._tasks.create_manual_contact(reminder, reason)
        await self._reminders.mark_manual_followup(
            int(reminder.id),
            reason,
        )

    async def _build_content(self, context: ReminderSendContext) -> str:
        """构造不含网址和凭证的确定性客户提醒。"""
        reminder_type = context.reminder.reminder_type
        if reminder_type is ReminderType.PRE_ARRIVAL:
            weather = (
                "武汉天气可能变化，出发前请留意降雨和气温。"
            )
            if self._weather is not None:
                try:
                    summary = await self._weather.forecast(
                        context.district,
                        context.order.check_in_date,
                    )
                except Exception:
                    # 天气是附加信息，查询失败不得阻止必要的入住提醒。
                    pass
                else:
                    safe_summary = self._safe_weather_summary(summary)
                    if safe_summary:
                        weather = safe_summary
            content = (
                f"温馨提醒：您明天将入住{context.property_title}。"
                f"路线提示：{context.address_hint or '请提前告知出发位置，管家会协助确认路线。'}"
                f"停车提示：{context.parking_instructions or '如需停车，请提前联系管家确认。'}"
                f"天气提示：{weather}"
            )
        elif reminder_type is ReminderType.ARRIVAL_DAY:
            content = (
                f"今天是您入住{context.property_title}的日期，"
                "请告知预计到达时间。房间确认可入住后，"
                "系统会另行发送入住指南、二维码和门锁密码。"
            )
        elif reminder_type is ReminderType.CHECKOUT:
            content = (
                "退房提醒：请按订单约定时间退房，离开前请检查"
                "证件、充电器和其他随身物品。如需协助请告诉我们。"
            )
        else:
            content = (
                "感谢入住 YuMi 民宿，祝您一路顺风。"
                "欢迎下次来武汉时再次入住。"
            )
        return _URL_PATTERN.sub("", content)[:1500]

    @staticmethod
    def _safe_weather_summary(summary: str) -> str:
        """选取最多三句天气建议，去除链接并避免半句截断。"""
        normalized = " ".join(
            _URL_PATTERN.sub("", summary).split()
        ).strip()
        if not normalized:
            return ""
        sentences = re.split(r"(?<=[。！？!?])", normalized)
        selected: list[str] = []
        length = 0
        for sentence in sentences:
            candidate = sentence.strip()
            if not candidate:
                continue
            if len(selected) >= 3 or length + len(candidate) > 300:
                break
            selected.append(candidate)
            length += len(candidate)
        return "".join(selected)
