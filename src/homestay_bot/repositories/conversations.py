from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import Language, MessageOrigin
from homestay_bot.domain.models import Conversation, Message, PropertyProfile, StayOrder
from homestay_bot.domain.stay_status import (
    is_checked_out_stay_status,
    is_current_stay,
    is_excluded_stay_status,
)
from homestay_bot.services.message_service import IncomingMessage, substantive_language


@dataclass(frozen=True, slots=True)
class DeliveryRewriteContext:
    """保存一次安全改写所需的失败回复、原问题和会话。"""

    failed_bot: Message
    source_guest: Message
    conversation: Conversation


class SQLAlchemyConversationRepository:
    """使用 SQLAlchemy 创建、读取和更新唯一客服会话。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前数据库会话。"""
        self._session = session

    async def detect_language(self, conversation_id: int, fallback: Language) -> Language:
        """从最近两个有效客人消息确定语言；流式读取跳过短消息，不新增状态字段。"""
        rows = await self._session.stream_scalars(
            select(Message.content).where(
                Message.conversation_id == conversation_id,
                Message.origin == MessageOrigin.GUEST, Message.message_type == "text",
            ).order_by(Message.id.desc()).execution_options(yield_per=50)
        )
        signals: list[Language] = []
        try:
            async for content in rows:
                signal = substantive_language(content or "")
                if signal is not None:
                    signals.append(signal)
                if len(signals) == 2:
                    break
        finally:
            await rows.close()
        if signals and (len(signals) == 1 or signals[0] == signals[1]):
            return signals[0]
        return fallback

    async def get_or_create(self, message: IncomingMessage) -> Conversation:
        """按客服账号和外部联系人查找会话，不存在时创建。"""
        statement = select(Conversation).where(
            Conversation.open_kfid == message.open_kfid,
            Conversation.external_userid == message.external_userid,
        )
        conversation = await self._session.scalar(statement)
        if conversation is not None:
            return conversation

        conversation = Conversation(
            open_kfid=message.open_kfid,
            external_userid=message.external_userid,
        )
        # begin_nested 会隐式刷新 pending 对象；先在捕获范围外暴露外层事务错误。
        await self._session.flush()
        try:
            async with self._session.begin_nested():
                self._session.add(conversation)
                await self._session.flush()
        except IntegrityError:
            # 并发补拉可能同时创建会话；保存点回滚后只重新读取竞争结果。
            conversation = await self._session.scalar(statement)
            if conversation is None:
                raise
        return conversation

    async def save(self, conversation: Conversation) -> None:
        """刷新会话模式、语言和接待员工等变更。"""
        self._session.add(conversation)
        await self._session.flush()

    async def lock_activity(self, conversation_id: int) -> None:
        """锁定会话活动行，串行化新入站与静默任务的检查和出站写入。"""
        await self._session.flush()
        await self._session.scalar(
            select(Conversation).where(Conversation.id == conversation_id).with_for_update()
            .execution_options(populate_existing=True)
        )

    async def _stay_conversation(
        self,
        conversation_id: int,
        *,
        lock: bool = True,
    ) -> Conversation | None:
        """写入时刷新并锁定；模型前只读查询不得持锁跨外部调用。"""
        if lock:
            await self._session.flush()
        statement = select(Conversation).where(Conversation.id == conversation_id)
        if lock:
            statement = statement.with_for_update()
        result: Conversation | None = await self._session.scalar(
            statement.execution_options(populate_existing=True, autoflush=False)
        )
        return result

    @staticmethod
    def _stay_snapshot(order: StayOrder) -> dict[str, Any]:
        """仅保存核验所需订单事实，到达时间不能替代允许入住时间。"""
        return {
            "order_id": order.id,
            "customer_id": order.customer_id,
            "property_id": order.property_id,
            "check_in_date": order.check_in_date.isoformat(),
            "check_out_date": order.check_out_date.isoformat(),
        }

    @staticmethod
    def _stay_order_valid(order: StayOrder, *, allow_history: bool = False) -> bool:
        """取消订单始终失效；退房状态仅允许显式历史咨询读取。"""
        return (
            not is_excluded_stay_status(order.status)
            and (allow_history or not is_checked_out_stay_status(order.status))
            and order.check_in_date < order.check_out_date
        )

    async def prepare_stay_confirmation(
        self,
        conversation_id: int,
        *,
        source_message_id: str,
        today: date,
        order_id: int | None = None,
    ) -> str:
        """只展示归属已核实的有效订单；多订单先选择，绝不隐式取第一条。"""
        conversation = await self._stay_conversation(conversation_id)
        if conversation is None:
            return ""
        source = await self._session.scalar(
            select(Message).where(
                Message.conversation_id == conversation_id,
                Message.external_message_id == source_message_id,
                Message.origin == MessageOrigin.GUEST,
            )
        )
        if source is None:
            return ""
        english = conversation.language is Language.EN
        current = conversation.stay_confirmation or {}
        # 重放旧交互不能撤销后来已确认的住宿，也不能倒退到旧订单提示。
        previous_source = await self._session.scalar(
            select(Message.id).where(
                Message.conversation_id == conversation_id,
                Message.external_message_id == current.get("prompt_message_id"),
            )
        )
        if previous_source is not None and (
            previous_source > source.id
            or (previous_source == source.id and current.get("status") == "confirmed")
        ):
            return ""
        if conversation.customer_id is None:
            return ("Please ask the host to verify which booking belongs to you." if english
                    else "暂未核实您的订单归属，请联系管家核实本次住宿。")
        orders = list(
            (
                await self._session.scalars(
                    select(StayOrder)
                    .where(
                        StayOrder.customer_id == conversation.customer_id,
                        StayOrder.check_out_date > today,
                    )
                    .order_by(StayOrder.check_in_date, StayOrder.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        # 与模型上下文的「进行中订单」共用同一判定，避免一边判有订单、一边找不到。
        orders = [
            order
            for order in orders
            if is_current_stay(order.status, order.check_in_date, order.check_out_date, today)
        ]
        if order_id is not None:
            orders = [order for order in orders if order.id == order_id]
        if not orders:
            return ("No verified active booking was found. Please contact the host." if english
                    else "暂未找到已核实归属的有效订单，请联系管家核实本次住宿。")
        descriptions = []
        for order in orders:
            title = await self._session.scalar(
                select(PropertyProfile.title).where(PropertyProfile.id == order.property_id)
            )
            descriptions.append(
                f"Booking {order.id}: {title or 'room'}, "
                 f"check-in {order.check_in_date}, check-out {order.check_out_date}" if english else
                 f"订单 {order.id}：{title or '房间'}，"
                 f"{order.check_in_date.isoformat()} 入住、{order.check_out_date.isoformat()} 退房"
            )
        if len(orders) != 1:
            conversation.stay_confirmation = None
            await self._session.flush()
            return (("Please select your stay from these bookings:\n" if english else
                     "您有多个有效订单，请先选择本次住宿：\n") + "\n".join(descriptions))
        conversation.stay_confirmation = {
            **self._stay_snapshot(orders[0]),
            "status": "pending",
            "prompt_message_id": source_message_id,
        }
        await self._session.flush()
        return (("Please confirm: " + descriptions[0] + ". Is this correct?") if english
                else "请确认：" + descriptions[0] + "，对吗？")

    async def _validated_stay_order(
        self,
        conversation: Conversation,
        snapshot: dict[str, Any],
        *,
        today: date,
        allow_history: bool = False,
        lock: bool = True,
    ) -> StayOrder | None:
        """锁定权威订单复核快照，使改期与确认事务串行执行。"""
        order_id = snapshot.get("order_id")
        if type(order_id) is not int or conversation.customer_id is None:
            return None
        statement = select(StayOrder).where(StayOrder.id == order_id)
        if lock:
            statement = statement.with_for_update()
        order = await self._session.scalar(
            statement.execution_options(populate_existing=True, autoflush=False)
        )
        if order is None or order.customer_id != conversation.customer_id:
            return None
        if any(snapshot.get(key) != value for key, value in self._stay_snapshot(order).items()):
            return None
        if not self._stay_order_valid(order, allow_history=allow_history):
            return None
        if not allow_history and order.check_out_date <= today:
            return None
        return order

    async def confirm_stay(
        self,
        conversation_id: int,
        *,
        prompt_message_id: str,
        guest_message_id: str,
        now: datetime,
    ) -> bool:
        """明确肯定意图才能调用；原子核验当前提示、回复归属及订单快照。"""
        conversation = await self._stay_conversation(conversation_id)
        snapshot = dict(conversation.stay_confirmation or {}) if conversation else {}
        if conversation is None or snapshot.get("prompt_message_id") != prompt_message_id:
            return False
        if snapshot.get("status") not in {"pending", "confirmed"}:
            return False
        order = await self._validated_stay_order(
            conversation, snapshot, today=now.astimezone(ZoneInfo("Asia/Shanghai")).date()
        )
        if order is None:
            return False
        if snapshot.get("status") == "confirmed":
            return snapshot.get("guest_message_id") == guest_message_id
        messages = list(
            (
                await self._session.scalars(
                    select(Message).where(
                        Message.conversation_id == conversation_id,
                        Message.external_message_id.in_([prompt_message_id, guest_message_id]),
                        Message.origin == MessageOrigin.GUEST,
                        Message.message_type == "text",
                    )
                )
            ).all()
        )
        by_id = {message.external_message_id: message for message in messages}
        prompt, reply = by_id.get(prompt_message_id), by_id.get(guest_message_id)
        if (
            prompt is None
            or reply is None
            or reply.id <= prompt.id
            or reply.sent_at < prompt.sent_at
        ):
            return False
        conversation.stay_confirmation = {
            **snapshot,
            "status": "confirmed",
            "guest_message_id": guest_message_id,
            "confirmed_at": now.isoformat(),
        }
        await self._session.flush()
        return True

    async def decline_stay(self, conversation_id: int, *, prompt_message_id: str) -> None:
        """明确否定只撤销当前提示，迟到的否定不能覆盖后来的提示。"""
        conversation = await self._stay_conversation(conversation_id)
        if (
            conversation
            and (conversation.stay_confirmation or {}).get("prompt_message_id") == prompt_message_id
        ):
            conversation.stay_confirmation = None
            await self._session.flush()

    async def get_confirmed_stay(
        self,
        conversation_id: int,
        *,
        today: date,
        allow_history: bool = False,
        lock: bool = True,
    ) -> dict[str, Any] | None:
        """出站前再查权威订单；退房后仅显式历史问题可读取快照。"""
        conversation = await self._stay_conversation(conversation_id, lock=lock)
        snapshot = dict(conversation.stay_confirmation or {}) if conversation else {}
        if conversation is None or snapshot.get("status") != "confirmed":
            return None
        order = await self._validated_stay_order(
            conversation, snapshot, today=today, allow_history=allow_history, lock=lock
        )
        if order is None:
            return None
        room = await self._session.get(PropertyProfile, order.property_id)
        # 通知使用运营房号/名称，不能把数据库主键冒充客人入住的房间号。
        return {**snapshot, "property_title": room.title if room else None,
                "room_number": room.room_number if room else None}


class SQLAlchemyMessageRepository:
    """使用 SQLAlchemy 持久化并查询已去重消息。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前数据库会话。"""
        self._session = session

    async def exists(self, external_message_id: str) -> bool:
        """按企业微信消息编号判断是否已经处理。"""
        statement = select(Message.id).where(Message.external_message_id == external_message_id)
        return await self._session.scalar(statement) is not None

    async def add(self, message: Message) -> bool:
        """保存消息并刷新主键；唯一键竞争返回 False 且不污染外层事务。"""
        # 只允许保存点内部的消息唯一键错误按重复处理，不能吞掉外层约束错误。
        await self._session.flush()
        try:
            async with self._session.begin_nested():
                self._session.add(message)
                await self._session.flush()
        except IntegrityError:
            # 只有外部消息编号已存在才属于幂等重复；外键、非空等错误必须上抛。
            if await self.exists(message.external_message_id):
                return False
            raise
        return True

    async def list_recent(
        self,
        conversation_id: int,
        limit: int,
        through_external_message_id: str | None = None,
    ) -> list[Message]:
        """按系统实际处理顺序读取最近消息，避免外部时区扰乱上下文。"""
        conditions = [
            Message.conversation_id == conversation_id,
            Message.message_type == "text",
        ]
        if through_external_message_id is not None:
            boundary = (
                select(Message.id)
                .where(Message.external_message_id == through_external_message_id)
                .scalar_subquery()
            )
            conditions.append(Message.id <= boundary)
        statement = select(Message).where(*conditions).order_by(Message.id.desc()).limit(limit)
        recent = list((await self._session.scalars(statement)).all())
        recent.reverse()
        return recent

    async def has_newer_guest_message(
        self,
        conversation_id: int,
        external_message_id: str,
    ) -> bool:
        """判断来源消息之后是否已保存更新的客人文本。"""
        boundary = (
            select(Message.id)
            .where(Message.external_message_id == external_message_id)
            .scalar_subquery()
        )
        statement = select(
            exists().where(
                Message.conversation_id == conversation_id,
                Message.id > boundary,
                Message.origin == MessageOrigin.GUEST,
                Message.message_type == "text",
            )
        )
        return bool(await self._session.scalar(statement))

    async def find_conversation_id(
        self,
        open_kfid: str,
        external_userid: str,
    ) -> int | None:
        """按企业微信客服账号与外部用户编号反查会话编号。

        出站载荷里带的是这两个字段，真实发送发生在提交之后；要在那一刻复核
        「排队期间会话里是否又发生了新活动」，就得先回到同一个会话。
        """
        found = await self._session.scalar(
            select(Conversation.id).where(
                Conversation.open_kfid == open_kfid,
                Conversation.external_userid == external_userid,
            )
        )
        return int(found) if found is not None else None

    async def has_newer_conversation_activity(
        self,
        conversation_id: int,
        external_message_id: str,
    ) -> bool:
        """判断来源边界后是否出现任意客人或员工活动。"""
        boundary = (
            select(Message.id)
            .where(Message.external_message_id == external_message_id)
            .scalar_subquery()
        )
        statement = select(
            exists().where(
                Message.conversation_id == conversation_id,
                Message.id > boundary,
                Message.origin != MessageOrigin.BOT,
            )
        )
        return bool(await self._session.scalar(statement))

    async def has_newer_servicer_activity(
        self,
        conversation_id: int,
        external_message_id: str,
    ) -> bool:
        """判断来源边界后是否出现人工客服发言；长回复续发段只因此停止。"""
        boundary = (
            select(Message.id)
            .where(Message.external_message_id == external_message_id)
            .scalar_subquery()
        )
        statement = select(
            exists().where(
                Message.conversation_id == conversation_id,
                Message.id > boundary,
                Message.origin == MessageOrigin.SERVICER,
            )
        )
        return bool(await self._session.scalar(statement))

    async def replace_external_message_id(
        self, temporary_id: str, external_message_id: str
    ) -> None:
        """发送成功后用企业微信真实 msgid 替换 outbox 临时编号。"""
        await self._session.execute(
            update(Message)
            .where(Message.external_message_id == temporary_id)
            .values(external_message_id=external_message_id)
        )
        await self._session.flush()

    async def mark_delivery_failed(
        self,
        external_message_id: str,
        *,
        error_code: str,
    ) -> Message | None:
        """记录企业微信异步投递失败，供重试编排和上下文过滤使用。"""
        message = await self._session.scalar(
            select(Message).where(Message.external_message_id == external_message_id)
        )
        if message is None or message.origin is not MessageOrigin.BOT:
            return message
        metadata = dict(message.message_metadata or {})
        try:
            retry_count = int(metadata.get("delivery_retry_count", 0))
        except (TypeError, ValueError):
            retry_count = 0
        metadata.update(
            {
                "delivery_status": "failed",
                "delivery_error_code": error_code[:64],
                "delivery_retry_count": max(retry_count, 0),
            }
        )
        message.message_metadata = metadata
        await self._session.flush()
        return message

    async def get_delivery_rewrite_context(
        self,
        failed_bot_id: int,
    ) -> DeliveryRewriteContext | None:
        """读取首次安全拦截回复及其精确关联的客人问题。"""
        failed_bot = await self._session.get(Message, failed_bot_id)
        if (
            failed_bot is None
            or failed_bot.origin is not MessageOrigin.BOT
            or not failed_bot.content
        ):
            return None
        metadata = dict(failed_bot.message_metadata or {})
        if (
            metadata.get("delivery_status") != "failed"
            or metadata.get("delivery_error_code") != "wecom_async_13"
        ):
            return None
        conversation = await self._session.get(
            Conversation,
            failed_bot.conversation_id,
        )
        if conversation is None:
            return None

        source_guest: Message | None = None
        source_external_id = str(metadata.get("source_guest_message_id", "")).strip()
        if source_external_id:
            source_guest = await self._session.scalar(
                select(Message).where(
                    Message.conversation_id == failed_bot.conversation_id,
                    Message.external_message_id == source_external_id,
                    Message.origin == MessageOrigin.GUEST,
                    Message.message_type == "text",
                    Message.content.is_not(None),
                )
            )
        if source_guest is None:
            # 兼容部署前没有精确关联字段的旧消息，只回退到失败回复之前的最近问题。
            source_guest = await self._session.scalar(
                select(Message)
                .where(
                    Message.conversation_id == failed_bot.conversation_id,
                    Message.id < failed_bot.id,
                    Message.origin == MessageOrigin.GUEST,
                    Message.message_type == "text",
                    Message.content.is_not(None),
                )
                .order_by(Message.id.desc())
                .limit(1)
            )
        if source_guest is None or not source_guest.content:
            return None
        return DeliveryRewriteContext(
            failed_bot=failed_bot,
            source_guest=source_guest,
            conversation=conversation,
        )

    async def save_delivery_rewrite_metadata(
        self,
        message: Message,
        metadata: dict[str, object],
    ) -> None:
        """保存原失败回复的改写及二次投递审计字段。"""
        # JSON 字段在模型调用前已经提交；每次赋新对象才能触发 SQLAlchemy 脏检查。
        message.message_metadata = dict(metadata)
        await self._session.flush()
