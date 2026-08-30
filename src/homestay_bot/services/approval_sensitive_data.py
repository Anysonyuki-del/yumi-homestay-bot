from dataclasses import dataclass
from typing import Protocol

from homestay_bot.domain.models import BookingApproval
from homestay_bot.services.sensitive_data import SensitiveDataCipher


@dataclass(frozen=True)
class ApprovalSensitiveFields:
    """保存一次审批敏感字段解密后的短生命周期值。"""

    guest_name: str
    guest_mobile: str
    special_requests: str | None


class ApprovalSensitiveData:
    """集中处理审批敏感字段的用途隔离加密、读取和过渡回填。"""

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
        """把审批敏感值写入用途隔离密文；旧明文由过渡调用方负责双写。"""
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

    def read(self, approval: BookingApproval) -> ApprovalSensitiveFields:
        """优先解密新字段，仅在密文缺失时兼容读取旧明文。"""
        guest_name = (
            self._cipher.decrypt(
                approval.guest_name_ciphertext,
                purpose=self._GUEST_NAME_PURPOSE,
            )
            if approval.guest_name_ciphertext is not None
            else approval.guest_name
        )
        guest_mobile = (
            self._cipher.decrypt(
                approval.guest_mobile_ciphertext,
                purpose=self._GUEST_MOBILE_PURPOSE,
            )
            if approval.guest_mobile_ciphertext is not None
            else approval.guest_mobile
        )
        special_requests = (
            self._cipher.decrypt(
                approval.special_requests_ciphertext,
                purpose=self._SPECIAL_REQUESTS_PURPOSE,
            )
            if approval.special_requests_ciphertext is not None
            else approval.special_requests
        )
        return ApprovalSensitiveFields(
            guest_name=guest_name,
            guest_mobile=guest_mobile,
            special_requests=special_requests,
        )

    def ensure_encrypted(self, approval: BookingApproval) -> bool:
        """只补齐缺失密文，重复执行不得覆盖已经存在的密文。"""
        changed = False
        if approval.guest_name_ciphertext is None:
            approval.guest_name_ciphertext = self._cipher.encrypt(
                approval.guest_name,
                purpose=self._GUEST_NAME_PURPOSE,
            )
            changed = True
        if approval.guest_mobile_ciphertext is None:
            approval.guest_mobile_ciphertext = self._cipher.encrypt(
                approval.guest_mobile,
                purpose=self._GUEST_MOBILE_PURPOSE,
            )
            changed = True
        if (
            approval.special_requests is not None
            and approval.special_requests_ciphertext is None
        ):
            approval.special_requests_ciphertext = self._cipher.encrypt(
                approval.special_requests,
                purpose=self._SPECIAL_REQUESTS_PURPOSE,
            )
            changed = True
        return changed


class ApprovalSensitiveDataBackfillRepository(Protocol):
    """定义单批审批密文回填所需的最小仓储接口。"""

    async def list_sensitive_data_backfill_batch(
        self,
        *,
        after_id: int,
        limit: int,
    ) -> list[BookingApproval]:
        """按主键稳定顺序返回仍缺少必要密文的审批。"""

    async def save(self, approval: BookingApproval) -> None:
        """保存已补齐密文的审批。"""


@dataclass(frozen=True)
class ApprovalSensitiveDataBackfillResult:
    """记录一次有界回填的扫描进度，供调用方显式续跑。"""

    scanned: int
    updated: int
    last_id: int


class ApprovalSensitiveDataBackfillService:
    """每次最多回填 100 条旧审批，不自行启动后台循环。"""

    _MAX_BATCH_SIZE = 100

    def __init__(
        self,
        repository: ApprovalSensitiveDataBackfillRepository,
        sensitive_data: ApprovalSensitiveData,
    ) -> None:
        """注入审批仓储和统一敏感数据服务。"""
        self._repository = repository
        self._sensitive_data = sensitive_data

    async def run_batch(
        self,
        *,
        after_id: int = 0,
        limit: int = _MAX_BATCH_SIZE,
    ) -> ApprovalSensitiveDataBackfillResult:
        """运行一个有上限且可从最后主键继续的幂等回填批次。"""
        bounded_limit = max(1, min(limit, self._MAX_BATCH_SIZE))
        approvals = await self._repository.list_sensitive_data_backfill_batch(
            after_id=after_id,
            limit=bounded_limit,
        )
        updated = 0
        for approval in approvals:
            if self._sensitive_data.ensure_encrypted(approval):
                await self._repository.save(approval)
                updated += 1
        return ApprovalSensitiveDataBackfillResult(
            scanned=len(approvals),
            updated=updated,
            last_id=approvals[-1].id if approvals else after_id,
        )
