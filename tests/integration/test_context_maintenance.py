"""客户上下文维护循环在真实 SQLite 上的事务边界回归。

单元测试 test_application.py::test_context_maintenance_isolates_customer_failures 用替身证明
「首个客户失败后第二个仍继续」，但验证不了数据库最后留下了什么。这里用真实会话工厂和
真实仓储跑一轮 _run_context_maintenance_loop，只把摘要步骤换成合成步骤，
按 Codex 本地清理复核（docs/reviews/2026-11-19_codex-to-claude-local-cleanup-review.md）
给出的验收条件核对落库结果。
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot import application
from homestay_bot.domain.enums import MessageOrigin
from homestay_bot.domain.models import (
    Base,
    Conversation,
    Customer,
    CustomerContextSummary,
    Message,
)
from homestay_bot.repositories.context import SQLAlchemyContextRepository


class StopContextMaintenance(RuntimeError):
    """维护循环跑完一轮、进入等待时抛出，用来结束无限循环。"""


class SessionExposingRepository(SQLAlchemyContextRepository):
    """真实仓储，额外公开所绑定的会话，让合成维护步骤写进同一事务。"""

    def __init__(self, session) -> None:
        """记录会话后交给真实仓储初始化。"""
        super().__init__(session)
        self.session = session


class SyntheticRetentionService:
    """仿照 maintain_customer 的事务形态：先写短摘要，提交后再写长摘要。

    客户 1 在长摘要写入并 flush 之后失败，模拟提交后的第二次模型调用或保存出错。
    """

    def __init__(self, repository, summarizer, *, before_external=None) -> None:
        """接收循环为当前客户创建的仓储与提交边界。"""
        self._session = repository.session
        self._before_external = before_external

    async def maintain_customer(self, customer_id: int, now: datetime) -> None:
        """写入 A（或 C），经 before_external 提交；客户 1 再写入 B 后抛错。"""
        label = "A" if customer_id == 1 else "C"
        summary = CustomerContextSummary(customer_id=customer_id, short_summary=label)
        self._session.add(summary)
        await self._session.flush()
        if customer_id != 1:
            return
        # 与真实服务一致：调用外部模型前先提交已落定的写入。
        await self._before_external()
        summary.long_summary = "B"
        await self._session.flush()
        raise RuntimeError("synthetic long summary failure")


async def _customer_with_message(session, index: int) -> int:
    """建一个带一条客人消息的正式客户，让维护循环能发现它。"""
    customer = Customer(display_name=f"维护客户{index}")
    session.add(customer)
    await session.flush()
    conversation = Conversation(
        customer_id=customer.id,
        open_kfid=f"kfid-maintenance-{index}",
        external_userid=f"user-maintenance-{index}",
    )
    session.add(conversation)
    await session.flush()
    session.add(
        Message(
            conversation_id=conversation.id,
            external_message_id=f"maintenance-{index}",
            origin=MessageOrigin.GUEST,
            message_type="text",
            content="合成消息",
            sent_at=datetime.now(UTC),
        )
    )
    await session.flush()
    return customer.id


@pytest.mark.asyncio
async def test_failed_customer_keeps_committed_write_and_rolls_back_the_rest(
    monkeypatch,
) -> None:
    """客户 1 提交前的 A 保留、提交后的 B 回滚；客户 2 的 C 正常落库，本轮仍记心跳。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        first = await _customer_with_message(session, 1)
        second = await _customer_with_message(session, 2)
        await session.commit()
    # 合成步骤按客户编号 1、2 区分行为，这里确认建库后的编号与之对应。
    assert (first, second) == (1, 2)

    async def stop_after_round(delay: float) -> None:
        """一轮结束进入一小时等待时停止循环。"""
        raise StopContextMaintenance

    monkeypatch.setattr(application, "SQLAlchemyContextRepository", SessionExposingRepository)
    monkeypatch.setattr(application, "ContextRetentionService", SyntheticRetentionService)
    monkeypatch.setattr(application.asyncio, "sleep", stop_after_round)
    heartbeats: list[datetime] = []
    completed_at = datetime(2026, 11, 19, 8, 0, tzinfo=UTC)

    with pytest.raises(StopContextMaintenance):
        await application._run_context_maintenance_loop(
            factory=factory,
            summarizer=object(),
            heartbeat_now=lambda: completed_at,
            heartbeat=heartbeats.append,
        )

    # 用新会话读库，确认结果来自已提交的数据而不是某个会话的缓存。
    async with factory() as verify:
        rows: dict[int, Any] = {
            row.customer_id: (row.short_summary, row.long_summary)
            for row in (await verify.scalars(select(CustomerContextSummary))).all()
        }
    await engine.dispose()

    assert rows == {1: ("A", ""), 2: ("C", "")}
    # 单客户失败不算整轮失败，心跳照常记录。
    assert heartbeats == [completed_at]
