from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import (
    ApprovalStatus,
    BusinessTaskStatus,
    BusinessTaskType,
    JobStatus,
)
from homestay_bot.domain.models import (
    AuditLog,
    Base,
    BookingApproval,
    BusinessTask,
    Conversation,
    ExternalRequest,
    HostexWebhookEvent,
    Job,
    PropertyProfile,
    PurgedTaskMark,
    TaskAttachment,
)
from homestay_bot.repositories.jobs import SQLAlchemyJobRepository
from homestay_bot.repositories.retention import SQLAlchemyRetentionRepository
from homestay_bot.services.private_file_storage import PrivateFileStorage
from homestay_bot.services.task_page_service import (
    ATTACHMENT_CLEANUP_JOB_TYPE,
    build_attachment_cleanup_dedupe_key,
)


@pytest.mark.asyncio
async def test_retention_purges_only_expired_terminal_records() -> None:
    """清理应删除过期终态记录，但保留待处理任务和近期审计。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 3, tzinfo=UTC)

    async with factory() as session:
        old = now - timedelta(days=400)
        session.add_all(
            [
                Job(
                    job_type="wecom_sync",
                    payload={},
                    status=JobStatus.COMPLETED,
                    attempts=1,
                    available_at=old,
                    created_at=old,
                    updated_at=old,
                ),
                Job(
                    job_type="wecom_sync",
                    payload={"safe": "pending"},
                    status=JobStatus.PENDING,
                    attempts=0,
                    available_at=old,
                    created_at=old,
                    updated_at=old,
                ),
                ExternalRequest(
                    provider="hostex",
                    method="GET",
                    path="/reservations",
                    succeeded=True,
                    created_at=old,
                ),
                HostexWebhookEvent(
                    event_key="old-event",
                    event_type="reservation.updated",
                    payload={},
                    status="completed",
                    attempts=1,
                    created_at=old,
                    updated_at=old,
                ),
                AuditLog(
                    action="old-action",
                    target_type="job",
                    target_id="1",
                    details={},
                    created_at=old,
                ),
            ]
        )
        await session.commit()

        deleted = await SQLAlchemyRetentionRepository(session).purge(now=now)
        await session.commit()

        assert deleted == {
            "booking_approval_pii": 0,
            "jobs": 1,
            "external_requests": 1,
            "hostex_webhook_events": 1,
            "audit_logs": 1,
        }
        assert await session.get(Job, 2) is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_retention_no_longer_purges_approval_pii() -> None:
    """只清理达到期限的终态审批 PII，开放或近期审批必须完整保留。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 31, 12, tzinfo=UTC)

    async with factory() as session:
        conversation = Conversation(open_kfid="wk-retention", external_userid="wm-retention")
        session.add(conversation)
        await session.flush()

        def approval(
            code: str,
            status: ApprovalStatus,
            *,
            check_out_days_ago: int,
            updated_days_ago: int,
        ) -> BookingApproval:
            """构造带完整密文和指定生命周期时间的审批。"""
            return BookingApproval(
                approval_code=code,
                conversation_id=conversation.id,
                status=status,
                check_in_date=(now - timedelta(days=check_out_days_ago + 1)).date(),
                check_out_date=(now - timedelta(days=check_out_days_ago)).date(),
                number_of_guests=2,
                guest_name_ciphertext=b"encrypted-name",
                guest_mobile_ciphertext=b"encrypted-mobile",
                room_type_preference="江景房",
                special_requests_ciphertext=b"encrypted-request",
                created_at=now - timedelta(days=updated_days_ago),
                updated_at=now - timedelta(days=updated_days_ago),
            )

        records = [
            approval(
                "BOOKED-OLD",
                ApprovalStatus.BOOKED,
                check_out_days_ago=30,
                updated_days_ago=30,
            ),
            approval(
                "BOOKED-NEW",
                ApprovalStatus.BOOKED,
                check_out_days_ago=29,
                updated_days_ago=29,
            ),
            approval(
                "REJECTED-OLD",
                ApprovalStatus.REJECTED,
                check_out_days_ago=100,
                updated_days_ago=90,
            ),
            approval(
                "CONFLICT-OLD",
                ApprovalStatus.CONFLICT,
                check_out_days_ago=100,
                updated_days_ago=90,
            ),
            approval(
                "PENDING-OLD",
                ApprovalStatus.PENDING,
                check_out_days_ago=100,
                updated_days_ago=200,
            ),
            approval(
                "CREATING-OLD",
                ApprovalStatus.CREATING,
                check_out_days_ago=100,
                updated_days_ago=200,
            ),
            approval(
                "REVIEW-OLD",
                ApprovalStatus.NEEDS_REVIEW,
                check_out_days_ago=100,
                updated_days_ago=200,
            ),
        ]
        session.add_all(records)
        await session.commit()

        deleted = await SQLAlchemyRetentionRepository(session).purge(now=now)
        await session.commit()
        for record in records:
            await session.refresh(record)

        # 1.41.0 起审批客人资料不再到期清除（用户决定数据库可长期保存客人信息）；
        # 计数键保留、恒为 0，调度与统计不用改。
        assert deleted["booking_approval_pii"] == 0
        assert all(record.pii_purged_at is None for record in records)
        assert all(record.guest_name_ciphertext == b"encrypted-name" for record in records)
        assert all(record.guest_mobile_ciphertext == b"encrypted-mobile" for record in records)

    await engine.dispose()


