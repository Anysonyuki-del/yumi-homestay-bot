"""只在需要转交核对订单时收集最小资料；明文存任务，不自动绑定订单。"""

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import BusinessTaskType, Language
from homestay_bot.domain.models import BusinessTask, Conversation, Message, StayOrder
from homestay_bot.domain.stay_status import is_excluded_stay_status
from homestay_bot.repositories.operations import SQLAlchemyOperationsRepository
from homestay_bot.services.message_service import IncomingMessage

VERIFICATION_PLACEHOLDER = "[核对信息已转交管家]"


def verification_prompt(language: Language) -> str:
    """只提供两种核对方式，不索要完整手机号或身份证。"""
    return (
        "To ask the host to verify your booking, please send either your order number, "
        "or the last four digits of the booking phone number and the booking name."
        if language is Language.EN
        else "需要请管家核对订单，请提供二选一：订单号，或预订手机号后4位加预订人姓名。"
    )


class GuestVerificationService:
    """复用任务与会话事务，不把客人自报资料作为订单归属依据。"""

    def __init__(self, session: AsyncSession) -> None:
        """使用调用方事务保存任务与模型隔离标记。"""
        self._session = session

    async def handle(
        self,
        conversation: Conversation,
        message: IncomingMessage,
    ) -> tuple[int, str] | None:
        """先处理已索要资料的回复，再判断是否需要新建核对请求。"""
        if conversation.customer_id is None:
            return None
        # 调用方已持会话活动锁；不要为普通咨询锁住该客人的历史任务。
        tasks = list(
            (
                await self._session.scalars(
                    select(BusinessTask)
                    .where(
                        BusinessTask.customer_id == conversation.customer_id,
                        BusinessTask.verification_data.is_not(None),
                    )
                    .order_by(BusinessTask.id.desc())
                )
            ).all()
        )
        pending = next(
            (
                task
                for task in tasks
                if (task.verification_data or {}).get("conversation_id") == conversation.id
                and (task.verification_data or {}).get("status") == "awaiting"
                and str(task.status) not in {"completed", "cancelled", "expired"}
            ),
            None,
        )
        text = message.content.strip()
        # 自然语序和同一条申请自带资料都识别；是否完成解析不决定模型隔离。
        labelled = bool(
            re.search(
                r"(?:订单号|order(?: number)?|预订人|姓名|后[四4]位|last four|booking name)",
                text,
                re.I,
            )
            and re.search(r"[0-9]{4}", text)
        )
        compact = bool(
            re.fullmatch(
                r"(?:[A-Za-z0-9-]{4,64}|[0-9]{4}[，,\s]+[\u4e00-\u9fffA-Za-z .'-]{2,40})",
                text,
            )
            and re.search(r"[0-9]{4}", text)
        )
        details = labelled or (pending is not None and compact)
        if pending is not None and pending.source_message_id != message.msgid and details:
            return await self._save_details(pending, conversation, message)
        needs_order = re.search(
            r"改期|退款|退钱|发票|办理入住|入住确认|我的房间|我住的|"
            r"reschedul|refund|invoice|my (?:room|booking|stay)|check.?in",
            text,
            re.I,
        )
        if not needs_order or re.search(r"政策|规定|规则|policy|rules", text, re.I):
            return None
        orders = list(
            (
                await self._session.scalars(
                    select(StayOrder).where(
                        StayOrder.customer_id == conversation.customer_id,
                    )
                )
            ).all()
        )
        if any(not is_excluded_stay_status(order.status) for order in orders):
            return None
        if pending is None:
            repository = SQLAlchemyOperationsRepository(self._session)
            pending = await repository.create_pending_confirmation(
                customer_id=conversation.customer_id,
                source_message_id=message.msgid,
                task_type=BusinessTaskType.SPECIAL_SERVICE,
                description="客人请求管家核对订单",
            )
            pending.verification_data = {"status": "awaiting", "conversation_id": conversation.id}
            await self._session.flush()
        if details:
            return await self._save_details(pending, conversation, message)
        return pending.id, verification_prompt(conversation.language)

    async def _save_details(
        self,
        task: BusinessTask,
        conversation: Conversation,
        message: IncomingMessage,
    ) -> tuple[int, str]:
        """保存人工核对原文与模型隔离标记；不查询、关联或展示任何订单。"""
        task.verification_data = {
            **(task.verification_data or {}),
            "status": "provided",
            "value": message.content[:2000],
            "message_id": message.msgid,
        }
        row = await self._session.scalar(
            select(Message).where(
                Message.conversation_id == conversation.id,
                Message.external_message_id == message.msgid,
            )
        )
        if row is not None:
            row.message_metadata = {**(row.message_metadata or {}), "verification_task_id": task.id}
        await self._session.flush()
        reply = (
            "Your details are recorded for the host to verify. "
            "Your booking has not yet been confirmed."
            if conversation.language is Language.EN
            else "核对信息已记录，待管家核实；目前尚未确认订单内容。"
        )
        return task.id, reply
