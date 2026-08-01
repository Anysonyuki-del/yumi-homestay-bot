from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import JobStatus
from homestay_bot.domain.models import Job


class SQLAlchemyJobRepository:
    """使用数据库行锁实现可恢复的持久化任务队列。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        included_job_types: set[str] | None = None,
        excluded_job_types: set[str] | None = None,
    ) -> None:
        """绑定当前 worker 数据库会话。"""
        self._session = session
        self._included_job_types = included_job_types
        self._excluded_job_types = excluded_job_types or set()

    async def exists_dedupe_key(self, dedupe_key: str) -> bool:
        """判断幂等键是否已有任务，供分阶段出站避免重复写入。"""
        statement = select(Job.id).where(Job.dedupe_key == dedupe_key)
        return await self._session.scalar(statement) is not None

    async def enqueue(
        self,
        job_type: str,
        payload: dict[str, Any],
        *,
        available_at: datetime | None = None,
        dedupe_key: str | None = None,
    ) -> Job:
        """创建待执行任务并立即刷新主键。"""
        if dedupe_key is not None:
            existing = await self._session.scalar(
                select(Job).where(Job.dedupe_key == dedupe_key)
            )
            if existing is not None:
                return existing
        job = Job(
            job_type=job_type,
            dedupe_key=dedupe_key,
            payload=payload,
            status=JobStatus.PENDING,
            attempts=0,
            available_at=available_at or datetime.now(UTC),
        )
        self._session.add(job)
        await self._session.flush()
        return job

    async def claim_next(self, *, now: datetime | None = None) -> Job | None:
        """使用 FOR UPDATE SKIP LOCKED 领取一项到期任务。"""
        claim_time = now or datetime.now(UTC)
        conditions = [
            Job.status == JobStatus.PENDING,
            Job.available_at <= claim_time,
        ]
        if self._included_job_types is not None:
            conditions.append(Job.job_type.in_(self._included_job_types))
        if self._excluded_job_types:
            conditions.append(Job.job_type.not_in(self._excluded_job_types))
        statement = (
            select(Job)
            .where(*conditions)
            .order_by(Job.available_at, Job.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        job = await self._session.scalar(statement)
        if job is None:
            return None
        job.status = JobStatus.RUNNING
        job.attempts += 1
        job.locked_at = claim_time
        await self._session.flush()
        return job

    async def recover_stale(self, *, before: datetime) -> int:
        """恢复安全任务；外部发送和下单任务超时后转人工，不自动重放。"""
        non_replayable_types = {
            "credential_send_part",
            "wecom_send_text",
            "wecom_send_internal_text",
            "wecom_send_internal_card",
            "hostex_create_reservation",
            "lifecycle_send",
        }
        failed_statement = (
            update(Job)
            .where(
                Job.status == JobStatus.RUNNING,
                Job.locked_at < before,
                Job.job_type.in_(non_replayable_types),
            )
            .values(
                status=JobStatus.FAILED,
                locked_at=None,
                last_error_code="stale_non_replayable",
            )
        )
        retry_statement = (
            update(Job)
            .where(
                Job.status == JobStatus.RUNNING,
                Job.locked_at < before,
                Job.job_type.not_in(non_replayable_types),
            )
            .values(
                status=JobStatus.PENDING,
                locked_at=None,
                last_error_code="stale_lock_recovered",
            )
        )
        failed = cast(
            CursorResult[Any], await self._session.execute(failed_statement)
        )
        retried = cast(
            CursorResult[Any], await self._session.execute(retry_statement)
        )
        await self._session.flush()
        return int(failed.rowcount) + int(retried.rowcount)

    async def mark_completed(self, job: Job) -> None:
        """标记任务完成并清除锁时间。"""
        job.status = JobStatus.COMPLETED
        job.locked_at = None
        job.last_error_code = None
        if job.job_type in {"wecom_process_message", "wecom_send_text"}:
            # 完成后不再需要原始客文，避免任务表绕过七天消息清理长期留存正文。
            job.payload = {}
        await self._session.flush()

    async def mark_failed(
        self,
        job: Job,
        *,
        error_code: str,
        retry_allowed: bool,
        max_attempts: int,
    ) -> None:
        """按明确重试策略延迟重排，禁止写任务被自动重放。"""
        job.last_error_code = error_code[:64]
        job.locked_at = None
        if retry_allowed and job.attempts < max_attempts:
            job.status = JobStatus.PENDING
            job.available_at = datetime.now(UTC) + timedelta(
                # 长期等待管理员配置的任务按小时低频重试，避免指数溢出。
                seconds=min(2 ** min(max(job.attempts, 1), 12), 3600)
            )
        else:
            job.status = JobStatus.FAILED
        await self._session.flush()
