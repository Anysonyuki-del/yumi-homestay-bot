from datetime import UTC, date, datetime

import pytest
from cryptography.fernet import Fernet, InvalidToken

from homestay_bot.domain.enums import ApprovalStatus
from homestay_bot.domain.models import BookingApproval
from homestay_bot.services.approval_sensitive_data import ApprovalSensitiveData
from homestay_bot.services.sensitive_data import SensitiveDataCipher


def approval() -> BookingApproval:
    """创建不含旧明文字段的阶段 2B 审批记录。"""
    return BookingApproval(
        id=7,
        approval_code="APP-7",
        conversation_id=1,
        status=ApprovalStatus.PENDING,
        check_in_date=date(2026, 9, 1),
        check_out_date=date(2026, 9, 2),
        number_of_guests=2,
        room_type_preference="江景房",
    )


def sensitive_data() -> ApprovalSensitiveData:
    """使用随机测试密钥构造审批敏感数据服务。"""
    return ApprovalSensitiveData(
        SensitiveDataCipher(Fernet.generate_key().decode("ascii"))
    )


def test_write_encrypts_each_field_with_separate_purpose() -> None:
    """三类审批字段必须只写密文，且用途之间不能互相解密。"""
    item = approval()
    cipher = SensitiveDataCipher(Fernet.generate_key().decode("ascii"))
    service = ApprovalSensitiveData(cipher)

    service.write(
        item,
        guest_name="张三",
        guest_mobile="13800138000",
        special_requests="高楼层",
    )

    assert "张三".encode() not in item.guest_name_ciphertext
    assert b"13800138000" not in item.guest_mobile_ciphertext
    assert "高楼层".encode() not in item.special_requests_ciphertext
    values = service.read(item)
    assert values.guest_name == "张三"
    assert values.guest_mobile == "13800138000"
    assert values.special_requests == "高楼层"
    with pytest.raises(InvalidToken):
        cipher.decrypt(
            item.guest_name_ciphertext,
            purpose="approval_guest_mobile",
        )


def test_read_does_not_hide_corrupted_ciphertext() -> None:
    """已有损坏密文时必须抛错，不能伪造或静默降级。"""
    item = approval()
    service = sensitive_data()
    service.write(
        item,
        guest_name="张三",
        guest_mobile="13800138000",
        special_requests=None,
    )
    item.guest_name_ciphertext = b"corrupted"

    with pytest.raises(InvalidToken):
        service.read(item)


def test_read_rejects_missing_required_ciphertext_after_compatibility_period() -> None:
    """2B 结束兼容期后，未清理记录缺少必需密文必须立即失败。"""
    item = approval()
    service = sensitive_data()

    with pytest.raises(ValueError, match="审批敏感资料密文缺失"):
        service.read(item)


def test_read_marks_intentionally_purged_fields_as_absent() -> None:
    """已经按保留期清理的记录应返回空值，不能恢复或伪造旧明文。"""
    item = approval()
    item.pii_purged_at = datetime(2026, 8, 31, tzinfo=UTC)
    service = sensitive_data()

    values = service.read(item)

    assert values.guest_name is None
    assert values.guest_mobile is None
    assert values.special_requests is None


def test_booking_rejects_intentionally_purged_fields() -> None:
    """已清理审批不得重新进入下单或写后核验流程。"""
    item = approval()
    item.pii_purged_at = datetime(2026, 8, 31, tzinfo=UTC)
    service = sensitive_data()

    with pytest.raises(ValueError, match="已清理"):
        service.require_for_booking(item)
