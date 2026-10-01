from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import ComplaintReviewStatus, JobStatus
from homestay_bot.domain.models import AuditLog, ComplaintReview, Conversation, Message
from homestay_bot.repositories.complaints import (
    CANCELLABLE_STATUSES,
    EDITABLE_STATUSES,
    RETURNABLE_STATUSES,
    SENDABLE_STATUSES,
    ComplaintStateRefused,
    ComplaintVersionConflict,
    SQLAlchemyComplaintRepository,
    _sanitize_text,
)
from homestay_bot.repositories.jobs import SQLAlchemyJobRepository

_IN_FLIGHT_TEXT = "这条回复正在发送或自动重试中，请稍后刷新页面查看结果"


class ComplaintGuestSender(Protocol):
    """定义客诉人工回复的事务型发送边界。"""

    async def send_text(self, open_kfid: str, external_userid: str, content: str) -> str | None:
        """登记一条客人回复。"""


class ComplaintAdminService:
    """提供客诉编辑、退回、发送和关闭操作。"""

    _MAX_DETAIL_MESSAGES = 200

    def __init__(self, session: AsyncSession, sender: ComplaintGuestSender) -> None:
        """绑定当前事务和客人消息发送器。"""
        self._session = session
        self._reviews = SQLAlchemyComplaintRepository(session)
        self._jobs = SQLAlchemyJobRepository(session)
        self._sender = sender

    async def list_open(self, *, offset: int, limit: int) -> list[Any]:
        """返回交班时需要继续处理的客诉，终态记录不进入列表。"""
        return await self._reviews.list_open(offset=offset, limit=limit)

    async def get_detail(
        self,
        review_id: int,
        *,
        before_message_id: int | None = None,
    ) -> dict[str, Any]:
        """按游标返回最新一页对话和脱敏分析，供员工复核。"""
        review = await self._reviews.get(review_id)
        if review is None:
            raise LookupError("客诉记录不存在")
        conversation = await self._session.get(Conversation, review.conversation_id)
        if conversation is None:
            raise LookupError("客诉会话不存在")
        statement = select(Message).where(
            Message.conversation_id == review.conversation_id
        )
        if before_message_id is not None:
            statement = statement.where(Message.id < before_message_id)
        rows = list(
            (
                await self._session.scalars(
                    statement.order_by(Message.id.desc()).limit(
                        self._MAX_DETAIL_MESSAGES + 1
                    )
                )
            ).all()
        )
        has_older_messages = len(rows) > self._MAX_DETAIL_MESSAGES
        page = rows[: self._MAX_DETAIL_MESSAGES]
        page.reverse()
        in_flight = await self._delivery_in_flight(review)
        return {
            "review": review,
            "conversation": conversation,
            "messages": page,
            "has_older_messages": has_older_messages,
            "older_before_message_id": page[0].id if page else None,
            "is_latest_message_page": before_message_id is None,
            "actions": self._actions(review, in_flight=in_flight),
            "delivery_in_flight": in_flight,
        }

    async def _delivery_in_flight(self, review: ComplaintReview) -> bool:
        """当前记录的那次发送是否仍在排队或执行。

        必须看任务而不能只看客诉状态：连接失败或 45009 限流时，worker 先把客诉写成
        DELIVERY_FAILED，再把原任务放回 PENDING 自动重试（jobs.mark_failed）。这时
        手动重发会登记第二个任务，客人可能收到两条。任务一旦进入 COMPLETED/FAILED
        不会自动回到 PENDING（recover_stale 只放回 RUNNING 的任务，那时本来就在途），
        所以这个判定看到在途就拒绝即可，不需要额外加锁。任务行已按保留期清理时查不到
        状态，按不在途处理。
        """
        if not review.delivery_outbox_id:
            return False
        status = await self._jobs.status_for_dedupe_key(review.delivery_outbox_id)
        return status in {JobStatus.PENDING, JobStatus.RUNNING}

    @staticmethod
    def _actions(review: ComplaintReview, *, in_flight: bool) -> dict[str, bool]:
        """页面按钮资格，与服务端校验共用同一组状态常量；只是展示，不替代校验。"""
        status = review.status
        return {
            "edit": status in EDITABLE_STATUSES
            or (status is ComplaintReviewStatus.DELIVERY_FAILED and not in_flight),
            "save": status in EDITABLE_STATUSES,
            "send": status in SENDABLE_STATUSES and not in_flight,
            "return": status in RETURNABLE_STATUSES,
            "cancel": status in CANCELLABLE_STATUSES and not in_flight,
        }

    async def update_draft(self, review_id: int, version: int, draft: str) -> None:
        """保存员工修改后的回复草稿；空草稿没有意义，与发送同样拒绝。"""
        if not draft.strip():
            raise ComplaintStateRefused("回复内容不能为空", status_code=422)
        await self._reviews.update_draft(
            review_id,
            expected_version=version,
            draft=draft,
        )

    async def send(self, review_id: int, version: int, draft: str, employee_id: int) -> None:
        """登记出站任务并在同一事务内把客诉改为已排队，实际发送由 worker 回写。

        发送的是本次提交的正文（用户确认的 D1-A，2026-10-01）：员工在回复框里改完
        直接点发送，发出去的就是框里的内容。提交为空一律拒绝，不再悄悄沿用已保存的
        旧稿——那正是「改了却发出旧稿」的来源。

        顺序是先登记任务、再做带版本条件的状态更新：条件更新失败会抛错，调用方
        不提交事务，任务随之回滚，不会留下已登记却被拒绝的发送。
        """
        detail = await self.get_detail(review_id)
        review = detail["review"]
        conversation = detail["conversation"]
        if review.version != version:
            raise ComplaintVersionConflict("客诉草稿已被其他员工更新，请核对最新内容后再发送")
        if detail["delivery_in_flight"]:
            raise ComplaintStateRefused(_IN_FLIGHT_TEXT)
        if review.status not in SENDABLE_STATUSES:
            raise ComplaintStateRefused("当前客诉状态不允许发送")
        content = _sanitize_text(draft.strip())
        if not content:
            raise ComplaintStateRefused("回复内容不能为空", status_code=422)
        outbox_id = await self._sender.send_text(
            conversation.open_kfid,
            conversation.external_userid,
            content,
        )
        if not outbox_id:
            raise ComplaintStateRefused("这条回复已经登记过发送，请刷新页面查看最新状态")
        await self._reviews.mark_send_queued(
            review_id,
            expected_version=version,
            outbox_id=outbox_id,
            draft=content,
        )
        self._audit(employee_id, "complaint.send", review_id)

    async def return_for_analysis(self, review_id: int, version: int, employee_id: int) -> None:
        """退回分析状态，后台任务可再次生成草稿。"""
        await self._refuse_in_flight(review_id)
        await self._reviews.mark_returned(review_id, expected_version=version)
        review = await self._reviews.get(review_id)
        if review is not None:
            await self._jobs.enqueue(
                "complaint_review_generate",
                {"review_id": review_id},
                dedupe_key=f"complaint-review-retry:{review_id}:{review.version}",
            )
        self._audit(employee_id, "complaint.return", review_id)

    async def cancel(self, review_id: int, version: int, employee_id: int) -> None:
        """关闭客诉并记录最小审计。"""
        await self._refuse_in_flight(review_id)
        await self._reviews.mark_cancelled(review_id, expected_version=version)
        self._audit(employee_id, "complaint.cancel", review_id)

    async def _refuse_in_flight(self, review_id: int) -> None:
        """原任务仍在发送或自动重试时拒绝关闭、退回，避免记录与客人实际收到的对不上。"""
        review = await self._reviews.get(review_id)
        if review is None:
            raise LookupError("客诉记录不存在")
        if await self._delivery_in_flight(review):
            raise ComplaintStateRefused(_IN_FLIGHT_TEXT)

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
