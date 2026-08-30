from dataclasses import dataclass

from homestay_bot.domain.models import BookingApproval
from homestay_bot.services.sensitive_data import SensitiveDataCipher


@dataclass(frozen=True)
class ApprovalSensitiveFields:
    """保存一次审批敏感字段解密后的短生命周期值。"""

    guest_name: str | None
    guest_mobile: str | None
    special_requests: str | None


@dataclass(frozen=True)
class BookingApprovalSensitiveFields:
    """保存下单和写后核验必须存在的审批客人资料。"""

    guest_name: str
    guest_mobile: str


class ApprovalSensitiveData:
    """集中处理审批敏感字段的用途隔离加密和短生命周期解密。"""

    _GUEST_NAME_PURPOSE = "approval_guest_name"
    _GUEST_MOBILE_PURPOSE = "approval_guest_mobile"
    _SPECIAL_REQUESTS_PURPOSE = "approval_special_requests"

    def __init__(self, cipher: SensitiveDataCipher) -> None:
        """注入全局数据密钥派生器，但为每类审批字段使用独立用途。"""
        self._cipher = cipher

    def write(
        self,
        approval: BookingApproval,
        *,
        guest_name: str,
        guest_mobile: str,
        special_requests: str | None,
    ) -> None:
        """只把审批敏感值写入用途隔离密文，并重置清理标记。"""
        approval.guest_name_ciphertext = self._cipher.encrypt(
            guest_name,
            purpose=self._GUEST_NAME_PURPOSE,
        )
        approval.guest_mobile_ciphertext = self._cipher.encrypt(
            guest_mobile,
            purpose=self._GUEST_MOBILE_PURPOSE,
        )
        approval.special_requests_ciphertext = (
            self._cipher.encrypt(
                special_requests,
                purpose=self._SPECIAL_REQUESTS_PURPOSE,
            )
            if special_requests is not None
            else None
        )
        approval.pii_purged_at = None

    def read(self, approval: BookingApproval) -> ApprovalSensitiveFields:
        """解密未清理审批；缺少必需密文时拒绝静默降级。"""
        if approval.pii_purged_at is not None:
            return ApprovalSensitiveFields(
                guest_name=None,
                guest_mobile=None,
                special_requests=None,
            )
        if (
            approval.guest_name_ciphertext is None
            or approval.guest_mobile_ciphertext is None
        ):
            raise ValueError("审批敏感资料密文缺失")
        guest_name = self._cipher.decrypt(
            approval.guest_name_ciphertext,
            purpose=self._GUEST_NAME_PURPOSE,
        )
        guest_mobile = self._cipher.decrypt(
            approval.guest_mobile_ciphertext,
            purpose=self._GUEST_MOBILE_PURPOSE,
        )
        special_requests = (
            self._cipher.decrypt(
                approval.special_requests_ciphertext,
                purpose=self._SPECIAL_REQUESTS_PURPOSE,
            )
            if approval.special_requests_ciphertext is not None
            else None
        )
        return ApprovalSensitiveFields(
            guest_name=guest_name,
            guest_mobile=guest_mobile,
            special_requests=special_requests,
        )

    def require_for_booking(
        self,
        approval: BookingApproval,
    ) -> BookingApprovalSensitiveFields:
        """为下单状态机读取必需资料，已清理或缺失时拒绝继续。"""
        values = self.read(approval)
        if values.guest_name is None or values.guest_mobile is None:
            raise ValueError("审批敏感资料已清理，不能继续下单")
        return BookingApprovalSensitiveFields(
            guest_name=values.guest_name,
            guest_mobile=values.guest_mobile,
        )
