from typing import Any, Protocol


class CustomerTagSyncRepository(Protocol):
    """定义客户标签同步所需的最小身份和状态操作。"""

    async def verified_contact_id(self, customer_id: int) -> str | None:
        """返回已验证企业微信客户联系身份。"""

    async def resolve_wecom_tag_ids(self, tag_ids: list[int]) -> list[str]:
        """把内部标签主键映射为企业微信标签编号。"""

    async def mark_sync_completed(self, customer_id: int) -> None:
        """清除当前客户标签的待同步状态。"""

    async def mark_sync_failed(
        self,
        customer_id: int,
        error_code: str,
    ) -> None:
        """保留待同步状态并记录安全错误类型。"""


class CustomerTagContactApi(Protocol):
    """定义企业微信客户标签写接口。"""

    async def mark_tags(
        self,
        external_userid: str,
        *,
        add_tag_ids: list[str],
        remove_tag_ids: list[str],
    ) -> None:
        """增删一个外部联系人的标签。"""


class CustomerTagSyncService:
    """只同步已验证客户联系身份，失败不回滚本地标签。"""

    def __init__(
        self,
        repository: CustomerTagSyncRepository,
        contact_api: CustomerTagContactApi,
    ) -> None:
        """注入标签仓储和可选客户联系客户端。"""
        self._repository = repository
        self._contact_api = contact_api

    async def handle(self, payload: dict[str, Any]) -> None:
        """解析内部标签差异并幂等同步企业微信。"""
        customer_id = int(payload["customer_id"])
        external_userid = await self._repository.verified_contact_id(
            customer_id
        )
        if external_userid is None:
            await self._repository.mark_sync_completed(customer_id)
            return
        add_tag_ids = await self._repository.resolve_wecom_tag_ids(
            [int(item) for item in payload.get("add_tag_ids", [])]
        )
        remove_tag_ids = await self._repository.resolve_wecom_tag_ids(
            [int(item) for item in payload.get("remove_tag_ids", [])]
        )
        try:
            await self._contact_api.mark_tags(
                external_userid,
                add_tag_ids=add_tag_ids,
                remove_tag_ids=remove_tag_ids,
            )
        except Exception as error:
            await self._repository.mark_sync_failed(
                customer_id,
                type(error).__name__,
            )
            raise
        await self._repository.mark_sync_completed(customer_id)
