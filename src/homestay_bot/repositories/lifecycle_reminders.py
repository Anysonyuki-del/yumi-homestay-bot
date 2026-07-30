from datetime import UTC, date, datetime
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import (
    CustomerIdentityProvider,
    JobStatus,
    MessageOrigin,
    ReminderStatus,
    ReminderType,
)
from homestay_bot.domain.models import (
    AuditLog,
    Conversation,
    CustomerIdentity,
    Job,
    LifecycleReminder,
    Message,
    PropertyProfile,
    StayOrder,
)
from homestay_bot.services.lifecycle_reminders import (
    ReminderSendContext,
    ReminderSendUnavailable,
)


def _as_utc(value: datetime) -> datetime:
    """把 SQLite 返回的无时区时间按项目约定解释为 UTC。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SQLAlchemyLifecycleReminderRepository:
    """持久化提醒计划，并只解析订单客户自己的可靠微信会话。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前事务。"""
        self._session = session

    async def require_order(self, order_id: int) -> StayOrder:
        """按主键读取订单。"""
        order = await self._session.get(StayOrder, order_id)
        if order is None:
            raise LookupError("订单不存在")
        return order

    async def ensure_reminder(
        self,
        *,
        order_id: int,
        reminder_type: ReminderType,
        scheduled_local_date: date,
        scheduled_at: datetime,
    ) -> LifecycleReminder:
        """按订单、类型和武汉本地日期幂等登记提醒。"""
        statement = select(LifecycleReminder).where(
            LifecycleReminder.order_id == order_id,
            LifecycleReminder.reminder_type == reminder_type,
            LifecycleReminder.scheduled_local_date
            == scheduled_local_date,
        )
        existing = await self._session.scalar(statement)
        if existing is not None:
            if existing.status is ReminderStatus.CANCELLED:
                # 同日期订单取消后恢复时复用唯一提醒，并重新唤醒已经
                # 结束的旧任务；平台受理或人工状态永不自动重置。
                existing.status = ReminderStatus.SCHEDULED
                existing.scheduled_at = scheduled_at
                existing.failure_reason = None
                existing.manual_followup_at = None
                dedupe_key = (
                    f"lifecycle:{order_id}:{reminder_type.value}:"
                    f"{scheduled_local_date.isoformat()}"
                )
                job = await self._session.scalar(
                    select(Job).where(Job.dedupe_key == dedupe_key)
                )
                if job is not None and job.status in {
                    JobStatus.COMPLETED,
                    JobStatus.FAILED,
                }:
                    job.status = JobStatus.PENDING
                    job.attempts = 0
                    job.available_at = scheduled_at
                    job.locked_at = None
                    job.last_error_code = None
                await self._session.flush()
            return existing
        reminder = LifecycleReminder(
            order_id=order_id,
            reminder_type=reminder_type,
            scheduled_local_date=scheduled_local_date,
            scheduled_at=scheduled_at,
            status=ReminderStatus.SCHEDULED,
        )
        self._session.add(reminder)
        await self._session.flush()
        return reminder

    async def require_send_context(
        self,
        reminder_id: int,
    ) -> ReminderSendContext:
        """锁定提醒，并选择订单客户最近有客人消息的已验证会话。"""
        reminder = await self._session.scalar(
            select(LifecycleReminder)
            .where(LifecycleReminder.id == reminder_id)
            .with_for_update()
        )
        if reminder is None:
            raise LookupError("提醒不存在")
        if reminder.status is not ReminderStatus.SCHEDULED:
            raise ReminderSendUnavailable(
                reminder,
                "reminder_not_scheduled",
                requires_manual=False,
            )
        order = await self._session.get(StayOrder, reminder.order_id)
        if order is None or order.customer_id is None:
            raise ReminderSendUnavailable(
                reminder,
                "order_customer_missing",
            )
        property_profile = await self._session.get(
            PropertyProfile,
            order.property_id,
        )
        if property_profile is None:
            raise ReminderSendUnavailable(
                reminder,
                "property_missing",
            )

        # 外部用户编号必须同时属于订单客户的会话和已验证身份，
        # 避免用其他客户的近期消息误判发送窗口或收件人。
        latest = (
            await self._session.execute(
                select(Conversation, Message)
                .join(
                    Message,
                    Message.conversation_id == Conversation.id,
                )
                .join(
                    CustomerIdentity,
                    (
                        CustomerIdentity.customer_id
                        == Conversation.customer_id
                    )
                    & (
                        CustomerIdentity.external_id
                        == Conversation.external_userid
                    ),
                )
                .where(
                    Conversation.customer_id == order.customer_id,
                    CustomerIdentity.provider
                    == CustomerIdentityProvider.WECOM_KF,
                    CustomerIdentity.is_verified.is_(True),
                    Message.origin == MessageOrigin.GUEST,
                )
                .order_by(Message.sent_at.desc(), Message.id.desc())
                .limit(1)
            )
        ).first()
        if latest is None:
            raise ReminderSendUnavailable(
                reminder,
                "verified_wecom_conversation_missing",
            )
        conversation, last_guest = latest
        sent_count = int(
            await self._session.scalar(
                select(func.count(Message.id)).where(
                    Message.conversation_id == conversation.id,
                    Message.origin.in_(
                        [MessageOrigin.BOT, MessageOrigin.SERVICER]
                    ),
                    Message.sent_at > last_guest.sent_at,
                )
            )
            or 0
        )
        return ReminderSendContext(
            reminder=reminder,
            order=order,
            property_title=property_profile.title,
            district=property_profile.district or "",
            address_hint=property_profile.address_hint or "",
            parking_instructions=(
                property_profile.parking_instructions or ""
            ),
            open_kfid=conversation.open_kfid,
            external_userid=conversation.external_userid,
            last_guest_at=_as_utc(last_guest.sent_at),
            sent_count=sent_count,
        )

    async def cancel_for_order(self, order_id: int) -> int:
        """撤销订单全部计划中提醒，并保留已经受理的历史记录。"""
        reminders = list(
            (
                await self._session.scalars(
                    select(LifecycleReminder)
                    .where(
                        LifecycleReminder.order_id == order_id,
                        LifecycleReminder.status
                        == ReminderStatus.SCHEDULED,
                    )
                    .with_for_update()
                )
            ).all()
        )
        for reminder in reminders:
            reminder.status = ReminderStatus.CANCELLED
            reminder.failure_reason = "order_cancelled"
            self._session.add(
                AuditLog(
                    actor_employee_id=None,
                    action="lifecycle_reminder_cancelled",
                    target_type="lifecycle_reminder",
                    target_id=str(reminder.id),
                    details={"reason": "order_cancelled"},
                )
            )
        await self._session.flush()
        return len(reminders)

    async def cancel_obsolete_for_order(
        self,
        order_id: int,
        active_keys: list[tuple[ReminderType, date]],
    ) -> int:
        """撤销订单改期后日期或类型已经不再匹配的待发提醒。"""
        active = set(active_keys)
        scheduled = list(
            (
                await self._session.scalars(
                    select(LifecycleReminder)
                    .where(
                        LifecycleReminder.order_id == order_id,
                        LifecycleReminder.status
                        == ReminderStatus.SCHEDULED,
                    )
                    .with_for_update()
                )
            ).all()
        )
        obsolete = [
            reminder
            for reminder in scheduled
            if (
                reminder.reminder_type,
                reminder.scheduled_local_date,
            )
            not in active
        ]
        for reminder in obsolete:
            reminder.status = ReminderStatus.CANCELLED
            reminder.failure_reason = "order_rescheduled"
            self._session.add(
                AuditLog(
                    actor_employee_id=None,
                    action="lifecycle_reminder_cancelled",
                    target_type="lifecycle_reminder",
                    target_id=str(reminder.id),
                    details={"reason": "order_rescheduled"},
                )
            )
        await self._session.flush()
        return len(obsolete)

    async def mark_platform_accepted(
        self,
        reminder_id: int,
        message_id: str,
    ) -> None:
        """记录企业微信平台受理结果，不推断客户已收到。"""
        reminder = await self._require_for_update(reminder_id)
        if reminder.status is not ReminderStatus.SCHEDULED:
            return
        reminder.status = ReminderStatus.PLATFORM_ACCEPTED
        reminder.external_message_id = message_id
        reminder.platform_accepted_at = datetime.now(UTC)
        reminder.failure_reason = None
        self._session.add(
            AuditLog(
                actor_employee_id=None,
                action="lifecycle_reminder_platform_accepted",
                target_type="lifecycle_reminder",
                target_id=str(reminder.id),
                details={"reminder_type": reminder.reminder_type.value},
            )
        )
        await self._session.flush()

    async def find_by_message_id(
        self,
        message_id: str,
    ) -> LifecycleReminder | None:
        """按企业微信平台消息编号查找已受理提醒。"""
        return cast(
            LifecycleReminder | None,
            await self._session.scalar(
                select(LifecycleReminder).where(
                    LifecycleReminder.external_message_id == message_id
                )
            ),
        )

    async def mark_manual_followup(
        self,
        reminder_id: int,
        reason: str,
    ) -> None:
        """幂等转为人工跟进，并记录不含客人内容的原因码。"""
        reminder = await self._require_for_update(reminder_id)
        if reminder.status is ReminderStatus.MANUAL_FOLLOWUP:
            return
        if reminder.status is ReminderStatus.CANCELLED:
            return
        reminder.status = ReminderStatus.MANUAL_FOLLOWUP
        reminder.failure_reason = reason[:64]
        reminder.manual_followup_at = datetime.now(UTC)
        self._session.add(
            AuditLog(
                actor_employee_id=None,
                action="lifecycle_reminder_manual_followup",
                target_type="lifecycle_reminder",
                target_id=str(reminder.id),
                details={"reason": reason[:64]},
            )
        )
        await self._session.flush()

    async def _require_for_update(
        self,
        reminder_id: int,
    ) -> LifecycleReminder:
        """锁定并返回提醒。"""
        reminder = await self._session.scalar(
            select(LifecycleReminder)
            .where(LifecycleReminder.id == reminder_id)
            .with_for_update()
        )
        if reminder is None:
            raise LookupError("提醒不存在")
        return reminder
