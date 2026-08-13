import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from weakref import WeakKeyDictionary

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from homestay_bot.domain.enums import ComplaintReviewStatus, JobStatus
from homestay_bot.domain.models import ComplaintReview, Job

_SQLITE_CLAIM_LOCKS: WeakKeyDictionary[AsyncEngine, asyncio.Lock] = WeakKeyDictionary()
_SENSITIVE_PAYLOAD_JOB_TYPES = frozenset(
    {
        "wecom_sync",
        "wecom_process_message",
        "wecom_send_text",
        "wecom_send_internal_text",
        "wecom_send_internal_card",
    }
)


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
        self._claim_lock: asyncio.Lock | None = None

    async def exists_dedupe_key(self, dedupe_key: str) -> bool:
        """判断幂等键是否已有任务，供分阶段出站避免重复写入。"""
        statement = select(Job.id).where(Job.dedupe_key == dedupe_key)
        return await self._session.scalar(statement) is not None

    async def status_for_dedupe_key(self, dedupe_key: str) -> JobStatus | None:
        """按幂等键只读返回任务状态，不读取已经清空或可能敏感的载荷。"""

        return cast(
            JobStatus | None,
            await self._session.scalar(
                select(Job.status).where(Job.dedupe_key == dedupe_key)
            ),
        )

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
            existing = cast(
                Job | None,
                await self._session.scalar(
                    select(Job).where(Job.dedupe_key == dedupe_key)
                ),
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
        # begin_nested 的隐式预刷新必须在捕获范围外完成，避免误吞外层事务错误。
        await self._session.flush()
        try:
            async with self._session.begin_nested():
                # 只把候选任务放进保存点，唯一键竞争不得回滚外层业务写入。
                self._session.add(job)
                await self._session.flush()
        except IntegrityError:
            if dedupe_key is None:
                raise
            existing = cast(
                Job | None,
                await self._session.scalar(
                    select(Job).where(Job.dedupe_key == dedupe_key)
                ),
            )
            if existing is None:
                raise
            return existing
        return job

    async def claim_next(self, *, now: datetime | None = None) -> Job | None:
        """使用行锁领取任务；SQLite 下持锁到 RUNNING 状态提交完成。"""
        claim_time = now or datetime.now(UTC)
        claim_lock = self._sqlite_claim_lock()
        if claim_lock is not None:
            # SQLite 不支持 FOR UPDATE，进程锁必须覆盖读取 PENDING 到提交 RUNNING；
            # handler 执行阶段无需持锁，不同任务可由两个 worker 并发处理。
            await claim_lock.acquire()
            self._claim_lock = claim_lock
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
        try:
            job = await self._session.scalar(statement)
            if job is None:
                await self.release_claim_lock()
                return None
            job.status = JobStatus.RUNNING
            job.attempts += 1
            job.locked_at = claim_time
            await self._session.flush()
            return job
        except BaseException:
            await self.release_claim_lock()
            raise

    def _sqlite_claim_lock(self) -> asyncio.Lock | None:
        """返回当前 SQLite 引擎的进程内领取锁，PostgreSQL 不需要该锁。"""
        bind = self._session.bind
        if bind is None or bind.dialect.name != "sqlite":
            return None
        engine = cast(AsyncEngine, bind)
        lock = _SQLITE_CLAIM_LOCKS.get(engine)
        if lock is None:
            lock = asyncio.Lock()
            _SQLITE_CLAIM_LOCKS[engine] = lock
        return lock

    async def release_claim_lock(self) -> None:
        """释放 SQLite 领取锁；由 worker 在领取提交后或异常退出时调用。"""
        if self._claim_lock is None:
            return
        lock, self._claim_lock = self._claim_lock, None
        if lock.locked():
            lock.release()

    def _job_type_conditions(self) -> list[Any]:
        """构造当前 worker 的任务类型过滤条件，供领取和恢复共用。"""
        conditions: list[Any] = []
        if self._included_job_types is not None:
            conditions.append(Job.job_type.in_(self._included_job_types))
        if self._excluded_job_types:
            conditions.append(Job.job_type.not_in(self._excluded_job_types))
        return conditions

    async def recover_stale(
        self,
        *,
        before: datetime,
        max_attempts: int = 3,
    ) -> int:
        """按 worker 类型恢复遗留任务，并在达到上限时终止重试。"""
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
                *self._job_type_conditions(),
            )
            .values(
                status=JobStatus.FAILED,
                locked_at=None,
                last_error_code="stale_non_replayable",
                payload={},
            )
        )
        normalized_max_attempts = max(1, max_attempts)
        maxed_out_statement = (
            update(Job)
            .where(
                Job.status == JobStatus.RUNNING,
                Job.locked_at < before,
                Job.job_type.not_in(non_replayable_types),
                Job.attempts >= normalized_max_attempts,
                *self._job_type_conditions(),
            )
            .values(
                status=JobStatus.FAILED,
                locked_at=None,
                last_error_code="stale_retry_limit",
                payload={},
            )
        )
        retry_statement = (
            update(Job)
            .where(
                Job.status == JobStatus.RUNNING,
                Job.locked_at < before,
                Job.job_type.not_in(non_replayable_types),
                Job.attempts < normalized_max_attempts,
                *self._job_type_conditions(),
            )
            .values(
                status=JobStatus.PENDING,
                locked_at=None,
                last_error_code="stale_lock_recovered",
            )
        )
        await self._mark_stale_complaint_deliveries(before)
        failed = cast(
            CursorResult[Any], await self._session.execute(failed_statement)
        )
        maxed_out = cast(
            CursorResult[Any], await self._session.execute(maxed_out_statement)
        )
        retried = cast(
            CursorResult[Any], await self._session.execute(retry_statement)
        )
        await self._session.flush()
        return (
            int(failed.rowcount)
            + int(maxed_out.rowcount)
            + int(retried.rowcount)
        )

    async def _mark_stale_complaint_deliveries(self, before: datetime) -> None:
        """把遗留客诉发送任务同步回写为投递失败，避免状态永久排队。"""
        jobs = list(
            (
                await self._session.scalars(
                    select(Job).where(
                        Job.status == JobStatus.RUNNING,
                        Job.locked_at < before,
                        Job.job_type == "wecom_send_text",
                        *self._job_type_conditions(),
                    )
                )
            ).all()
        )
        for job in jobs:
            outbox_id = job.payload.get("outbox_id")
            if not isinstance(outbox_id, str) or not outbox_id:
                continue
            review = await self._session.scalar(
                select(ComplaintReview).where(
                    ComplaintReview.delivery_outbox_id == outbox_id
                )
            )
            if review is None or review.status is not ComplaintReviewStatus.SEND_QUEUED:
                continue
            review.status = ComplaintReviewStatus.DELIVERY_FAILED
            review.sent_at = None
            review.delivery_error_code = "stale_non_replayable"
            review.version += 1

    async def mark_completed(self, job: Job) -> None:
        """标记任务完成并清除锁时间。"""
        job.status = JobStatus.COMPLETED
        job.locked_at = None
        job.last_error_code = None
        if job.job_type in _SENSITIVE_PAYLOAD_JOB_TYPES:
            # 完成后不再需要客人或员工通知正文，避免任务表绕过保留策略。
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
            if job.job_type in _SENSITIVE_PAYLOAD_JOB_TYPES:
                # 失败终态只保留状态和错误码，避免任务表长期留存消息正文。
                job.payload = {}
        await self._session.flush()
