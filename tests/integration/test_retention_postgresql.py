"""真实 PostgreSQL 上的清理约束、事务与行锁验证。

SQLite 不检查 VARCHAR 长度、忽略 FOR UPDATE，这里的用例只能在 PostgreSQL 上
证明。连接串只从 YUMI_TEST_POSTGRES_URL 读取，且必须指向本机专用测试库与测试
角色；绝不回退到应用的 DATABASE_URL 或 .env。缺少变量时跳过，正式验收命令须先
确认变量存在，跳过不能计为通过。
"""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import BusinessTaskStatus, BusinessTaskType, EmployeeRole
from homestay_bot.domain.errors import OperationRefused
from homestay_bot.domain.models import (
    BusinessTask,
    Employee,
    Job,
    PropertyProfile,
    PurgedTaskMark,
    TaskAttachment,
)
from homestay_bot.repositories.jobs import SQLAlchemyJobRepository
from homestay_bot.repositories.operations import (
    PURGED_MARK_RETENTION_DAYS,
    SQLAlchemyOperationsRepository,
)
from homestay_bot.repositories.retention import SQLAlchemyRetentionRepository
from homestay_bot.services.task_page_service import ATTACHMENT_CLEANUP_JOB_TYPE

TEST_URL_ENV = "YUMI_TEST_POSTGRES_URL"
_ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_TEST_DATABASE = "yumi_test_retention"
_TEST_ROLE = "yumi_test"
# 判定「另一会话确实在等锁」的观察窗口；远小于连接上设置的 lock_timeout。
_BLOCK_OBSERVATION_SECONDS = 0.5


def validated_test_url(raw: str) -> URL:
    """只接受本机专用测试库与测试角色的 asyncpg 连接串，在连接之前拒绝其他目标。

    查询参数一律拒绝：asyncpg 会把 host、database、user 等查询参数当作连接
    参数，可以绕过前面对主机和库名的检查。错误信息不回显连接串，避免带出密码。
    """
    url = make_url(raw)
    if url.drivername != "postgresql+asyncpg":
        raise ValueError("隔离测试库只接受 postgresql+asyncpg 驱动")
    if url.host not in _ALLOWED_HOSTS:
        raise ValueError("隔离测试库只允许本机回环地址")
    if url.database != _TEST_DATABASE:
        raise ValueError(f"隔离测试库名必须是 {_TEST_DATABASE}")
    if url.username != _TEST_ROLE:
        raise ValueError(f"隔离测试角色必须是 {_TEST_ROLE}")
    if url.query:
        raise ValueError("隔离测试连接串不接受查询参数")
    return url


@pytest.mark.parametrize(
    "raw",
    [
        "postgresql+asyncpg://yumi_test:pw@127.0.0.1:5432/yumi_test_retention",
        "postgresql+asyncpg://yumi_test:pw@localhost/yumi_test_retention",
        "postgresql+asyncpg://yumi_test:pw@[::1]:5432/yumi_test_retention",
    ],
)
def test_test_url_accepts_only_local_dedicated_database(raw: str) -> None:
    """本机回环地址上的专用库与专用角色可以通过。"""
    assert validated_test_url(raw).database == _TEST_DATABASE


@pytest.mark.parametrize(
    "raw",
    [
        "postgresql+psycopg://yumi_test:secret-pw@127.0.0.1/yumi_test_retention",
        "postgresql+asyncpg://yumi_test:secret-pw@db.example.com/yumi_test_retention",
        "postgresql+asyncpg://yumi_test:secret-pw@127.0.0.1/homestay",
        "postgresql+asyncpg://homestay:secret-pw@127.0.0.1/yumi_test_retention",
        "postgresql+asyncpg://yumi_test:secret-pw@127.0.0.1/yumi_test_retention"
        "?host=db.example.com",
        "sqlite+aiosqlite:///./yumi_test_retention",
    ],
)
def test_test_url_rejects_other_targets_without_echoing_secrets(raw: str) -> None:
    """驱动、主机、库名、角色任一不符或带查询参数都拒绝，且不回显密码。"""
    with pytest.raises(ValueError) as refused:
        validated_test_url(raw)
    assert "secret-pw" not in str(refused.value)


