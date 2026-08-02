from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import JobStatus
from homestay_bot.domain.models import AuditLog, ExternalRequest, HostexWebhookEvent, Job


class SQLAlchemyRetentionRepository:
    """按保守默认期限清理不会再参与业务流程的历史记录。"""

    JOB_RETENTION_DAYS = 30
    EXTERNAL_REQUEST_RETENTION_DAYS = 90
    WEBHOOK_RETENTION_DAYS = 90
    AUDIT_RETENTION_DAYS = 365

    def __init__(self, session: AsyncSession) -> None:
        """绑定清理事务。"""
        self._session = session

    async def purge(self, *, now: datetime | None = None) -> dict[str, int]:
        """只删除终态历史，返回各表删除数量供日志和测试核对。"""
        current = (now or datetime.now(UTC)).astimezone(UTC)
        statements = {
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