@pytest.mark.asyncio
async def test_archived_task_cleanup_respects_every_boundary() -> None:
    """自动清理只碰归档满 180 天的任务，其余一律不动。

    这是本项目第一个会自动销毁业务数据的清理，边界写错就是静默丢数据：
    未归档的任务无论多旧都不删——自动删掉还没人处理的活，比留着它危险得多。
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 9, 8, tzinfo=UTC)

    async with factory() as session:
        await session.execute(text("PRAGMA foreign_keys=ON"))
        session.add(PropertyProfile(id=101, title="测试房间"))
        await session.flush()

        def make(description: str, archived_at: datetime | None) -> BusinessTask:
            """构造一条终态任务并指定归档时间。"""
            return BusinessTask(
                task_type=BusinessTaskType.CLEANING,
                status=BusinessTaskStatus.EXPIRED,
                property_id=101,
                service_date=date(2025, 1, 1),
                description=description,
                archived_at=archived_at,
            )

        old_archived = make("归档满 181 天", now - timedelta(days=181))
        fresh_archived = make("归档仅 179 天", now - timedelta(days=179))
        never_archived = make("从未归档但很旧", None)
        session.add_all([old_archived, fresh_archived, never_archived])
        await session.flush()
        session.add(
            TaskAttachment(
                task_id=old_archived.id,
                private_file_id="0123456789abcdef",
                kind="photo",
            )
        )
        await session.flush()

        deleted = await SQLAlchemyRetentionRepository(session).purge_archived_tasks(
            now=now,
        )
        await session.commit()

        assert deleted == 1
        assert await session.get(BusinessTask, old_archived.id) is None
        # 未满保留期与从未归档的都必须完好
        assert await session.get(BusinessTask, fresh_archived.id) is not None
        assert await session.get(BusinessTask, never_archived.id) is not None
        # 照片清理改为与删库同事务登记、提交之后由 worker 幂等执行。此前是先删
        # 文件再删库，一旦提交失败数据库回滚而照片已经没了，没有备份就无法重建。
        # 这里断言的是「清理有据可查且不会丢」，比原来的「已经删掉了」更强。
        cleanup = await session.scalar(
            select(Job).where(Job.job_type == ATTACHMENT_CLEANUP_JOB_TYPE)
        )
        assert cleanup is not None
        assert cleanup.payload["file_ids"] == ["0123456789abcdef"]
        assert cleanup.status is JobStatus.PENDING


async def _memory_factory() -> tuple[object, async_sessionmaker[AsyncSession]]:
    """创建开启外键的内存 SQLite，保证附件行随任务级联删除。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.execute(text("PRAGMA foreign_keys=ON"))
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _archived_task(
    *,
    archived_at: datetime,
    task_id: int | None = None,
    dedupe_key: str | None = None,
) -> BusinessTask:
    """构造一条已归档的终态保洁任务。"""
    return BusinessTask(
        id=task_id,
        dedupe_key=dedupe_key,
        task_type=BusinessTaskType.CLEANING,
        status=BusinessTaskStatus.COMPLETED,
        property_id=101,
        service_date=date(2025, 1, 1),
        description="历史保洁",
        archived_at=archived_at,
    )


