import pytest

from homestay_bot.services.customer_tag_sync import CustomerTagSyncService


class SyncRepositoryStub:
    """返回已验证客户联系身份并记录同步状态。"""

    def __init__(self, identity="wo-1") -> None:
        """配置可选企业微信客户联系身份。"""
        self.identity = identity
        self.completed: list[int] = []
        self.failed: list[tuple[int, str]] = []

    async def verified_contact_id(self, customer_id):
        """返回已验证外部联系人 ID。"""
        return self.identity

    async def resolve_wecom_tag_ids(self, tag_ids):
        """把内部标签主键映射为企业微信标签 ID。"""
        return [f"et-{item}" for item in tag_ids]

    async def mark_sync_completed(self, customer_id):
        """记录同步完成。"""
        self.completed.append(customer_id)

    async def mark_sync_failed(self, customer_id, error_code):
        """记录同步失败。"""
        self.failed.append((customer_id, error_code))


class ContactApiStub:
    """记录企业微信标签调用并可模拟失败。"""

    def __init__(self, error=None) -> None:
        """配置可选外部异常。"""
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def mark_tags(self, external_userid, **fields):
        """记录调用或抛出测试异常。"""
        self.calls.append(
            {"external_userid": external_userid, **fields}
        )
        if self.error is not None:
            raise self.error


@pytest.mark.asyncio
async def test_sync_only_uses_verified_contact_and_mapped_tags() -> None:
    """同步只发送已验证身份和存在企业微信映射的标签。"""
    repository = SyncRepositoryStub()
    api = ContactApiStub()
    service = CustomerTagSyncService(repository, api)

    await service.handle(
        {
            "customer_id": 7,
            "add_tag_ids": [1, 2],
            "remove_tag_ids": [3],
        }
    )

    assert api.calls == [
        {
            "external_userid": "wo-1",
            "add_tag_ids": ["et-1", "et-2"],
            "remove_tag_ids": ["et-3"],
        }
    ]
    assert repository.completed == [7]


@pytest.mark.asyncio
async def test_sync_failure_keeps_pending_without_exposing_identity() -> None:
    """外部失败必须保留待重试状态且只记录错误类型。"""
    repository = SyncRepositoryStub()
    service = CustomerTagSyncService(
        repository,
        ContactApiStub(TimeoutError("contains identity wo-secret")),
    )

    with pytest.raises(TimeoutError):
        await service.handle(
            {
                "customer_id": 7,
                "add_tag_ids": [1],
                "remove_tag_ids": [],
            }
        )

    assert repository.failed == [(7, "TimeoutError")]


@pytest.mark.asyncio
async def test_missing_contact_identity_skips_external_sync() -> None:
    """没有已验证客户联系身份时跳过，不把微信客服身份混用。"""
    repository = SyncRepositoryStub(identity=None)
    api = ContactApiStub()
    service = CustomerTagSyncService(repository, api)

    await service.handle(
        {
            "customer_id": 7,
            "add_tag_ids": [1],
            "remove_tag_ids": [],
        }
    )

    assert api.calls == []
    assert repository.completed == [7]
