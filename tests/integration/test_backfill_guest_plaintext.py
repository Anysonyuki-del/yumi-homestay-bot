"""存量客人资料回填：把审批与客户手机号的密文解密写入 1.41.0 新增的明文列。"""

from datetime import UTC, date, datetime

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import ApprovalStatus
from homestay_bot.domain.models import Base, BookingApproval, Conversation, Customer
from homestay_bot.services.approval_sensitive_data import ApprovalSensitiveData
from homestay_bot.services.sensitive_data import SensitiveDataCipher
from homestay_bot.tools.backfill_guest_plaintext import backfill


def _approval(code: str, conversation_id: int) -> BookingApproval:
    """最小审批记录。"""
    return BookingApproval(
        approval_code=code,
        conversation_id=conversation_id,
        status=ApprovalStatus.PENDING,
        check_in_date=date(2026, 10, 1),
        check_out_date=date(2026, 10, 2),
        number_of_guests=2,
        room_type_preference="江景房",
    )


async def _seed(tmp_path, cipher: SensitiveDataCipher):
    """建库：一条只有密文的审批、一条已清理的旧审批、一条密文损坏的审批、一个只有密文手机号的客户。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'backfill.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sensitive = ApprovalSensitiveData(cipher)
    async with factory() as session:
        conversation = Conversation(open_kfid="wk", external_userid="wm")
        session.add(conversation)
        await session.flush()
        legacy = _approval("LEGACY", conversation.id)
        sensitive.write(
            legacy, guest_name="王五", guest_mobile="13700137000", special_requests="安静"
        )
        legacy.guest_name = legacy.guest_mobile = legacy.special_requests = None
        purged = _approval("PURGED", conversation.id)
        purged.pii_purged_at = datetime(2026, 8, 1, tzinfo=UTC)
        broken = _approval("BROKEN", conversation.id)
        broken.guest_name_ciphertext = b"broken"
        broken.guest_mobile_ciphertext = b"broken"
        customer = Customer(display_name="老客", phone_ciphertext=cipher.encrypt("13600136000"))
        session.add_all([legacy, purged, broken, customer])
        await session.commit()
    return engine, factory


@pytest.mark.asyncio
async def test_preview_counts_without_writing_and_apply_fills_plaintext(tmp_path) -> None:
    """预览只计数不写库；--apply 回填后可读明文；再次执行不重复写；已清理与损坏的记录不动。"""
    cipher = SensitiveDataCipher(Fernet.generate_key().decode("ascii"))
    engine, factory = await _seed(tmp_path, cipher)

    async with factory() as session:
        preview = await backfill(session, cipher, apply=False)
        await session.rollback()
    assert preview == {"approvals": 1, "customers": 1, "failed": 1}
    async with factory() as session:
        legacy = await session.scalar(
            select(BookingApproval).where(BookingApproval.approval_code == "LEGACY")
        )
        assert legacy is not None and legacy.guest_name is None

    async with factory() as session:
        applied = await backfill(session, cipher, apply=True)
        await session.commit()
    assert applied == {"approvals": 1, "customers": 1, "failed": 1}
    async with factory() as session:
        rows = {
            row.approval_code: row
            for row in await session.scalars(select(BookingApproval))
        }
        customer = await session.scalar(select(Customer))
    assert (rows["LEGACY"].guest_name, rows["LEGACY"].guest_mobile) == ("王五", "13700137000")
    assert rows["LEGACY"].special_requests == "安静"
    assert rows["PURGED"].guest_name is None
    assert rows["BROKEN"].guest_name is None
    assert customer is not None and customer.phone == "13600136000"

    async with factory() as session:
        again = await backfill(session, cipher, apply=True)
        await session.commit()
    assert again == {"approvals": 0, "customers": 0, "failed": 1}
    await engine.dispose()