def _photo(storage_root: Path, seed: str) -> str:
    """在私有目录写入一张占位照片，返回存储层认可的文件编号。"""
    file_id = seed * 32 + ".png"
    (storage_root / file_id).write_bytes(b"photo")
    return file_id


async def _run_pending_cleanup(
    factory: async_sessionmaker[AsyncSession],
    storage: PrivateFileStorage,
) -> int:
    """用生产环境的照片清理 handler 执行全部待处理清理 job，返回执行条数。"""
    from homestay_bot.application import _build_attachment_cleanup_handler

    handler = _build_attachment_cleanup_handler(storage)
    async with factory() as session:
        jobs = list(
            await session.scalars(
                select(Job).where(
                    Job.job_type == ATTACHMENT_CLEANUP_JOB_TYPE,
                    Job.status == JobStatus.PENDING,
                )
            )
        )
    for job in jobs:
        await handler(job.payload)
    return len(jobs)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["rollback", "enqueue_error"])
async def test_photo_survives_a_failed_cleanup_transaction(
    tmp_path: Path,
    monkeypatch,
    failure: str,
) -> None:
    """删除后回滚或登记清理失败时，任务、附件行和磁盘照片一样不少。

    用真实私有目录与生产 handler 验证：回滚后没有已提交的清理 job，handler
    就无从删除照片；此前的版本只定义了一个空列表，并没有接入调用链。
    """
    engine, factory = await _memory_factory()
    storage_root = tmp_path / "private"
    storage = PrivateFileStorage(storage_root)
    now = datetime(2026, 9, 8, tzinfo=UTC)

    async with factory() as session:
        session.add(PropertyProfile(id=101, title="测试房间"))
        await session.flush()
        task = _archived_task(
            archived_at=now - timedelta(days=400),
            dedupe_key="turnover:101:2025-01-01",
        )
        session.add(task)
        await session.flush()
        file_id = _photo(storage_root, "a")
        session.add(TaskAttachment(task_id=task.id, private_file_id=file_id, kind="photo"))
        await session.commit()

    if failure == "enqueue_error":

        async def broken_enqueue(self, *args, **kwargs):
            """模拟登记清理任务时数据库报错。"""
            raise RuntimeError("enqueue failed")

        monkeypatch.setattr(SQLAlchemyJobRepository, "enqueue", broken_enqueue)

    async with factory() as session:
        if failure == "rollback":
            assert await SQLAlchemyRetentionRepository(session).purge_archived_tasks(
                now=now
            ) == 1
            await session.rollback()
        else:
            with pytest.raises(RuntimeError, match="enqueue failed"):
                await SQLAlchemyRetentionRepository(session).purge_archived_tasks(
                    now=now
                )
            await session.rollback()

    assert await _run_pending_cleanup(factory, storage) == 0
    assert (storage_root / file_id).is_file()
    async with factory() as session:
        assert await session.get(BusinessTask, task.id) is not None
        assert await session.scalar(
            select(func.count()).select_from(TaskAttachment)
        ) == 1
        assert await session.scalar(select(func.count()).select_from(Job)) == 0
        assert await session.scalar(
            select(func.count()).select_from(PurgedTaskMark)
        ) == 0

    await engine.dispose()


@pytest.mark.asyncio
async def test_committed_cleanup_deletes_photo_once_and_replays_safely(
    tmp_path: Path,
) -> None:
    """提交之后才由 worker 删除照片；同一载荷重复执行保持幂等。

    旧格式键的已提交 job 也只依赖载荷里的文件编号，同一个 handler 照常处理。
    """
    engine, factory = await _memory_factory()
    storage_root = tmp_path / "private"
    storage = PrivateFileStorage(storage_root)
    now = datetime(2026, 9, 8, tzinfo=UTC)

    async with factory() as session:
        session.add(PropertyProfile(id=101, title="测试房间"))
        await session.flush()
        task = _archived_task(archived_at=now - timedelta(days=400))
        session.add(task)
        await session.flush()
        new_photo = _photo(storage_root, "a")
        session.add(TaskAttachment(task_id=task.id, private_file_id=new_photo, kind="photo"))
        legacy_photo = _photo(storage_root, "b")
        # 升级前已提交的旧格式清理 job
        await SQLAlchemyJobRepository(session).enqueue(
            ATTACHMENT_CLEANUP_JOB_TYPE,
            {"file_ids": [legacy_photo]},
            dedupe_key="task-retention:7,8,9",
        )
        await session.commit()

    async with factory() as session:
        assert await SQLAlchemyRetentionRepository(session).purge_archived_tasks(
            now=now
        ) == 1
        await session.commit()

    assert await _run_pending_cleanup(factory, storage) == 2
    assert not (storage_root / new_photo).exists()
    assert not (storage_root / legacy_photo).exists()
    # 重放：文件已不存在即视为完成，不报错
    assert await _run_pending_cleanup(factory, storage) == 2

    await engine.dispose()


