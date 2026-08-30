from datetime import date

import pytest
from cryptography.fernet import Fernet, InvalidToken

from homestay_bot.domain.enums import ApprovalStatus
from homestay_bot.domain.models import BookingApproval
from homestay_bot.services.approval_sensitive_data import (
    ApprovalSensitiveData,
    ApprovalSensitiveDataBackfillService,
)
from homestay_bot.services.sensitive_data import SensitiveDataCipher


def approval() -> BookingApproval:
    """创建包含旧明文字段的阶段 2A 审批记录。"""
    return BookingApproval(
        id=7,
        approval_code="APP-7",
        conversation_id=1,
        status=ApprovalStatus.PENDING,
        check_in_date=date(2026, 9, 1),
        check_out_date=date(2026, 9, 2),
        number_of_guests=2,
        guest_name="张三",
        guest_mobile="13800138000",
        room_type_preference="江景房",
        special_requests="高楼层",
    )


def sensitive_data() -> ApprovalSensitiveData:
    """使用随机测试密钥构造审批敏感数据服务。"""
    return ApprovalSensitiveData(
        SensitiveDataCipher(Fernet.generate_key().decode("ascii"))
    )


def test_write_encrypts_each_field_with_separate_purpose() -> None:
    """三类审批字段必须双写密文，且用途之间不能互相解密。"""
    item = approval()
    cipher = SensitiveDataCipher(Fernet.generate_key().decode("ascii"))
    service = ApprovalSensitiveData(cipher)

    service.write(
        item,
        guest_name=item.guest_name,
        guest_mobile=item.guest_mobile,
        special_requests=item.special_requests,
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


def test_read_uses_legacy_plaintext_only_when_ciphertext_is_missing() -> None:
    """旧记录允许回退明文，但已有损坏密文时不得静默回退。"""
    item = approval()
    service = sensitive_data()

    assert service.read(item).guest_name == "张三"
    item.guest_name_ciphertext = b"corrupted"

    with pytest.raises(InvalidToken):
        service.read(item)


def test_ensure_encrypted_is_idempotent_for_legacy_rows() -> None:
    """历史记录回填重复执行时不得重新加密或改变既有密文。"""
    item = approval()
    service = sensitive_data()

    assert service.ensure_encrypted(item) is True
    first = (
        item.guest_name_ciphertext,
        item.guest_mobile_ciphertext,
        item.special_requests_ciphertext,
    )
    assert service.ensure_encrypted(item) is False
    assert (
        item.guest_name_ciphertext,
        item.guest_mobile_ciphertext,
        item.special_requests_ciphertext,
    ) == first


class BackfillRepositoryStub:
    """记录有界回填查询与保存行为。"""

    def __init__(self, items: list[BookingApproval]) -> None:
        """保存待回填审批及调用记录。"""
        self.items = items
        self.queries: list[tuple[int, int]] = []
        self.saved: list[int] = []

    async def list_sensitive_data_backfill_batch(
        self,
        *,
        after_id: int,
        limit: int,
    ) -> list[BookingApproval]:
        """返回调用方请求的测试批次。"""
        self.queries.append((after_id, limit))
        return self.items[:limit]

    async def save(self, item: BookingApproval) -> None:
        """记录已更新审批编号。"""
        self.saved.append(item.id)


@pytest.mark.asyncio
async def test_backfill_service_caps_each_batch_at_one_hundred() -> None:
    """即使调用方传入更大数量，单批回填也不得超过 100 条。"""
    items = [approval() for _ in range(2)]
    items[0].id = 7
    items[1].id = 8
    repository = BackfillRepositoryStub(items)
    service = ApprovalSensitiveDataBackfillService(
        repository,
        sensitive_data(),
    )

    result = await service.run_batch(after_id=0, limit=1_000)

    assert repository.queries == [(0, 100)]
    assert repository.saved == [7, 8]
    assert result.scanned == 2
    assert result.updated == 2
    assert result.last_id == 8
