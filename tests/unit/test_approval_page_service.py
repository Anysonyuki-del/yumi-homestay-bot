from datetime import date

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from homestay_bot.domain.enums import ApprovalStatus
from homestay_bot.domain.models import Base, BookingApproval, Conversation
from homestay_bot.services.approval_page_service import ApprovalPageService
from homestay_bot.services.approval_sensitive_data import ApprovalSensitiveData
from homestay_bot.services.sensitive_data import SensitiveDataCipher


class HostexReadStub:
    """返回审批详情所需的空参考目录。"""

    async def list_properties(self):
        """返回空房源目录。"""
        return []

    async def list_reference_prices(self, start_date, end_date):
        """返回空参考价。"""
        return []

    async def list_income_methods(self):
        """返回空收款方式。"""
        return []


class BookingStub:
    """详情读取测试不执行真实确认。"""

    async def confirm_and_create(self, approval_id, employee_id, command):
        """意外调用时立即失败。"""
        raise AssertionError("详情读取不应创建订单")


@pytest.mark.asyncio
async def test_page_service_returns_decrypted_view_without_orm_ciphertext() -> None:
    """模板只能接收解密后的只读视图，不能接触 ORM 密文字段。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sensitive = ApprovalSensitiveData(
        SensitiveDataCipher(Fernet.generate_key().decode("ascii"))
    )

    async with factory() as session:
        conversation = Conversation(open_kfid="wk-1", external_userid="wm-1")
        session.add(conversation)
        await session.flush()
        approval = BookingApproval(
            approval_code="APP-VIEW",
            conversation_id=conversation.id,
            status=ApprovalStatus.PENDING,
            check_in_date=date(2026, 9, 1),
            check_out_date=date(2026, 9, 2),
            number_of_guests=2,
            guest_name="张三",
            guest_mobile="13800138000",
            room_type_preference="江景房",
            special_requests="高楼层",
        )
        sensitive.write(
            approval,
            guest_name="张三",
            guest_mobile="13800138000",
            special_requests="高楼层",
        )
        session.add(approval)
        await session.commit()

        service = ApprovalPageService(
            session=session,
            hostex=HostexReadStub(),
            booking=BookingStub(),
            sensitive_data=sensitive,
        )
        detail = await service.get_detail(approval.id)
        pending = await service.list_pending(offset=0, limit=10)

    view = detail["approval"]
    assert view.guest_name == "张三"
    assert view.special_requests == "高楼层"
    assert detail["masked_mobile"] == "138****8000"
    assert not isinstance(view, BookingApproval)
    assert not hasattr(view, "guest_name_ciphertext")
    assert pending[0].guest_name == "张三"
    assert not isinstance(pending[0], BookingApproval)
    await engine.dispose()