@pytest.mark.asyncio
async def test_archived_cleanup_runs_in_bounded_batches(tmp_path: Path) -> None:
    """超过两批的到期任务逐批处理：每批不超上限，各自登记一条清理 job。

    清理键只由本批实际删除的编号决定；照片既不漏登记也不重复登记。系统任务
    按原业务去重键留下墓碑，时间取本轮 now。
    """
    engine, factory = await _memory_factory()
    storage_root = tmp_path / "private"
    storage_root.mkdir()
    now = datetime.now(UTC)
    photos: dict[int, str] = {}

    async with factory() as session:
        session.add(PropertyProfile(id=101, title="测试房间"))
        await session.flush()
        for index in range(5):
            task = _archived_task(
                archived_at=now - timedelta(days=200),
                dedupe_key=f"turnover:101:2025-01-0{index + 1}" if index < 2 else None,
            )
            session.add(task)
            await session.flush()
            photos[task.id] = _photo(storage_root, "abcde"[index])
            session.add(
                TaskAttachment(task_id=task.id, private_file_id=photos[task.id], kind="photo")
            )
        # 未到期与从未归档的任务不属于任何一批
        session.add(_archived_task(archived_at=now - timedelta(days=10)))
        await session.commit()

    batches: list[int] = []
    while True:
        async with factory() as session:
            deleted = await SQLAlchemyRetentionRepository(session).purge_archived_tasks(
                now=now,
                batch_size=2,
            )
            await session.commit()
        batches.append(deleted)
        if deleted < 2:
            break

    assert batches == [2, 2, 1]
    ordered_ids = sorted(photos)
    expected_batches = [ordered_ids[0:2], ordered_ids[2:4], ordered_ids[4:5]]
    async with factory() as session:
        jobs = list(
            await session.scalars(
                select(Job)
                .where(Job.job_type == ATTACHMENT_CLEANUP_JOB_TYPE)
                .order_by(Job.id)
            )
        )
        assert [job.dedupe_key for job in jobs] == [
            build_attachment_cleanup_dedupe_key(ids, source="retention")
            for ids in expected_batches
        ]
        assert [job.payload["file_ids"] for job in jobs] == [
            [photos[task_id] for task_id in ids] for ids in expected_batches
        ]
        assert await session.scalar(
            select(func.count()).select_from(BusinessTask)
        ) == 1
        marks = list(await session.scalars(select(PurgedTaskMark)))
        assert sorted(mark.dedupe_key for mark in marks) == [
            "turnover:101:2025-01-01",
            "turnover:101:2025-01-02",
        ]
        for mark in marks:
            # 墓碑时间是本轮删除时间，不是 200 天前的归档时间
            assert mark.purged_at.replace(tzinfo=UTC) == now

    await engine.dispose()


@pytest.mark.asyncio
async def test_forty_five_digit_task_ids_fit_one_cleanup_job() -> None:
    """40 个五位数编号在旧格式下是 254 字符；新键在列宽内，一条 job 登记全部照片。"""
    engine, factory = await _memory_factory()
    now = datetime(2026, 9, 8, tzinfo=UTC)
    task_ids = list(range(10_000, 10_040))

    async with factory() as session:
        session.add(PropertyProfile(id=101, title="测试房间"))
        await session.flush()
        for task_id in task_ids:
            session.add(
                _archived_task(task_id=task_id, archived_at=now - timedelta(days=200))
            )
        await session.flush()
        for task_id in task_ids:
            session.add(
                TaskAttachment(
                    task_id=task_id,
                    private_file_id=f"{task_id:032x}.png",
                    kind="photo",
                )
            )
        await session.commit()

    async with factory() as session:
        assert await SQLAlchemyRetentionRepository(session).purge_archived_tasks(
            now=now
        ) == 40
        await session.commit()
        job = await session.scalar(select(Job))
        assert job is not None
        assert job.dedupe_key is not None
        assert len(job.dedupe_key) <= Job.__table__.c.dedupe_key.type.length
        assert len(job.payload["file_ids"]) == 40

    await engine.dispose()