@pytest_asyncio.fixture
async def pg_engine() -> AsyncIterator[AsyncEngine]:
    """连接已迁移到 head 的隔离测试库，每个用例前清空全部业务表。"""
    raw = os.environ.get(TEST_URL_ENV)
    if not raw:
        pytest.skip(f"未设置 {TEST_URL_ENV}，PostgreSQL 用例未执行")
    engine = create_async_engine(
        validated_test_url(raw),
        # 锁等待有上限：实现若意外死锁，用例失败而不是把 CI 挂住。
        connect_args={"server_settings": {"lock_timeout": "5000"}},
    )
    async with engine.begin() as connection:
        # 只在通过上述校验的专用测试库内清数据；表清单取自迁移后的实际库，
        # 保留 alembic_version，不依赖模型与迁移逐表一致。
        tables = list(
            await connection.scalars(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                )
            )
        )
        if tables:
            joined = ", ".join(f'"{name}"' for name in tables)
            await connection.execute(text(f"TRUNCATE {joined} RESTART IDENTITY CASCADE"))
    try:
        yield engine
    finally:
        await engine.dispose()


async def _seed(factory: async_sessionmaker, tasks: list[BusinessTask]) -> list[int]:
    """写入房间、管理员和给定任务，返回任务编号。"""
    async with factory() as session:
        session.add_all(
            [
                PropertyProfile(id=101, title="测试房间"),
                Employee(id=1, wecom_userid="admin", name="管理员", role=EmployeeRole.ADMIN),
            ]
        )
        await session.flush()
        session.add_all(tasks)
        await session.commit()
    return [task.id for task in tasks]


def _archived(
    *,
    archived_at: datetime,
    task_id: int | None = None,
    dedupe_key: str | None = None,
) -> BusinessTask:
    """构造一条已归档的终态任务。"""
    return BusinessTask(
        id=task_id,
        dedupe_key=dedupe_key,
        task_type=BusinessTaskType.CLEANING,
        status=BusinessTaskStatus.COMPLETED,
        property_id=101,
        service_date=date(2025, 1, 1),
        description="已归档",
        archived_at=archived_at,
    )


async def _is_blocked(task: asyncio.Task) -> bool:
    """观察一段时间后协程仍未结束，即视为在等行锁。"""
    await asyncio.sleep(_BLOCK_OBSERVATION_SECONDS)
    return not task.done()


@pytest.mark.asyncio
async def test_legacy_key_is_rejected_and_bounded_key_commits(pg_engine) -> None:
    """T1：旧格式超长键被 PostgreSQL 以 22001 拒绝；新实现 40 个五位数编号照常提交。"""
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    now = datetime.now(UTC)
    task_ids = list(range(10_000, 10_040))
    await _seed(
        factory,
        [
            _archived(task_id=task_id, archived_at=now - timedelta(days=200))
            for task_id in task_ids
        ],
    )
    async with factory() as session:
        session.add_all(
            TaskAttachment(task_id=task_id, private_file_id=f"{task_id:032x}.png", kind="photo")
            for task_id in task_ids
        )
        await session.commit()

    legacy_key = "task-retention:" + ",".join(str(value) for value in task_ids)
    async with factory() as session:
        with pytest.raises(DBAPIError) as rejected:
            await SQLAlchemyJobRepository(session).enqueue(
                ATTACHMENT_CLEANUP_JOB_TYPE,
                {"file_ids": []},
                dedupe_key=legacy_key,
            )
        assert getattr(rejected.value.orig, "sqlstate", None) == "22001"
        await session.rollback()

    async with factory() as session:
        assert await SQLAlchemyRetentionRepository(session).purge_archived_tasks(
            now=now
        ) == 40
        await session.commit()

    async with factory() as session:
        job = await session.scalar(select(Job))
        assert job is not None and job.dedupe_key is not None
        assert len(job.dedupe_key) <= 128
        assert len(job.payload["file_ids"]) == 40
        assert await session.scalar(select(func.count()).select_from(TaskAttachment)) == 0


