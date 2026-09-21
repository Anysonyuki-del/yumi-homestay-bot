from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import ColumnElement, and_, delete, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import ApprovalStatus, JobStatus
from homestay_bot.domain.models import (
    AuditLog,
    BookingApproval,
    BusinessTask,
    ExternalRequest,
    HostexWebhookEvent,
    Job,
    TaskAttachment,
)
from homestay_bot.repositories.jobs import SQLAlchemyJobRepository
from homestay_bot.repositories.operations import SQLAlchemyOperationsRepository
from homestay_bot.services.task_page_service import (
    ATTACHMENT_CLEANUP_JOB_TYPE,
    build_attachment_cleanup_dedupe_key,
)


def _require_positive_batch(batch_size: int) -> None:
    """拒绝零或负数批量，避免被误传后变成无上限的一次性删除。"""
    if isinstance(batch_size, bool) or batch_size <= 0:
        raise ValueError(f"清理批量必须是正整数：{batch_size!r}")


class SQLAlchemyRetentionRepository:
    """按保守默认期限清理不会再参与业务流程的历史记录。

    每次调用只处理有界的一批，且从不自行提交：事务边界归调度方所有，一批一个
    短事务，失败只回滚当前批。
    """

    JOB_RETENTION_DAYS = 30
    EXTERNAL_REQUEST_RETENTION_DAYS = 90
    WEBHOOK_RETENTION_DAYS = 90
    AUDIT_RETENTION_DAYS = 365
    BOOKED_APPROVAL_PII_RETENTION_DAYS = 30
    TERMINAL_APPROVAL_PII_RETENTION_DAYS = 90
    ARCHIVED_TASK_RETENTION_DAYS = 180
    # 通用清理按每类记录计数，归档清理按父任务计数；附件行数和清理载荷不受
    # 这个上限约束。
    PURGE_BATCH_SIZE = 500
    ARCHIVED_TASK_BATCH_SIZE = 100

    def __init__(self, session: AsyncSession) -> None:
        """绑定清理事务。"""
        self._session = session

    async def purge(
        self,
        *,
        now: datetime | None = None,
        batch_size: int = PURGE_BATCH_SIZE,
    ) -> dict[str, int]:
        """每类终态历史最多处理一批，返回各类实际处理数量。

        先按原资格条件和主键顺序选出至多 batch_size 个编号，再对这些编号执行
        修改；修改语句仍带原资格条件，快照之后状态已变化的行不会被误删。比较符
        保持原样：审批 PII 用 `<=`，其余用 `<`。调度方依据「某类是否满批」决定
        是否继续下一批。
        """
        _require_positive_batch(batch_size)
        current = (now or datetime.now(UTC)).astimezone(UTC)
        booked_pii_cutoff = (
            current - timedelta(days=self.BOOKED_APPROVAL_PII_RETENTION_DAYS)
        ).date()
        terminal_pii_cutoff = current - timedelta(
            days=self.TERMINAL_APPROVAL_PII_RETENTION_DAYS
        )
        approval_pii_eligible: Sequence[ColumnElement[bool]] = (
            BookingApproval.pii_purged_at.is_(None),
            or_(
                and_(
                    BookingApproval.status == ApprovalStatus.BOOKED,
                    BookingApproval.check_out_date <= booked_pii_cutoff,
                ),
                and_(
                    BookingApproval.status.in_(
                        [ApprovalStatus.REJECTED, ApprovalStatus.CONFLICT]
                    ),
                    BookingApproval.updated_at <= terminal_pii_cutoff,
                ),
            ),
            or_(
                BookingApproval.guest_name_ciphertext.is_not(None),
                BookingApproval.guest_mobile_ciphertext.is_not(None),
                BookingApproval.special_requests_ciphertext.is_not(None),
            ),
        )
        deleted: dict[str, int] = {}
        candidate_ids = await self._candidate_ids(
            BookingApproval, approval_pii_eligible, batch_size
        )
        deleted["booking_approval_pii"] = await self._execute_bounded(
            update(BookingApproval)
            .where(BookingApproval.id.in_(candidate_ids), *approval_pii_eligible)
            .values(
                guest_name_ciphertext=None,
                guest_mobile_ciphertext=None,
                special_requests_ciphertext=None,
                pii_purged_at=current,
            ),
            candidate_ids,
        )
        deletions: tuple[tuple[str, Any, Sequence[ColumnElement[bool]]], ...] = (
            (
                "jobs",
                Job,
                (
                    Job.status.in_([JobStatus.COMPLETED, JobStatus.FAILED]),
                    Job.updated_at
                    < current - timedelta(days=self.JOB_RETENTION_DAYS),
                ),
            ),
            (
                "external_requests",
                ExternalRequest,
                (
                    ExternalRequest.created_at
                    < current - timedelta(days=self.EXTERNAL_REQUEST_RETENTION_DAYS),
                ),
            ),
            (
                "hostex_webhook_events",
                HostexWebhookEvent,
                (
                    HostexWebhookEvent.status != "pending",
                    HostexWebhookEvent.updated_at
                    < current - timedelta(days=self.WEBHOOK_RETENTION_DAYS),
                ),
            ),
            (
                "audit_logs",
                AuditLog,
                (
                    AuditLog.created_at
                    < current - timedelta(days=self.AUDIT_RETENTION_DAYS),
                ),
            ),
        )
        for name, model, eligible in deletions:
            candidate_ids = await self._candidate_ids(model, eligible, batch_size)
            deleted[name] = await self._execute_bounded(
                delete(model).where(model.id.in_(candidate_ids), *eligible),
                candidate_ids,
            )
        return deleted

    async def _candidate_ids(
        self,
        model: Any,
        eligible: Sequence[ColumnElement[bool]],
        batch_size: int,
    ) -> list[int]:
        """按主键顺序选出至多一批合格记录的编号。"""
        return list(
            await self._session.scalars(
                select(model.id).where(*eligible).order_by(model.id).limit(batch_size)
            )
        )

    async def _execute_bounded(self, statement: Any, candidate_ids: list[int]) -> int:
        """只对本批候选编号执行修改，返回实际影响行数；无候选时不发语句。"""
        if not candidate_ids:
            return 0
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                statement.execution_options(synchronize_session="fetch")
            ),
        )
        return int(result.rowcount or 0)

    async def purge_archived_tasks(
        self,
        *,
        now: datetime | None = None,
        batch_size: int = ARCHIVED_TASK_BATCH_SIZE,
    ) -> int:
        """删除一批归档超过保留期的任务，返回实际删除的父任务数。

        只删已归档任务。未归档的任务无论多旧都不会被自动删除——自动删除掉还没
        人处理的活，比留着它危险得多。

        在 PostgreSQL 上按编号顺序以 SKIP LOCKED 锁住本批父任务直到本批提交：
        管理员正锁着的任务本轮跳过，留到下一轮；本批先锁住的任务，恢复归档只能
        等本批提交后看到结果。SQLite 忽略行锁，这里不以它作并发证据。返回不足
        一批只表示本轮到此为止，不证明全库已无积压。

        附件编号必须在删除前读出，否则会随外键级联消失。DELETE 仍带归档条件并
        用 RETURNING 取实际删除集合，墓碑、照片清理登记与计数只按这个集合来，
        和删除同事务提交；磁盘照片由提交后的 worker 删除，这里不碰文件。
        """
        _require_positive_batch(batch_size)
        current = (now or datetime.now(UTC)).astimezone(UTC)
        cutoff = current - timedelta(days=self.ARCHIVED_TASK_RETENTION_DAYS)
        eligible = (
            BusinessTask.archived_at.is_not(None),
            BusinessTask.archived_at < cutoff,
        )
        candidate_ids = list(
            await self._session.scalars(
                select(BusinessTask.id)
                .where(*eligible)
                .order_by(BusinessTask.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        )
        if not candidate_ids:
            return 0
        attachments = (
            await self._session.execute(
                select(TaskAttachment.task_id, TaskAttachment.private_file_id)
                .where(TaskAttachment.task_id.in_(candidate_ids))
                .order_by(TaskAttachment.id)
            )
        ).all()
        rows = (
            await self._session.execute(
                delete(BusinessTask)
                .where(BusinessTask.id.in_(candidate_ids), *eligible)
                .returning(BusinessTask.id, BusinessTask.dedupe_key)
                .execution_options(synchronize_session="fetch")
            )
        ).all()
        if not rows:
            return 0
        deleted_ids = sorted(row.id for row in rows)
        deleted_set = set(deleted_ids)
        await SQLAlchemyOperationsRepository(self._session).mark_purged_many(
            [row.dedupe_key for row in rows],
            now=current,
        )
        file_ids = [
            file_id for task_id, file_id in attachments if task_id in deleted_set
        ]
        # 照片清理登记进同一事务，提交之后才由 worker 幂等执行。先删文件再删库
        # 时提交一旦失败，数据库回滚而照片已经没了，没有备份就无法重建原图。
        if file_ids:
            await SQLAlchemyJobRepository(self._session).enqueue(
                ATTACHMENT_CLEANUP_JOB_TYPE,
                {"file_ids": file_ids},
                dedupe_key=build_attachment_cleanup_dedupe_key(
                    deleted_ids, source="retention"
                ),
            )
        return len(deleted_ids)
