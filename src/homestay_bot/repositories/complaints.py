import re
from datetime import datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import ComplaintReviewStatus
from homestay_bot.domain.errors import OperationRefused
from homestay_bot.domain.models import ComplaintReview


class ComplaintVersionConflict(OperationRefused, ValueError):
    """表示员工正在编辑过期版本的客诉草稿。

    同时继承 OperationRefused：文案是写给员工看的，页面可以原样展示。保留
    ValueError 基类，既有按 ValueError 捕获的调用方不受影响。
    """


class ComplaintStateRefused(OperationRefused, ValueError):
    """当前客诉状态不允许本次操作；文案写给员工看，可以原样展示。"""


_S = ComplaintReviewStatus
# 各人工操作允许的起始状态。页面按钮与服务端校验共用这一份，避免两处漂移。
EDITABLE_STATUSES = frozenset({_S.READY_FOR_REVIEW, _S.EDITING})
SENDABLE_STATUSES = frozenset({_S.READY_FOR_REVIEW, _S.EDITING, _S.DELIVERY_FAILED})
# 退回会重新生成草稿，但首次发送的出站编号不带阶段后缀，已经发过（SEND_QUEUED
# 之后的任何状态）再退回，新草稿会撞上去重键永远发不出去；所以只允许尚未发送过
# 的状态退回。SENT、CANCELLED 只读是用户确认的 D2（2026-10-01）。
RETURNABLE_STATUSES = frozenset({_S.READY_FOR_REVIEW, _S.EDITING, _S.ANALYSIS_FAILED})
# 关闭不允许 SEND_QUEUED：出站任务不会因客诉关闭而停发，关了只会让记录与客人
# 实际收到的内容对不上。DELIVERY_FAILED 可以关闭，但原任务仍在自动重试时由服务层
# 另行拒绝（仓储看不到任务状态）。
CANCELLABLE_STATUSES = frozenset(
    {
        _S.PENDING_ANALYSIS,
        _S.READY_FOR_REVIEW,
        _S.EDITING,
        _S.ANALYSIS_FAILED,
        _S.RETURNED,
        _S.DELIVERY_FAILED,
    }
)
# 分析结果只能写进仍在等待分析的客诉。
_ANALYSIS_PENDING_STATUSES = frozenset(
    {_S.PENDING_ANALYSIS, _S.ANALYSIS_FAILED, _S.RETURNED}
)
# worker 回写投递结果时客诉应处于的状态。
_DELIVERY_PENDING_STATUSES = frozenset({_S.SEND_QUEUED, _S.DELIVERY_FAILED})


def _sanitize_text(value: str) -> str:
    """遮盖常见联系方式和长数字，避免分析结果复制敏感信息。"""
    value = re.sub(r"(?<!\d)\d{11}(?!\d)", "[手机号已脱敏]", value)
    value = re.sub(
        r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",
        "[邮箱已脱敏]",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"(?<!\d)\d{12,}(?!\d)", "[编号已脱敏]", value)
    return value[:4000]


def _sanitize_analysis(value: Any) -> Any:
    """递归限制分析结构并脱敏字符串值。"""
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, list):
        return [_sanitize_analysis(item) for item in value[:20]]
    if isinstance(value, dict):
        return {
            str(key)[:64]: _sanitize_analysis(item)
            for key, item in list(value.items())[:40]
        }
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return str(value)[:4000]