@pytest.mark.asyncio
async def test_retention_skips_task_locked_by_restore_and_keeps_going(pg_engine) -> None:
    """T9：管理员恢复先持锁时，清理跳过该任务并继续处理其他合格任务。

    恢复提交后该任务已不合格，下一批也不会删除它。
    """
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    old = datetime.now(UTC) - timedelta(days=200)
    restoring, other = await _seed(
        factory,
        [_archived(archived_at=old), _archived(archived_at=old)],
    )

    async with factory() as admin, factory() as cleaner:
        await SQLAlchemyOperationsRepository(admin).restore_task(restoring, 1)
        # 管理员尚未提交，行锁仍在
        deleted = await SQLAlchemyRetentionRepository(cleaner).purge_archived_tasks()
        await cleaner.commit()
        assert deleted == 1
        await admin.commit()

    async with factory() as cleaner:
        assert await SQLAlchemyRetentionRepository(cleaner).purge_archived_tasks() == 0
        await cleaner.commit()

    async with factory() as session:
        kept = await session.get(BusinessTask, restoring)
        assert kept is not None and kept.archived_at is None
        assert await session.get(BusinessTask, other) is None


@pytest.mark.asyncio
async def test_restore_waits_for_retention_and_reports_missing_task(pg_engine) -> None:
    """T9：清理先持锁时，恢复只能等待；清理提交后恢复报任务不存在，不能报成功。"""
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    (task_id,) = await _seed(
        factory,
        [_archived(archived_at=datetime.now(UTC) - timedelta(days=200))],
    )

    async with factory() as cleaner, factory() as admin:
        assert await SQLAlchemyRetentionRepository(cleaner).purge_archived_tasks() == 1
        restore = asyncio.create_task(
            SQLAlchemyOperationsRepository(admin).restore_task(task_id, 1)
        )
        assert await _is_blocked(restore)
        await cleaner.commit()
        with pytest.raises(LookupError):
            await restore
        await admin.rollback()

    async with factory() as session:
        assert await session.get(BusinessTask, task_id) is None


@pytest.mark.asyncio
async def test_bulk_purge_refused_after_restore_even_with_stale_session(pg_engine) -> None:
    """T12：恢复先提交，则批量删除整批拒绝；会话里预加载的旧归档状态不算数。"""
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    first, second = await _seed(
        factory,
        [
            _archived(archived_at=datetime(2026, 8, 5, tzinfo=UTC)),
            _archived(archived_at=datetime(2026, 8, 5, tzinfo=UTC)),
        ],
    )

    async with factory() as purger, factory() as admin:
        loaded = await purger.get(BusinessTask, first)
        assert loaded is not None and loaded.archived_at is not None
        await SQLAlchemyOperationsRepository(admin).restore_task(first, 1)
        await admin.commit()
        with pytest.raises(OperationRefused, match="只有已归档的任务可以永久删除"):
            await SQLAlchemyOperationsRepository(purger).purge_selected([first, second], 1)
        await purger.rollback()

    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(BusinessTask)) == 2


@pytest.mark.asyncio
async def test_bulk_purge_waits_for_restore_lock_then_refuses(pg_engine) -> None:
    """T12：恢复先持锁未提交时，批量删除等待锁；恢复提交后整批拒绝。"""
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    first, second = await _seed(
        factory,
        [
            _archived(archived_at=datetime(2026, 8, 5, tzinfo=UTC)),
            _archived(archived_at=datetime(2026, 8, 5, tzinfo=UTC)),
        ],
    )

    async with factory() as admin, factory() as purger:
        await SQLAlchemyOperationsRepository(admin).restore_task(first, 1)
        purge = asyncio.create_task(
            SQLAlchemyOperationsRepository(purger).purge_selected([first, second], 1)
        )
        assert await _is_blocked(purge)
        await admin.commit()
        with pytest.raises(OperationRefused):
            await purge
        await purger.rollback()

    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(BusinessTask)) == 2


@pytest.mark.asyncio
async def test_restore_waits_for_bulk_purge_then_reports_missing_task(pg_engine) -> None:
    """T12：批量删除先锁住时，恢复等待；删除提交后恢复报任务不存在。"""
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    first, second = await _seed(
        factory,
        [
            _archived(
                archived_at=datetime(2026, 8, 5, tzinfo=UTC),
                dedupe_key="turnover:101:2026-08-01",
            ),
            _archived(archived_at=datetime(2026, 8, 5, tzinfo=UTC)),
        ],
    )

    async with factory() as purger, factory() as admin:
        assert await SQLAlchemyOperationsRepository(purger).purge_selected(
            [second, first],
            1,
        ) == 2
        restore = asyncio.create_task(
            SQLAlchemyOperationsRepository(admin).restore_task(first, 1)
        )
        assert await _is_blocked(restore)
        await purger.commit()
        with pytest.raises(LookupError):
            await restore
        await admin.rollback()

    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(BusinessTask)) == 0
        assert list(await session.scalars(select(PurgedTaskMark.dedupe_key))) == [
            "turnover:101:2026-08-01"
        ]


