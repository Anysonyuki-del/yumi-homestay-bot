"""投递失败看板的链归并与阶段判定。"""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, text

from homestay_bot.repositories.admin_diagnostics import _roll_up_delivery_chains

BASE = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _rows(*specs: dict[str, object]) -> list[object]:
    """用真实 Row 对象喂归并函数，避免测试替身与实际列名脱节。

    走 Core 的带类型列，让 sent_at 按 DateTime 还原，与仓储里真实查询一致。
    """
    engine = create_engine("sqlite://")
    probe = sa.table(
        "probe",
        sa.column("id", sa.Integer),
        sa.column("sent_at", sa.DateTime(timezone=True)),
        sa.column("status", sa.String),
        sa.column("error_code", sa.String),
        sa.column("retry_of", sa.String),
        sa.column("pending", sa.Boolean),
        sa.column("notified", sa.Boolean),
    )
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE probe (id INTEGER, sent_at TIMESTAMP, status TEXT,"
            " error_code TEXT, retry_of TEXT, pending BOOLEAN, notified BOOLEAN)"
        ))
        for spec in specs:
            connection.execute(
                probe.insert().values(
                    **{column.name: spec.get(column.name) for column in probe.columns}
                )
            )
        return list(
            connection.execute(probe.select().order_by(probe.c.id.desc())).all()
        )


def _failed(message_id: int, **extra: object) -> dict[str, object]:
    return {
        "id": message_id,
        "sent_at": BASE + timedelta(minutes=message_id),
        "status": "failed",
        "error_code": "wecom_async_13",
        **extra,
    }


def test_production_shape_counts_chains_not_rows() -> None:
    """生产四条链的真实形态：一次失败与它的重发是同一条链，不能数成两次。

    消息 52 与它的改写 53 都是 failed。按消息行统计会报「5 次未送达」，实际是
    4 次，且其中 3 次的重发已被受理。看板要说清的正是这个区别。
    """
    rollup = _roll_up_delivery_chains(
        _rows(
            _failed(49, notified=1),
            {"id": 50, "sent_at": BASE + timedelta(minutes=50),
             "status": "accepted", "retry_of": "49"},
            _failed(52),
            _failed(53, retry_of="52", notified=1),
            _failed(55),
            {"id": 56, "sent_at": BASE + timedelta(minutes=56),
             "status": "accepted", "retry_of": "55"},
            _failed(105),
            {"id": 106, "sent_at": BASE + timedelta(minutes=106),
             "status": "accepted", "retry_of": "105"},
        ),
        truncated=False,
    )

    assert rollup.total == 4, "五条失败消息只对应四次未送达"
    assert (rollup.resent, rollup.notified) == (3, 1)
    assert (rollup.retrying, rollup.unattended) == (0, 0)
    assert {chain.root_id for chain in rollup.chains} == {49, 52, 55, 105}
    chain_52 = next(c for c in rollup.chains if c.root_id == 52)
    assert chain_52.stage == "notified", "改写失败但已叫人，不是无人知晓"
    assert chain_52.attempts == 2


def test_a_chain_nobody_was_told_about_is_the_only_alarm() -> None:
    """既无在途重试、又无受理重发、也从未通知：这一档才要人立刻接手。"""
    rollup = _roll_up_delivery_chains(_rows(_failed(70)), truncated=False)

    assert rollup.unattended == 1
    assert rollup.chains[0].stage == "unattended"


def test_a_retry_still_in_flight_is_not_reported_as_unattended() -> None:
    """重试在途时系统仍在处理，报成「无人知晓」会造成假警报。"""
    rollup = _roll_up_delivery_chains(_rows(_failed(80, pending=1)), truncated=False)

    assert (rollup.retrying, rollup.unattended) == (1, 0)


@pytest.mark.parametrize("retry_of", ["", None, "abc", "999"])
def test_a_broken_retry_link_never_merges_unrelated_chains(retry_of: object) -> None:
    """retry_of 缺失、非法或指向窗口外时，各自成链，不得并进别人的链。"""
    rollup = _roll_up_delivery_chains(
        _rows(_failed(90), _failed(91, retry_of=retry_of)), truncated=False
    )

    assert rollup.total == 2


