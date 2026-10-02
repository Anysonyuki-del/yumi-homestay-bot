import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from homestay_bot.db import create_engine, create_session_factory
from homestay_bot.domain.models import Job
from homestay_bot.repositories.jobs import SQLAlchemyJobRepository


@pytest.mark.asyncio
async def test_sqlite_engine_waits_for_concurrent_writer() -> None:
    """本地 SQLite 应等待短暂写锁，避免立即让后台 worker 退出。"""
    engine = create_engine("sqlite+aiosqlite:///test.db")
    try:
        _, connect_options = engine.sync_engine.dialect.create_connect_args(engine.url)
    finally:
        await engine.dispose()

    assert connect_options["timeout"] == 30.0


@pytest.mark.asyncio
async def test_sqlite_engine_enables_foreign_key_constraints() -> None:
    """本地 SQLite 必须启用外键约束，避免测试环境掩盖生产数据错误。"""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.connect() as connection:
            enabled = await connection.scalar(text("PRAGMA foreign_keys"))
    finally:
        await engine.dispose()

    assert enabled == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("before", ["none", "read", "write"])
@pytest.mark.parametrize("rollback", [True, False])
async def test_sqlite_savepoints_belong_to_outer_transaction(before, rollback) -> None:
    """保存点撞键仅撤销内层；成功保存点也必须随外层提交或回滚。"""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE probe (id INTEGER PRIMARY KEY)"))
        async with engine.connect() as connection:
            if before == "read":
                await connection.execute(text("SELECT count(*) FROM probe"))
            elif before == "write":
                await connection.execute(text("INSERT INTO probe VALUES (1)"))
            async with connection.begin_nested():
                await connection.execute(text("INSERT INTO probe VALUES (2)"))
            with pytest.raises(IntegrityError):
                async with connection.begin_nested():
                    await connection.execute(text("INSERT INTO probe VALUES (2)"))
            await connection.execute(text("INSERT INTO probe VALUES (3)"))
            if rollback:
                await connection.rollback()
            else:
                await connection.commit()
        async with engine.connect() as connection:
            rows = list(await connection.scalars(text("SELECT id FROM probe ORDER BY id")))
        assert rows == ([] if rollback else ([1, 2, 3] if before == "write" else [2, 3]))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("rollback", [True, False])
async def test_job_savepoint_collision_keeps_outer_transaction(monkeypatch, rollback) -> None:
    """真实仓储的唯一键冲突不撤销外层，释放过的保存点也不能提前提交。"""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    factory = create_session_factory(engine)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Job.__table__.create)
        async with factory() as session:
            repository = SQLAlchemyJobRepository(session)
            first = await repository.enqueue("synthetic", {}, dedupe_key="one")
            scalar = session.scalar
            missed = False

            async def miss_preflight_once(*args, **kwargs):
                """模拟查重时尚未看见竞争者；随后真正撞数据库唯一键并读取既有行。"""
                nonlocal missed
                if not missed:
                    missed = True
                    return None
                return await scalar(*args, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(session, "scalar", miss_preflight_once)
                duplicate = await repository.enqueue("synthetic", {}, dedupe_key="one")
            assert duplicate.id == first.id
            await repository.enqueue("synthetic", {}, dedupe_key="two")
            if rollback:
                await session.rollback()
            else:
                await session.commit()
        async with factory() as session:
            keys = list(await session.scalars(select(Job.dedupe_key).order_by(Job.id)))
        assert keys == ([] if rollback else ["one", "two"])
    finally:
        await engine.dispose()
