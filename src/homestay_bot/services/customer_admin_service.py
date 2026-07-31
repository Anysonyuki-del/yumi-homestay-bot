from dataclasses import dataclass
from typing import Any, Protocol

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.domain.models import Employee
from homestay_bot.services.sensitive_data import SensitiveDataCipher


@dataclass(frozen=True)
class CustomerCard:
    """只包含 CRM 页面允许渲染的客户字段。"""

    id: int
    display_name: str
    note: str
    masked_phone: str


class CustomerAdminRepository(Protocol):
    """定义管理员 CRM 页面所需的查询和写操作。"""

    async def list_customers(self, query: str | None) -> list[Any]:
        """搜索未合并客户。"""

    async def customer_detail(self, customer_id: int) -> dict[str, Any]:
        """返回客户关联标签、摘要和合并建议。"""

    async def merge_detail(self, suggestion_id: int) -> dict[str, Any]:
        """返回待审核合并建议及两侧客户。"""

    async def replace_tags(
        self,
        customer_id: int,
        tag_ids: list[int],
        administrator_id: int,
    ) -> tuple[list[int], list[int], int]:
        """替换本地标签并返回增删差异和审计修订号。"""

    async def update_note(
        self,
        customer_id: int,
        note: str,
        administrator_id: int,
    ) -> None:
        """更新客户备注。"""

    async def update_summary(
        self,
        *,
        customer_id: int,
        administrator_id: int,
        short_summary: str,
        long_summary: str,
        unresolved_items: list[str],
    ) -> None:
        """管理员更正客户摘要。"""

    async def delete_summary(
        self,
        customer_id: int,
        administrator_id: int,
    ) -> None:
        """删除客户摘要。"""

    async def review_merge(
        self,
        suggestion_id: int,
        administrator_id: int,
        accepted: bool,
    ) -> None:
        """确认或拒绝客户合并建议。"""

    async def create_manual_merge_suggestion(
        self,
        source_customer_id: int,
        target_customer_id: int,
        administrator_id: int,
    ) -> int:
        """创建待二次确认的管理员手动合并建议。"""

    async def has_verified_contact_identity(self, customer_id: int) -> bool:
        """判断客户是否关联已验证企业微信客户联系身份。"""

    async def mark_sync_completed(self, customer_id: int) -> None:
        """清除无需同步或已完成的标签待同步状态。"""


class CustomerAdminJobQueue(Protocol):
    """定义企业微信标签同步任务入队接口。"""

    async def enqueue(
        self,
        job_type: str,
        payload: dict[str, Any],
        *,
        dedupe_key: str,
    ) -> Any:
        """登记本地提交后的异步标签同步任务。"""


