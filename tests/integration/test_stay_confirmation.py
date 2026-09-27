from datetime import UTC, date, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import MessageOrigin
from homestay_bot.domain.models import (
    Base,
    Conversation,
    Customer,
    Message,
    PropertyProfile,
    StayOrder,
)
from homestay_bot.repositories.conversations import SQLAlchemyConversationRepository


@pytest.mark.asyncio
async def test_stay_confirmation_checks_snapshot_message_and_ownership() -> None:
    """确认只接受当前提示的客人回复，改期和归属变化立即失效。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add_all(
            [Customer(id=1, display_name="合成客人"), PropertyProfile(id=1, title="201")]
        )
        await session.flush()
        conversation = Conversation(id=1, customer_id=1, open_kfid="test", external_userid="test")
        order = StayOrder(
            id=1,
            customer_id=1,
            property_id=1,
            hostex_reservation_code="fake",
            stay_code="fake",
            check_in_date=date(2026, 9, 25),
            check_out_date=date(2026, 9, 28),
            status="confirmed",
        )
        session.add_all([conversation, order])
        await session.flush()
        for msgid in ("prompt", "yes", "new_prompt", "new_yes"):
            session.add(
                Message(
                    conversation_id=1,
                    external_message_id=msgid,
                    origin=MessageOrigin.GUEST,
                    message_type="text",
                    content="测试",
                    sent_at=datetime(2026, 9, 25, tzinfo=UTC),
                )
            )
        await session.flush()
        repo = SQLAlchemyConversationRepository(session)
        now = datetime(2026, 9, 25, tzinfo=UTC)
        assert not await repo.confirm_stay(
            1, prompt_message_id="prompt", guest_message_id="yes", now=now
        )
        prompt = await repo.prepare_stay_confirmation(
            1, source_message_id="prompt", today=now.date()
        )
        assert "201" in prompt and "2026-09-28" in prompt
        assert await repo.confirm_stay(
            1, prompt_message_id="prompt", guest_message_id="yes", now=now
        )
        assert await repo.confirm_stay(
            1, prompt_message_id="prompt", guest_message_id="yes", now=now
        )
        assert (await repo.get_confirmed_stay(1, today=now.date()))["property_id"] == 1
        assert await repo.prepare_stay_confirmation(
            1, source_message_id="prompt", today=now.date()
        ) == ""
        assert await repo.get_confirmed_stay(1, today=now.date(), lock=False)
        assert await repo.get_confirmed_stay(1, today=date(2026, 9, 29)) is None
        assert await repo.get_confirmed_stay(1, today=date(2026, 9, 29), allow_history=True)
        order.check_out_date = date(2026, 9, 29)
        await session.flush()
        assert await repo.get_confirmed_stay(1, today=now.date()) is None
        await repo.prepare_stay_confirmation(1, source_message_id="new_prompt", today=now.date())
        assert not await repo.confirm_stay(
            1, prompt_message_id="prompt", guest_message_id="yes", now=now
        )
        order.customer_id = None
        await session.flush()
        assert not await repo.confirm_stay(
            1, prompt_message_id="new_prompt", guest_message_id="new_yes", now=now
        )
        order.customer_id = 1
        session.add(
            StayOrder(
                id=2,
                customer_id=1,
                property_id=1,
                hostex_reservation_code="second",
                stay_code="second",
                check_in_date=date(2026, 10, 1),
                check_out_date=date(2026, 10, 3),
                status="confirmed",
            )
        )
        await session.flush()
        choice = await repo.prepare_stay_confirmation(
            1, source_message_id="new_prompt", today=now.date()
        )
        assert "多个有效订单" in choice
        assert conversation.stay_confirmation is None
        denied = await repo.prepare_stay_confirmation(
            1, source_message_id="new_prompt", today=now.date(), order_id=999
        )
        assert "管家核实" in denied
        await repo.prepare_stay_confirmation(
            1, source_message_id="new_prompt", today=now.date(), order_id=2
        )
        await repo.decline_stay(1, prompt_message_id="prompt")
        assert conversation.stay_confirmation is not None
        await repo.decline_stay(1, prompt_message_id="new_prompt")
        assert not await repo.confirm_stay(
            1, prompt_message_id="new_prompt", guest_message_id="new_yes", now=now
        )
    await engine.dispose()


def test_stay_confirmation_migration_preserves_existing_conversation(tmp_path) -> None:
    """隔离旧库升级不猜测确认，降级再升级保留会话。"""
    import os
    import sqlite3
    import subprocess
    import sys

    database = tmp_path / "stay.sqlite"
    environment = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{database}"}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0027_knowledge_embeddings"],
        env=environment,
        check=True,
        capture_output=True,
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO conversations (id, open_kfid, external_userid, language, mode) "
            "VALUES (1, 'synthetic', 'synthetic', 'ZH', 'BOT_ACTIVE')"
        )
    for command, revision in (
        ("upgrade", "head"),
        ("downgrade", "0029_knowledge_scope"),
        ("upgrade", "head"),
    ):
        subprocess.run(
            [sys.executable, "-m", "alembic", command, revision],
            env=environment,
            check=True,
            capture_output=True,
        )
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT stay_confirmation FROM conversations WHERE id=1"
        ).fetchone() == (None,)
