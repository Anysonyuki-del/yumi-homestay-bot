from datetime import UTC, datetime

import pytest
from cryptography.fernet import Fernet

from homestay_bot.domain.enums import (
    CustomerIdentityProvider,
    CustomerMergeStatus,
    MessageOrigin,
)
from homestay_bot.domain.models import Customer, CustomerMergeSuggestion
from homestay_bot.services.customer_service import CustomerService
from homestay_bot.services.message_service import IncomingMessage
from homestay_bot.services.sensitive_data import SensitiveDataCipher


class CustomerRepositoryStub:
    """记录客户服务发出的精确仓储指令。"""

    def __init__(self) -> None:
        """初始化调用记录和固定返回对象。"""
        self.identities: list[tuple[CustomerIdentityProvider, str, str]] = []
        self.fingerprints: list[tuple[int, str]] = []
        self.confirmations: list[tuple[int, int]] = []
        self.customers_were_merged = False
        self.customer = Customer(id=7, display_name="微信客户")
        self.suggestion = CustomerMergeSuggestion(
            id=9,
            source_customer_id=2,
            target_customer_id=7,
            reason="verified_phone",
            status=CustomerMergeStatus.PENDING,
        )

    async def ensure_identity(
        self,
        *,
        provider: CustomerIdentityProvider,
        external_id: str,
        display_name: str,
    ) -> Customer:
        """记录首次身份建档参数。"""
        self.identities.append((provider, external_id, display_name))
        return self.customer

    async def suggest_unique_phone_match(
        self, source_customer_id: int, fingerprint: str
    ) -> CustomerMergeSuggestion | None:
        """记录不可逆手机号指纹，不直接合并客户。"""
        self.fingerprints.append((source_customer_id, fingerprint))
        return self.suggestion

    async def merge_locked(
        self, suggestion_id: int, administrator_id: int
    ) -> Customer:
        """模拟管理员确认后的仓储原子合并。"""
        self.confirmations.append((suggestion_id, administrator_id))
        self.customers_were_merged = True
        return self.customer


def incoming_message() -> IncomingMessage:
    """构造首次微信客服咨询消息。"""
    return IncomingMessage(
        msgid="msg-1",
        open_kfid="wk-1",
        external_userid="wm-1",
        origin=MessageOrigin.GUEST,
        msgtype="text",
        content="你好",
        sent_at=datetime(2026, 7, 31, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_first_message_creates_verified_wecom_customer_identity() -> None:
    """首次咨询必须立即建立正式客户和微信客服身份。"""
    repository = CustomerRepositoryStub()
    service = CustomerService(
        repository,
        SensitiveDataCipher(Fernet.generate_key().decode("ascii")),
    )

    customer = await service.ensure_for_message(incoming_message())

    assert customer.id == 7
    assert repository.identities == [
        (CustomerIdentityProvider.WECOM_KF, "wm-1", "微信客户")
    ]


@pytest.mark.asyncio
async def test_phone_match_only_creates_merge_suggestion() -> None:
    """可靠手机号命中只能产生建议，不能绕过管理员直接合并。"""
    repository = CustomerRepositoryStub()
    service = CustomerService(
        repository,
        SensitiveDataCipher(Fernet.generate_key().decode("ascii")),
    )

    suggestion = await service.suggest_merge(
        source_customer_id=2,
        verified_phone="13800000000",
    )

    assert suggestion is repository.suggestion
    assert repository.fingerprints[0][0] == 2
    assert len(repository.fingerprints[0][1]) == 64
    assert repository.customers_were_merged is False


@pytest.mark.asyncio
async def test_confirm_merge_delegates_administrator_and_suggestion() -> None:
    """客户服务必须把管理员身份传给带行锁的合并仓储。"""
    repository = CustomerRepositoryStub()
    service = CustomerService(
        repository,
        SensitiveDataCipher(Fernet.generate_key().decode("ascii")),
    )

    customer = await service.confirm_merge(
        suggestion_id=9,
        administrator_id=3,
    )

    assert customer.id == 7
    assert repository.confirmations == [(9, 3)]