@pytest.mark.asyncio
async def test_purge_processes_bounded_batches_in_primary_key_order() -> None:
    """通用清理每类每批至多 batch_size 条，按主键顺序推进，不碰待处理记录。"""
    engine, factory = await _memory_factory()
    now = datetime(2026, 9, 8, tzinfo=UTC)
    old = now - timedelta(days=400)

    async with factory() as session:
        pending = Job(
            job_type="wecom_sync",
            payload={},
            status=JobStatus.PENDING,
            attempts=0,
            available_at=old,
            created_at=old,
            updated_at=old,
        )
        session.add(pending)
        finished = [
            Job(
                job_type="wecom_sync",
                payload={},
                status=JobStatus.COMPLETED,
                attempts=1,
                available_at=old,
                created_at=old,
                updated_at=old,
            )
            for _ in range(5)
        ]
        session.add_all(finished)
        await session.commit()
    finished_ids = [job.id for job in finished]

    rounds: list[int] = []
    remaining: list[list[int]] = []
    for _ in range(4):
        async with factory() as session:
            counts = await SQLAlchemyRetentionRepository(session).purge(
                now=now,
                batch_size=2,
            )
            await session.commit()
            rounds.append(counts["jobs"])
            remaining.append(
                sorted(
                    await session.scalars(
                        select(Job.id).where(Job.status == JobStatus.COMPLETED)
                    )
                )
            )

    assert rounds == [2, 2, 1, 0]
    assert remaining[0] == finished_ids[2:]
    assert remaining[1] == finished_ids[4:]
    async with factory() as session:
        assert await session.get(Job, pending.id) is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_retention_cutoffs_keep_their_original_comparisons() -> None:
    """`<` 类清理在恰好等于 cutoff 时保留、早一秒才删除；状态保护不变。"""
    engine, factory = await _memory_factory()
    now = datetime(2026, 9, 8, 12, tzinfo=UTC)
    repository_class = SQLAlchemyRetentionRepository

    def at(days: int, *, earlier: bool) -> datetime:
        """返回恰好 cutoff 或再早一秒的时刻。"""
        moment = now - timedelta(days=days)
        return moment - timedelta(seconds=1) if earlier else moment

    async with factory() as session:
        session.add(PropertyProfile(id=101, title="测试房间"))
        await session.flush()
        for earlier in (False, True):
            job_time = at(repository_class.JOB_RETENTION_DAYS, earlier=earlier)
            session.add(
                Job(
                    job_type=f"edge-{earlier}",
                    payload={},
                    status=JobStatus.FAILED,
                    attempts=1,
                    available_at=job_time,
                    created_at=job_time,
                    updated_at=job_time,
                )
            )
            session.add(
                ExternalRequest(
                    provider="hostex",
                    method="GET",
                    path=f"/edge-{earlier}",
                    succeeded=True,
                    created_at=at(
                        repository_class.EXTERNAL_REQUEST_RETENTION_DAYS,
                        earlier=earlier,
                    ),
                )
            )
            webhook_time = at(repository_class.WEBHOOK_RETENTION_DAYS, earlier=earlier)
            session.add(
                HostexWebhookEvent(
                    event_key=f"edge-{earlier}",
                    event_type="reservation.updated",
                    payload={},
                    status="completed",
                    attempts=1,
                    created_at=webhook_time,
                    updated_at=webhook_time,
                )
            )
            session.add(
                AuditLog(
                    action=f"edge-{earlier}",
                    target_type="job",
                    target_id="1",
                    details={},
                    created_at=at(repository_class.AUDIT_RETENTION_DAYS, earlier=earlier),
                )
            )
            session.add(
                _archived_task(
                    archived_at=at(
                        repository_class.ARCHIVED_TASK_RETENTION_DAYS,
                        earlier=earlier,
                    )
                )
            )
        ancient = now - timedelta(days=1000)
        # 再旧也不删：进行中的 job、待处理的 webhook、从未归档的任务
        session.add(
            Job(
                job_type="running",
                payload={},
                status=JobStatus.RUNNING,
                attempts=1,
                available_at=ancient,
                created_at=ancient,
                updated_at=ancient,
            )
        )
        session.add(
            HostexWebhookEvent(
                event_key="still-pending",
                event_type="reservation.updated",
                payload={},
                status="pending",
                attempts=0,
                created_at=ancient,
                updated_at=ancient,
            )
        )
        never_archived = BusinessTask(
            task_type=BusinessTaskType.CLEANING,
            status=BusinessTaskStatus.EXPIRED,
            property_id=101,
            service_date=date(2020, 1, 1),
            description="从未归档",
        )
        session.add(never_archived)
        await session.commit()

    async with factory() as session:
        repository = SQLAlchemyRetentionRepository(session)
        counts = await repository.purge(now=now)
        archived = await repository.purge_archived_tasks(now=now)
        await session.commit()

    assert counts == {
        "booking_approval_pii": 0,
        "jobs": 1,
        "external_requests": 1,
        "hostex_webhook_events": 1,
        "audit_logs": 1,
    }
    assert archived == 1
    async with factory() as session:
        assert sorted(await session.scalars(select(Job.job_type))) == [
            "edge-False",
            "running",
        ]
        assert list(await session.scalars(select(ExternalRequest.path))) == [
            "/edge-False"
        ]
        assert sorted(await session.scalars(select(HostexWebhookEvent.event_key))) == [
            "edge-False",
            "still-pending",
        ]
        assert list(await session.scalars(select(AuditLog.action))) == ["edge-False"]
        assert await session.scalar(
            select(func.count()).select_from(BusinessTask)
        ) == 2
        assert await session.get(BusinessTask, never_archived.id) is not None

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("batch_size", [0, -1, True])
async def test_retention_rejects_non_positive_batch_size(batch_size) -> None:
    """零、负数或布尔批量一律拒绝，不能意外变成无上限删除。"""
    engine, factory = await _memory_factory()

    async with factory() as session:
        repository = SQLAlchemyRetentionRepository(session)
        with pytest.raises(ValueError):
            await repository.purge(batch_size=batch_size)
        with pytest.raises(ValueError):
            await repository.purge_archived_tasks(batch_size=batch_size)

    await engine.dispose()


