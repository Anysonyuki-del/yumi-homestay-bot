from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.models import AuditLog, Conversation, Message
from homestay_bot.repositories.complaints import (
    ComplaintVersionConflict,
    SQLAlchemyComplaintRepository,
)


class ComplaintGuestSender(Protocol):
    """定义客诉人工回复的事务型发送边界。"""

    async def send_text(self, open_kfid: str, external_userid: str, content: str) -> str | None:
        """登记一条客人回复。"""


class ComplaintAdminService:
    """提供客诉编辑、退回、发送和关闭操作。"""

    def __init__(self, session: AsyncSession, sender: ComplaintGuestSender) -> None:
        """绑定当前事务和客人消息发送器。"""
        self._session = session
        self._reviews = SQLAlchemyComplaintRepository(session)
        self._sender = sender

    async def get_detail(self, review_id: int) -> dict[str, Any]:
        """返回完整对话和脱敏分析，供员工复核。"""
        review = await self._reviews.get(review_id)
        if review is None:
            raise LookupError("客诉记录不存在")
        conversation = await self._session.get(Conversation, review.conversation_id)
        if conversation is None:
            raise LookupError("客诉会话不存在")
        messages = await self._session.scalars(
            select(Message)
            .where(Message.conversation_id == review.conversation_id)
            .order_by(Message.id)
        )
        return {"review": review, "conversation": conversation, "messages": list(messages.all())}

    async def update_draft(self, review_id: int, version: int, draft: str) -> None:
        """保存员工修改后的回复草稿。"""
        await self._reviews.update_draft(
            review_id,
            expected_version=version,
            draft=draft,
        )

    async def send(self, review_id: int, version: int, draft: str, employee_id: int) -> None:
        """先保存草稿，再登记客人回复并标记已发送。"""
        detail = await self.get_detail(review_id)
        review = detail["review"]
        conversation = detail["conversation"]
        if review.version != version:
            raise ComplaintVersionConflict("客诉草稿已被其他员工更新")
        if draft.strip():
            review.draft = draft.strip()[:4000]
        if not review.draft:
            raise ValueError("回复内容不能为空")
        await self._sender.send_text(
            conversation.open_kfid,
            conversation.external_userid,
            review.draft,
        )
        await self._reviews.mark_sent(
            review_id,
            expected_version=version,
            sent_at=datetime.now(UTC),
        )
        self._audit(employee_id, "complaint.send", review_id)

    async def return_for_analysis(self, review_id: int, version: int, employee_id: int) -> None:
        """退回分析状态，后台任务可再次生成草稿。"""
        await self._reviews.mark_returned(review_id, expected_version=version)
        self._audit(employee_id, "complaint.return", review_id)

    async def cancel(self, review_id: int, version: int, employee_id: int) -> None:
        """关闭客诉并记录最小审计。"""
        await self._reviews.mark_cancelled(review_id, expected_version=version)
        self._audit(employee_id, "complaint.cancel", review_id)

    def _audit(self, employee_id: int, action: str, review_id: int) -> None:
        """审计只记录动作和客诉编号，不复制对话正文。"""
        self._session.add(
            AuditLog(
                actor_employee_id=employee_id,
                action=action,
                target_type="complaint_review",
                target_id=str(review_id),
                details={"review_id": review_id},
            )
        )