class CustomerAdminService:
    """执行管理员 CRM 权限、脱敏展示和本地优先写入。"""

    def __init__(
        self,
        repository: CustomerAdminRepository,
        cipher: SensitiveDataCipher,
        jobs: CustomerAdminJobQueue,
        *,
        tag_sync_enabled: bool,
    ) -> None:
        """注入 CRM 仓储、加密服务、任务队列和同步开关。"""
        self._repository = repository
        self._cipher = cipher
        self._jobs = jobs
        self._tag_sync_enabled = tag_sync_enabled

    async def list_customers(
        self,
        query: str | None,
        administrator: Employee,
    ) -> list[CustomerCard]:
        """只向管理员返回不含密文的客户卡片。"""
        self._require_admin(administrator)
        customers = await self._repository.list_customers(query)
        return [self._card(item) for item in customers]

    async def get_detail(
        self,
        customer_id: int,
        administrator: Employee,
    ) -> dict[str, Any]:
        """返回脱敏客户详情并移除原始客户 ORM 对象。"""
        self._require_admin(administrator)
        detail = await self._repository.customer_detail(customer_id)
        customer = detail["customer"]
        return {
            **detail,
            "customer": self._card(customer),
            "masked_phone": self._masked_phone(customer.phone_ciphertext),
        }

    async def set_tags(
        self,
        customer_id: int,
        tag_ids: list[int],
        administrator: Employee,
    ) -> None:
        """先替换本地标签，再按配置和可靠身份决定是否入队同步。"""
        self._require_admin(administrator)
        added, removed, revision = await self._repository.replace_tags(
            customer_id,
            sorted(set(tag_ids)),
            administrator.id,
        )
        if (
            not self._tag_sync_enabled
            or not await self._repository.has_verified_contact_identity(
                customer_id
            )
        ):
            await self._repository.mark_sync_completed(customer_id)
            return
        await self._jobs.enqueue(
            "customer_tag_sync",
            {
                "customer_id": customer_id,
                "add_tag_ids": added,
                "remove_tag_ids": removed,
            },
            dedupe_key=f"customer-tag-sync:{customer_id}:{revision}",
        )

    async def get_merge_detail(
        self,
        suggestion_id: int,
        administrator: Employee,
    ) -> dict[str, Any]:
        """返回只含脱敏客户卡片的合并人工复核信息。"""
        self._require_admin(administrator)
        detail = await self._repository.merge_detail(suggestion_id)
        return {
            "suggestion": detail["suggestion"],
            "source": self._card(detail["source"]),
            "target": self._card(detail["target"]),
        }

    async def create_manual_merge(
        self,
        source_customer_id: int,
        target_customer_id: int,
        administrator: Employee,
    ) -> int:
        """只允许管理员为两个不同客户创建待复核合并建议。"""
        self._require_admin(administrator)
        if source_customer_id == target_customer_id:
            raise ValueError("不能将客户档案合并到自身")
        return await self._repository.create_manual_merge_suggestion(
            source_customer_id,
            target_customer_id,
            administrator.id,
        )

    async def update_note(
        self,
        customer_id: int,
        note: str,
        administrator: Employee,
    ) -> None:
        """清理并保存最多两千字的管理员备注。"""
        self._require_admin(administrator)
        cleaned = note.strip()
        if len(cleaned) > 2000:
            raise ValueError("客户备注不得超过 2000 个字符")
        await self._repository.update_note(
            customer_id,
            cleaned,
            administrator.id,
        )

    async def update_summary(
        self,
        customer_id: int,
        administrator: Employee,
        *,
        short_summary: str,
        long_summary: str,
        unresolved_items: list[str],
    ) -> None:
        """由管理员更正摘要，限制长度并清除空待办。"""
        self._require_admin(administrator)
        short_value = short_summary.strip()
        long_value = long_summary.strip()
        if len(short_value) > 4000 or len(long_value) > 8000:
            raise ValueError("客户摘要内容过长")
        await self._repository.update_summary(
            customer_id=customer_id,
            administrator_id=administrator.id,
            short_summary=short_value,
            long_summary=long_value,
            unresolved_items=[
                item.strip()[:500]
                for item in unresolved_items
                if item.strip()
            ][:20],
        )

    async def delete_summary(
        self,
        customer_id: int,
        administrator: Employee,
    ) -> None:
        """只允许管理员删除客户摘要。"""
        self._require_admin(administrator)
        await self._repository.delete_summary(
            customer_id,
            administrator.id,
        )

    async def review_merge(
        self,
        suggestion_id: int,
        administrator: Employee,
        *,
        accepted: bool,
    ) -> None:
        """只允许管理员确认或拒绝合并建议。"""
        self._require_admin(administrator)
        await self._repository.review_merge(
            suggestion_id,
            administrator.id,
            accepted,
        )

    def _card(self, customer: Any) -> CustomerCard:
        """从 ORM 或测试对象提取无密文客户卡片。"""
        return CustomerCard(
            id=int(customer.id),
            display_name=str(customer.display_name),
            note=str(customer.note or ""),
            masked_phone=self._masked_phone(customer.phone_ciphertext),
        )

    def _masked_phone(self, ciphertext: bytes | None) -> str:
        """仅在内存解密手机号并立即转换为脱敏格式。"""
        if ciphertext is None:
            return "未登记"
        phone = self._cipher.decrypt(ciphertext)
        if len(phone) >= 7:
            return f"{phone[:3]}****{phone[-4:]}"
        return "已登记"

    @staticmethod
    def _require_admin(administrator: Employee) -> None:
        """拒绝停用员工或普通员工进入 CRM。"""
        if (
            not administrator.is_active
            or administrator.role is not EmployeeRole.ADMIN
        ):
            raise PermissionError("只有管理员可以管理客户")
