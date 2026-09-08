from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import and_, delete, or_, select, update
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
from homestay_bot.services.task_page_service import (
    ATTACHMENT_CLEANUP_JOB_TYPE,
)


class SQLAlchemyRetentionRepository:
    """按保守默认期限清理不会再参与业务流程的历史记录。"""

    JOB_RETENTION_DAYS = 30
    EXTERNAL_REQUEST_RETENTION_DAYS = 90
    WEBHOOK_RETENTION_DAYS = 90
    AUDIT_RETENTION_DAYS = 365
    BOOKED_APPROVAL_PII_RETENTION_DAYS = 30
    TERMINAL_APPROVAL_PII_RETENTION_DAYS = 90
    ARCHIVED_TASK_RETENTION_DAYS = 180

    def __init__(self, session: AsyncSession) -> None:
        """绑定清理事务。"""
        self._session = session

    async def purge(self, *, now: datetime | None = None) -> dict[str, int]:
        """只删除终态历史，返回各表删除数量供日志和测试核对。"""
        current = (now or datetime.now(UTC)).astimezone(UTC)
        statements = {
            "booking_approval_pii": (
                update(BookingApproval)
                .where(
                    BookingApproval.pii_purged_at.is_(None),
                    or_(
                        and_(
                            BookingApproval.status == ApprovalStatus.BOOKED,
                            BookingApproval.check_out_date
                            <= (
                                current
                                - timedelta(
                                    days=self.BOOKED_APPROVAL_PII_RETENTION_DAYS
                                )
                            ).date(),
                        ),
                        and_(
                            BookingApproval.status.in_(
                                [ApprovalStatus.REJECTED, ApprovalStatus.CONFLICT]
                            ),
                            BookingApproval.updated_at
                            <= current
                            - timedelta(
                                days=self.TERMINAL_APPROVAL_PII_RETENTION_DAYS
                            ),
                        ),
                    ),
                    or_(
                        BookingApproval.guest_name_ciphertext.is_not(None),
                        BookingApproval.guest_mobile_ciphertext.is_not(None),
                        BookingApproval.special_requests_ciphertext.is_not(None),
                    ),
                )
                .values(
                    guest_name_ciphertext=None,
                    guest_mobile_ciphertext=None,
                    special_requests_ciphertext=None,
                    pii_purged_at=current,
                )
            ),
            "jobs": delete(Job).where(
                Job.status.in_([JobStatus.COMPLETED, JobStatus.FAILED]),
                Job.updated_at < current - timedelta(days=self.JOB_RETENTION_DAYS),
            ),
            "external_requests": delete(ExternalRequest).where(
                ExternalRequest.created_at
                < current - timedelta(days=self.EXTERNAL_REQUEST_RETENTION_DAYS)
            ),
            "hostex_webhook_events": delete(HostexWebhookEvent).where(
                HostexWebhookEvent.status != "pending",
                HostexWebhookEvent.updated_at
                < current - timedelta(days=self.WEBHOOK_RETENTION_DAYS),
            ),
            "audit_logs": delete(AuditLog).where(
                AuditLog.created_at
                < current - timedelta(days=self.AUDIT_RETENTION_DAYS)
            ),
        }
        deleted: dict[str, int] = {}
        for name, statement in statements.items():
            result = cast(CursorResult[Any], await self._session.execute(statement))
            deleted[name] = int(result.rowcount or 0)
        return deleted

    async def purge_archived_tasks(
        self,
        *,
        delete_file: Callable[[str], None] | None = None,
        now: datetime | None = None,
    ) -> int:
        """删除归档超过保留期的任务及其现场照片，返回删除数量。

        与 purge 分开是因为这是唯一带副作用的清理：附件行由外键级联删除，但
        磁盘上的照片必须显式删除，否则会留下永远无人认领的孤儿文件。

        只删已归档任务。未归档的任务无论多旧都不会被自动删除——自动删除掉
        还没人处理的活，比留着它危险得多。
        """
        current = (now or datetime.now(UTC)).astimezone(UTC)
        cutoff = current - timedelta(days=self.ARCHIVED_TASK_RETENTION_DAYS)
        expired = list(
            await self._session.scalars(
                select(BusinessTask).where(
                    BusinessTask.archived_at.is_not(None),
                    BusinessTask.archived_at < cutoff,
                )
            )
        )
        if not expired:
            return 0
        task_ids = [task.id for task in expired]
        file_ids = list(
            await self._session.scalars(
                select(TaskAttachment.private_file_id).where(
                    TaskAttachment.task_id.in_(task_ids)
                )
            )
        )
        await self._session.execute(
            delete(BusinessTask).where(BusinessTask.id.in_(task_ids))
        )
        # 照片清理登记进同一事务，提交之后才由 worker 幂等执行。先删文件再删库
        # 时提交一旦失败，数据库回滚而照片已经没了，没有备份就无法重建原图。
        if file_ids:
            await SQLAlchemyJobRepository(self._session).enqueue(
                ATTACHMENT_CLEANUP_JOB_TYPE,
                {"file_ids": list(file_ids)},
                dedupe_key="task-retention:" + ",".join(str(v) for v in sorted(task_ids)),
            )
        return len(task_ids)