class SQLAlchemyComplaintRepository:
    """持久化脱敏客诉记录，并用版本号保护人工编辑。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前业务事务。"""
        self._session = session

    async def get(self, review_id: int) -> ComplaintReview | None:
        """按主键读取客诉记录。"""
        return await self._session.get(ComplaintReview, review_id)

    async def list_open(self, *, offset: int, limit: int) -> list[ComplaintReview]:
        """按最近更新时间分页返回尚未结束的客诉复核。"""
        statement = (
            select(ComplaintReview)
            .where(
                ComplaintReview.status.not_in(
                    (
                        ComplaintReviewStatus.SENT,
                        ComplaintReviewStatus.CANCELLED,
                    )
                )
            )
            .order_by(ComplaintReview.updated_at.desc(), ComplaintReview.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list((await self._session.scalars(statement)).all())

    async def create_or_get(
        self,
        *,
        conversation_id: int,
        source_message_id: str,
        reason: str,
        risk_level: str,
    ) -> ComplaintReview:
        """按来源消息幂等创建客诉记录。"""
        existing = await self._session.scalar(
            select(ComplaintReview).where(
                ComplaintReview.source_message_id == source_message_id
            )
        )
        if existing is not None:
            return existing
        review = ComplaintReview(
            conversation_id=conversation_id,
            source_message_id=source_message_id,
            reason=reason[:64],
            risk_level=risk_level[:32],
        )
        try:
            # 唯一键竞争只回滚当前保存点，不能破坏调用方事务中的其他写入。
            async with self._session.begin_nested():
                self._session.add(review)
                await self._session.flush()
        except IntegrityError:
            existing = await self._session.scalar(
                select(ComplaintReview).where(
                    ComplaintReview.source_message_id == source_message_id
                )
            )
            if existing is None:
                raise
            return cast(ComplaintReview, existing)
        return review

    async def mark_ready(
        self,
        review_id: int,
        *,
        analysis: dict[str, Any],
        draft: str,
        expected_version: int | None = None,
    ) -> ComplaintReview | None:
        """保存脱敏分析和草稿，进入人工复核状态；没写进去时返回 None。

        分析要等模型几十秒，期间管理员可能已经关闭或改动了这条客诉。原先读出对象
        再赋值，迟到的分析会把已关闭的客诉重新打开、覆盖草稿（Codex M2）。这里与其他
        状态变更一样用条件更新：只在仍处于待分析类状态、且版本与开始分析时一致时写入。
        """
        if await self._transition(
            review_id,
            allowed=_ANALYSIS_PENDING_STATUSES,
            expected_version=expected_version,
            values={
                "analysis": _sanitize_analysis(analysis),
                "draft": _sanitize_text(draft),
                "status": _S.READY_FOR_REVIEW,
            },
        ):
            return await self._require(review_id)
        return None

    async def _transition(
        self,
        review_id: int,
        *,
        allowed: frozenset[ComplaintReviewStatus],
        values: dict[str, Any],
        expected_version: int | None = None,
        outbox_id: str | None = None,
        external_message_id: str | None = None,
    ) -> bool:
        """用一条带条件的 UPDATE 完成状态变更，返回是否真的改到了这一行。

        读出对象再比较版本、再赋值的写法在两个请求交错时会丢更新：拿旧版本的关闭
        能覆盖已经提交的发送（Codex 探针 E10）。这里把版本、状态和出站归属都写进
        WHERE，比较与修改是同一条语句，SQLite 与 PostgreSQL 语义一致；行锁在
        SQLite 上不生效，测不出互斥，所以不用。做法沿用 runtime_config 与
        admin_credentials 的条件更新。

        所有要改的字段都放进 values，不在会话里改 ORM 对象，否则之后的 flush 会用
        旧对象把这里的结果覆盖回去；成功后按主键重新读取，刷新会话里的缓存对象。
        """
        conditions = [ComplaintReview.id == review_id, ComplaintReview.status.in_(allowed)]
        if expected_version is not None:
            conditions.append(ComplaintReview.version == expected_version)
        if outbox_id is not None:
            conditions.append(ComplaintReview.delivery_outbox_id == outbox_id[:128])
        if external_message_id is not None:
            conditions.append(
                ComplaintReview.delivery_external_message_id == external_message_id
            )
        result = await self._session.execute(
            update(ComplaintReview)
            .where(*conditions)
            .values(**values, version=ComplaintReview.version + 1)
            .execution_options(synchronize_session=False)
        )
        changed = int(getattr(result, "rowcount", 0) or 0) == 1
        if changed:
            await self._session.get(ComplaintReview, review_id, populate_existing=True)
        return changed

    async def _refuse(
        self, review_id: int, expected_version: int, message: str
    ) -> None:
        """条件更新没改到行时，按最新数据说明原因：不存在、版本过期或状态不允许。"""
        review = await self._session.get(
            ComplaintReview, review_id, populate_existing=True
        )
        if review is None:
            raise LookupError("客诉记录不存在")
        self._check_version(review, expected_version)
        raise ComplaintStateRefused(message)

    async def update_draft(
        self,
        review_id: int,
        *,
        expected_version: int,
        draft: str,
    ) -> ComplaintReview:
        """按版本更新员工编辑内容。"""
        if not await self._transition(
            review_id,
            allowed=EDITABLE_STATUSES,
            expected_version=expected_version,
            values={"draft": _sanitize_text(draft), "status": _S.EDITING},
        ):
            await self._refuse(review_id, expected_version, "当前客诉状态不允许编辑回复")
        return await self._require(review_id)

    async def mark_sent(
        self,
        review_id: int,
        *,
        expected_version: int,
        sent_at: datetime,
    ) -> ComplaintReview:
        """按版本把人工确认后的草稿标记为已发送。"""
        review = await self._require(review_id)
        self._check_version(review, expected_version)
        if review.status not in {
            ComplaintReviewStatus.READY_FOR_REVIEW,
            ComplaintReviewStatus.EDITING,
        }:
            raise ValueError("当前客诉状态不允许发送")
        review.status = ComplaintReviewStatus.SENT
        review.sent_at = sent_at
        review.version += 1
        await self._session.flush()
        return review

    async def mark_send_queued(
        self,
        review_id: int,
        *,
        expected_version: int,
        outbox_id: str,
        draft: str | None = None,
    ) -> ComplaintReview:
        """保存客诉出站任务已入队，等待 worker 回写真实投递结果。

        draft 非空时连同实际发送的正文一起写入，保证记录的草稿就是发出去的内容。
        """
        values: dict[str, Any] = {
            "status": _S.SEND_QUEUED,
            "delivery_error_code": None,
            "delivery_outbox_id": outbox_id[:128],
            "delivery_external_message_id": None,
            "sent_at": None,
        }
        if draft is not None:
            values["draft"] = _sanitize_text(draft)
        if not await self._transition(
            review_id,
            allowed=SENDABLE_STATUSES,
            expected_version=expected_version,
            values=values,
        ):
            await self._refuse(review_id, expected_version, "当前客诉状态不允许发送")
        return await self._require(review_id)

    async def mark_delivery_failed(
        self,
        review_id: int,
        *,
        error_code: str,
        outbox_id: str | None = None,
    ) -> ComplaintReview:
        """记录企业微信实际投递失败，保留安全错误类型供后台重试。

        outbox_id 给出时只接受当前这次发送的回写：旧任务迟到的结果不能覆盖
        之后的新尝试。状态或归属不符时不报错，保持原样。
        """
        await self._transition(
            review_id,
            allowed=_DELIVERY_PENDING_STATUSES,
            outbox_id=outbox_id,
            values={
                "status": _S.DELIVERY_FAILED,
                "delivery_error_code": error_code[:64],
                "sent_at": None,
            },
        )
        return await self._require(review_id)

    async def mark_delivery_sent(
        self,
        review_id: int,
        *,
        sent_at: datetime,
        external_message_id: str,
        outbox_id: str | None = None,
    ) -> ComplaintReview:
        """在企业微信返回真实消息编号后标记客诉已实际发送；归属规则同上。"""
        await self._transition(
            review_id,
            allowed=_DELIVERY_PENDING_STATUSES,
            outbox_id=outbox_id,
            values={
                "status": _S.SENT,
                "sent_at": sent_at,
                "delivery_error_code": None,
                "delivery_external_message_id": external_message_id[:128],
            },
        )
        return await self._require(review_id)

    async def mark_delivery_failed_by_external_message_id(
        self,
        external_message_id: str,
        *,
        error_code: str,
    ) -> ComplaintReview | None:
        """按企业微信真实消息编号回写异步投递失败。

        找不到对应客诉时返回 None，调用方据此改走普通机器人消息的失败处理；
        找到但状态已变时原样返回，不能误当成普通消息。
        """
        review_id = await self._session.scalar(
            select(ComplaintReview.id).where(
                ComplaintReview.delivery_external_message_id == external_message_id
            )
        )
        if review_id is None:
            return None
        await self._transition(
            review_id,
            allowed=frozenset({_S.SENT, _S.SEND_QUEUED, _S.DELIVERY_FAILED}),
            external_message_id=external_message_id,
            values={
                "status": _S.DELIVERY_FAILED,
                "sent_at": None,
                "delivery_error_code": error_code[:64],
            },
        )
        return await self._require(review_id)

    async def mark_returned(
        self, review_id: int, *, expected_version: int
    ) -> ComplaintReview:
        """按版本退回客诉，允许后台重新生成分析。"""
        if not await self._transition(
            review_id,
            allowed=RETURNABLE_STATUSES,
            expected_version=expected_version,
            values={"status": _S.RETURNED},
        ):
            await self._refuse(
                review_id,
                expected_version,
                "当前客诉状态不能退回重新分析：已进入发送流程或已结束的客诉只能查看",
            )
        return await self._require(review_id)

    async def mark_cancelled(
        self, review_id: int, *, expected_version: int
    ) -> ComplaintReview:
        """按版本关闭客诉，避免继续发送草稿。"""
        if not await self._transition(
            review_id,
            allowed=CANCELLABLE_STATUSES,
            expected_version=expected_version,
            values={"status": _S.CANCELLED},
        ):
            await self._refuse(
                review_id,
                expected_version,
                "当前客诉状态不能关闭：回复正在发送或客诉已结束",
            )
        return await self._require(review_id)

    async def _require(self, review_id: int) -> ComplaintReview:
        """读取客诉记录，不存在时返回明确错误。"""
        review = await self.get(review_id)
        if review is None:
            raise LookupError("客诉记录不存在")
        return review

    @staticmethod
    def _check_version(review: ComplaintReview, expected_version: int) -> None:
        """拒绝覆盖其他员工已经提交的新版本。"""
        if review.version != expected_version:
            raise ComplaintVersionConflict("客诉草稿已被其他员工更新")
