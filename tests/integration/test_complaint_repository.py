from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import ComplaintReviewStatus
from homestay_bot.domain.models import Base, ComplaintReview, Conversation, Customer
from homestay_bot.repositories.complaints import (
    ComplaintVersionConflict,
    SQLAlchemyComplaintRepository,
)


@pytest.fixture
async def repository():
    """创建独立数据库，验证客诉记录边界。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        customer = Customer(display_name="投诉客户")
        session.add(customer)
        await session.flush()
        conversation = Conversation(
            customer_id=customer.id,
            open_kfid="wk-test",
            external_userid="wm-test",
        )
        session.add(conversation)
        await session.flush()
        yield SQLAlchemyComplaintRepository(session), session, conversation.id
    await engine.dispose()


@pytest.mark.asyncio
async def test_complaint_review_is_idempotent_by_source_message(repository) -> None:
    """同一来源消息只能创建一条客诉记录。"""
    complaints, session, conversation_id = repository
    first = await complaints.create_or_get(
        conversation_id=conversation_id,
        source_message_id="msg-complaint-1",
        reason="complaint",
        risk_level="high",
    )
    repeated = await complaints.create_or_get(
        conversation_id=conversation_id,
        source_message_id="msg-complaint-1",
        reason="refund",
        risk_level="critical",
    )

    assert repeated.id == first.id
    assert repeated.reason == "complaint"
    assert repeated.status is ComplaintReviewStatus.PENDING_ANALYSIS
    await session.commit()


@pytest.mark.asyncio
async def test_complaint_review_version_and_status_are_guarded(repository) -> None:
    """编辑和发送必须校验版本，不能覆盖其他员工的更新。"""
    complaints, session, conversation_id = repository
    review = await complaints.create_or_get(
        conversation_id=conversation_id,
        source_message_id="msg-complaint-2",
        reason="refund",
        risk_level="high",
    )
    await complaints.mark_ready(
        review.id,
        analysis={"core_issue": "延迟入住", "refund_requested": True},
        draft="我会尽快为您核实。",
    )
    updated = await complaints.update_draft(
        review.id,
        expected_version=review.version,
        draft="我已经收到您的情况，会尽快为您核实。",
    )
    assert updated.version == 2
    assert updated.status is ComplaintReviewStatus.EDITING

    with pytest.raises(ComplaintVersionConflict):
        await complaints.update_draft(
            review.id,
            expected_version=1,
            draft="过期内容",
        )

    sent = await complaints.mark_sent(
        review.id,
        expected_version=updated.version,
        sent_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    assert sent.status is ComplaintReviewStatus.SENT
    await session.commit()


@pytest.mark.asyncio
async def test_complaint_review_does_not_persist_raw_guest_content(repository) -> None:
    """客诉记录只保存脱敏分析和草稿，不接受原始客人正文。"""
    complaints, session, conversation_id = repository
    review = await complaints.create_or_get(
        conversation_id=conversation_id,
        source_message_id="msg-complaint-3",
        reason="compensation",
        risk_level="high",
    )
    await complaints.mark_ready(
        review.id,
        analysis={"core_issue": "延迟入住", "phone": "13800138000"},
        draft="我会尽快为您核实。",
    )

    stored = await session.get(ComplaintReview, review.id)
    assert stored is not None
    assert "13800138000" not in str(stored.analysis)
    assert not hasattr(stored, "raw_content")