@pytest.mark.asyncio
async def test_purge_rechecks_eligibility_on_stale_candidate_ids(monkeypatch) -> None:
    """候选编号是快照：修改语句必须再带原资格条件，不能只凭编号删除。

    这里让候选查询把所有行都当作候选，模拟选出编号后状态已经变化的行；
    仍在处理中的 job、近期记录与待处理 webhook 必须原样保留。
    """
    engine, factory = await _memory_factory()
    now = datetime(2026, 9, 8, tzinfo=UTC)
    old = now - timedelta(days=400)

    async def every_row(self, model, eligible, batch_size):
        """返回该表全部编号，模拟过期快照。"""
        return list(await self._session.scalars(select(model.id)))

    async with factory() as session:
        session.add_all(
            [
                Job(
                    job_type="running",
                    payload={},
                    status=JobStatus.RUNNING,
                    attempts=1,
                    available_at=old,
                    created_at=old,
                    updated_at=old,
                ),
                ExternalRequest(
                    provider="hostex",
                    method="GET",
                    path="/recent",
                    succeeded=True,
                    created_at=now,
                ),
                HostexWebhookEvent(
                    event_key="pending",
                    event_type="reservation.updated",
                    payload={},
                    status="pending",
                    attempts=0,
                    created_at=old,
                    updated_at=old,
                ),
                AuditLog(
                    action="recent",
                    target_type="job",
                    target_id="1",
                    details={},
                    created_at=now,
                ),
            ]
        )
        await session.commit()

    monkeypatch.setattr(SQLAlchemyRetentionRepository, "_candidate_ids", every_row)
    async with factory() as session:
        counts = await SQLAlchemyRetentionRepository(session).purge(now=now)
        await session.commit()

    assert counts == {
        "booking_approval_pii": 0,
        "jobs": 0,
        "external_requests": 0,
        "hostex_webhook_events": 0,
        "audit_logs": 0,
    }

    await engine.dispose()