def test_an_accepted_resend_without_its_failure_is_not_counted() -> None:
    """只有重发落在窗口里、原始失败在窗口外时不臆测，不计入失败数。"""
    rollup = _roll_up_delivery_chains(
        _rows({"id": 60, "sent_at": BASE, "status": "accepted",
               "retry_of": "59"}),
        truncated=False,
    )

    assert rollup.total == 0


@pytest.mark.asyncio
async def test_the_query_runs_against_a_real_database_and_reads_json() -> None:
    """整条查询要在真实数据库上跑通：JSON 取值两个方言共用一份代码。

    分类逻辑的单测喂的是构造好的行，证明不了 `metadata->>` / `json_extract`
    这层。这里从真实表读一遍，顺带确认只读 BOT 消息、正文不进投影。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from homestay_bot.domain.enums import MessageOrigin
    from homestay_bot.domain.models import Base, Conversation, Message
    from homestay_bot.repositories.admin_diagnostics import (
        SQLAlchemyAdminDiagnosticsRepository,
    )

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with async_sessionmaker(engine)() as session:
        session.add(Conversation(open_kfid="wk-1", external_userid="wm-1"))
        await session.flush()
        session.add_all([
            Message(
                conversation_id=1, external_message_id="m-52", origin=MessageOrigin.BOT,
                message_type="text", content="客人不该出现在看板上的正文",
                sent_at=BASE, message_metadata={
                    "delivery_status": "failed", "delivery_error_code": "wecom_async_13",
                },
            ),
            Message(
                conversation_id=1, external_message_id="m-53", origin=MessageOrigin.BOT,
                message_type="text", content="改写后的回复",
                sent_at=BASE + timedelta(minutes=1), message_metadata={
                    "delivery_status": "failed", "retry_of_message_id": "1",
                    "delivery_failure_notified": True,
                    "delivery_error_code": "wecom_async_13",
                },
            ),
            Message(
                conversation_id=1, external_message_id="m-guest", origin=MessageOrigin.GUEST,
                message_type="text", content="客人提问",
                sent_at=BASE, message_metadata={"delivery_status": "failed"},
            ),
        ])
        await session.flush()

        rollup = await SQLAlchemyAdminDiagnosticsRepository(
            session
        ).delivery_failure_rollup(limit=100)

    await engine.dispose()

    assert rollup.total == 1, "两条机器人消息属同一条链；客人消息不参与投递统计"
    assert rollup.notified == 1
    chain = rollup.chains[0]
    assert (chain.root_id, chain.attempts) == (1, 2)
    assert chain.error_codes == ("wecom_async_13",)


@pytest.mark.asyncio
async def test_the_limit_marks_the_result_as_truncated() -> None:
    """取满上限时必须标记截断，页面据此说明不是全量，而不是假装是。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from homestay_bot.domain.enums import MessageOrigin
    from homestay_bot.domain.models import Base, Conversation, Message
    from homestay_bot.repositories.admin_diagnostics import (
        SQLAlchemyAdminDiagnosticsRepository,
    )

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with async_sessionmaker(engine)() as session:
        session.add(Conversation(open_kfid="wk-1", external_userid="wm-1"))
        await session.flush()
        session.add_all([
            Message(
                conversation_id=1, external_message_id=f"m-{index}",
                origin=MessageOrigin.BOT, message_type="text", content="x",
                sent_at=BASE + timedelta(minutes=index),
                message_metadata={"delivery_status": "failed"},
            )
            for index in range(5)
        ])
        await session.flush()

        repository = SQLAlchemyAdminDiagnosticsRepository(session)
        capped = await repository.delivery_failure_rollup(limit=3)
        whole = await repository.delivery_failure_rollup(limit=100)

    await engine.dispose()

    assert (capped.total, capped.truncated) == (3, True)
    assert (whole.total, whole.truncated) == (5, False)
