from typing import Protocol

from homestay_bot.domain.enums import CustomerIdentityProvider
from homestay_bot.domain.models import Customer, CustomerMergeSuggestion
from homestay_bot.services.message_service import IncomingMessage
from homestay_bot.services.sensitive_data import SensitiveDataCipher


class CustomerRepository(Protocol):
    """定义客户服务依赖的最小持久化边界。"""

    async def ensure_identity(
        self,
        *,
        provider: CustomerIdentityProvider,
        external_id: str,
        display_name: str,
    ) -> Customer:
        """按可靠渠道身份幂等返回客户。"""

    async def suggest_unique_phone_match(
        self,
        source_customer_id: int,
        fingerprint: str,
    ) -> CustomerMergeSuggestion | None:
        """按手机号指纹建立唯一匹配建议。"""

    async def merge_locked(
        self,
        suggestion_id: int,
        administrator_id: int,
    ) -> Customer:
        """在同一事务锁定并合并客户。"""


class CustomerService:
    """执行首次咨询建档、可靠匹配和管理员确认边界。"""

    def __init__(
        self,
        customers: CustomerRepository,
        cipher: SensitiveDataCipher,
    ) -> None:
        """注入客户仓储和独立敏感数据服务。"""
        self._customers = customers
        self._cipher = cipher

    async def ensure_for_message(self, message: IncomingMessage) -> Customer:
        """为每个微信客服联系人立即建立或复用正式客户档案。"""
        return await self._customers.ensure_identity(
            provider=CustomerIdentityProvider.WECOM_KF,
            external_id=message.external_userid,
            display_name="微信客户",
        )

    async def suggest_merge(
        self,
        source_customer_id: int,
        verified_phone: str,
    ) -> CustomerMergeSuggestion | None:
        """把已验证手机号转换为指纹，只生成待管理员确认建议。"""
        fingerprint = self._cipher.fingerprint(verified_phone)
        return await self._customers.suggest_unique_phone_match(
            source_customer_id,
            fingerprint,
        )

    async def confirm_merge(
        self,
        suggestion_id: int,
        administrator_id: int,
    ) -> Customer:
        """把管理员确认交给带行锁和审计的仓储事务。"""
        return await self._customers.merge_locked(
            suggestion_id,
            administrator_id,
        )