@pytest.mark.asyncio
async def test_concurrent_tombstone_upserts_keep_one_row_and_latest_time(pg_engine) -> None:
    """T17：同键墓碑并发 upsert 只留一行，后提交的旧时间不会把新时间覆盖回去。"""
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    key = "turnover:101:2026-08-01"
    later = datetime(2026, 9, 10, tzinfo=UTC)
    earlier = datetime(2026, 9, 1, tzinfo=UTC)

    async with factory() as first, factory() as second:
        await SQLAlchemyOperationsRepository(first).mark_purged_many([key], now=later)
        waiting = asyncio.create_task(
            SQLAlchemyOperationsRepository(second).mark_purged_many([key], now=earlier)
        )
        assert await _is_blocked(waiting)
        await first.commit()
        await waiting
        await second.commit()

    async with factory() as session:
        marks = list(await session.scalars(select(PurgedTaskMark)))
        assert len(marks) == 1
        assert marks[0].purged_at == later


@pytest.mark.asyncio
async def test_expiry_cleanup_does_not_delete_a_concurrently_refreshed_mark(
    pg_engine,
) -> None:
    """T17：过期墓碑正被另一事务刷新时，同步读墓碑不能把刷新后的有效墓碑删掉。"""
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    key = "turnover:101:2026-08-01"
    await _seed(factory, [])
    async with factory() as session:
        session.add(
            PurgedTaskMark(
                dedupe_key=key,
                purged_at=datetime.now(UTC)
                - timedelta(days=PURGED_MARK_RETENTION_DAYS + 10),
            )
        )
        await session.commit()

    refreshed_at = datetime.now(UTC)
    async with factory() as refresher, factory() as syncer:
        await SQLAlchemyOperationsRepository(refresher).mark_purged_many(
            [key],
            now=refreshed_at,
        )
        sync = asyncio.create_task(
            SQLAlchemyOperationsRepository(syncer).create_turnover(
                property_id=101,
                service_date=date(2026, 8, 1),
            )
        )
        assert await _is_blocked(sync)
        await refresher.commit()
        assert await sync is None
        await syncer.commit()

    async with factory() as session:
        mark = await session.scalar(select(PurgedTaskMark))
        assert mark is not None and mark.purged_at == refreshed_at
        assert await session.scalar(select(func.count()).select_from(BusinessTask)) == 0


@pytest.mark.asyncio
async def test_failed_batch_keeps_earlier_committed_batches(pg_engine, monkeypatch) -> None:
    """T6/T17：第二批写墓碑失败只回滚该批，第一批的删除与墓碑已提交保留。"""
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    old = datetime.now(UTC) - timedelta(days=200)
    ids = await _seed(
        factory,
        [
            _archived(archived_at=old, dedupe_key=f"turnover:101:2025-01-0{index}")
            for index in range(1, 5)
        ],
    )
    original = SQLAlchemyOperationsRepository.mark_purged_many
    calls = 0

    async def fail_second_batch(self, dedupe_keys, *, now):
        """第二次写墓碑时模拟数据库错误。"""
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("tombstone write failed")
        await original(self, dedupe_keys, now=now)

    monkeypatch.setattr(
        SQLAlchemyOperationsRepository,
        "mark_purged_many",
        fail_second_batch,
    )
    async with factory() as session:
        assert await SQLAlchemyRetentionRepository(session).purge_archived_tasks(
            batch_size=2
        ) == 2
        await session.commit()
    async with factory() as session:
        with pytest.raises(RuntimeError, match="tombstone write failed"):
            await SQLAlchemyRetentionRepository(session).purge_archived_tasks(batch_size=2)
        await session.rollback()

    async with factory() as session:
        remaining = sorted(await session.scalars(select(BusinessTask.id)))
        assert remaining == ids[2:]
        assert sorted(await session.scalars(select(PurgedTaskMark.dedupe_key))) == [
            "turnover:101:2025-01-01",
            "turnover:101:2025-01-02",
        ]
