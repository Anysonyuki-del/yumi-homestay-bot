from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.services.customer_admin_service import CustomerAdminService
from homestay_bot.services.sensitive_data import SensitiveDataCipher


def employee(role=EmployeeRole.ADMIN):
    """构造启用员工。"""
    return SimpleNamespace(id=1, role=role, is_active=True)


class CustomerAdminRepositoryStub:
    """记录 CRM 管理服务写操作。"""

    def __init__(self, cipher) -> None:
        """初始化一个带加密手机号的客户。"""
        self.customer = SimpleNamespace(
            id=7,
            display_name="测试客户",
            phone_ciphertext=cipher.encrypt("13800138000"),
            note="老客户",
        )
        self.tag_calls: list[tuple[int, list[int], int]] = []
        self.note_calls: list[tuple[int, str, int]] = []
        self.summary_calls: list[dict[str, object]] = []
        self.deleted_summaries: list[tuple[int, int]] = []
        self.merge_calls: list[tuple[int, int, bool]] = []
        self.manual_merge_calls: list[tuple[int, int, int]] = []
        self.sync_completed: list[int] = []

    async def list_customers(self, query):
        """返回固定客户列表。"""
        return [self.customer]

    async def customer_detail(self, customer_id):
        """返回客户、标签和摘要详情。"""
        assert customer_id == 7
        return {
            "customer": self.customer,
            "tags": [],
            "selected_tag_ids": [],
            "summary": None,
            "merge_suggestions": [],
        }

    async def replace_tags(self, customer_id, tag_ids, administrator_id):
        """记录标签替换并返回增删差异。"""
        self.tag_calls.append((customer_id, tag_ids, administrator_id))
        return [2], [3], 17

    async def update_note(self, customer_id, note, administrator_id):
        """记录备注更新。"""
        self.note_calls.append((customer_id, note, administrator_id))

    async def update_summary(self, **fields):
        """记录摘要更正。"""
        self.summary_calls.append(fields)

    async def delete_summary(self, customer_id, administrator_id):
        """记录摘要删除。"""
        self.deleted_summaries.append((customer_id, administrator_id))

    async def review_merge(self, suggestion_id, administrator_id, accepted):
        """记录合并确认或拒绝。"""
        self.merge_calls.append(
            (suggestion_id, administrator_id, accepted)
        )

    async def create_manual_merge_suggestion(
        self,
        source_customer_id,
        target_customer_id,
        administrator_id,
    ):
        """记录手动合并建议并返回固定编号。"""
        self.manual_merge_calls.append(
            (
                source_customer_id,
                target_customer_id,
                administrator_id,
            )
        )
        return 23

    async def has_verified_contact_identity(self, customer_id):
        """返回客户存在已验证企业微信客户联系身份。"""
        return True

    async def mark_sync_completed(self, customer_id):
        """记录无需外部同步。"""
        self.sync_completed.append(customer_id)


class JobQueueStub:
    """记录标签同步后台任务。"""

    def __init__(self) -> None:
        """初始化任务列表。"""
        self.items: list[dict[str, object]] = []

    async def enqueue(self, job_type, payload, *, dedupe_key):
        """记录同步任务。"""
        self.items.append(
            {
                "job_type": job_type,
                "payload": payload,
                "dedupe_key": dedupe_key,
            }
        )


class ManualMergeRepositoryStub:
    """只暴露手动合并所需编号接口，禁止读取客户敏感资料。"""

    def __init__(self) -> None:
        """初始化手动合并调用记录。"""
        self.manual_merge_calls: list[tuple[int, int, int]] = []

    async def create_manual_merge_suggestion(
        self,
        source_customer_id,
        target_customer_id,
        administrator_id,
    ):
        """记录三个编号并返回固定建议编号。"""
        self.manual_merge_calls.append(
            (
                source_customer_id,
                target_customer_id,
                administrator_id,
            )
        )
        return 23

    def __getattr__(self, name):
        """任何额外仓储访问都视为读取了不必要的客户资料。"""
        raise AssertionError(f"手动合并不应访问仓储属性：{name}")


@pytest.mark.asyncio
async def test_detail_masks_phone_and_never_returns_plaintext() -> None:
    """CRM 页面只能得到脱敏手机号。"""
    cipher = SensitiveDataCipher(Fernet.generate_key().decode("ascii"))
    service = CustomerAdminService(
        CustomerAdminRepositoryStub(cipher),
        cipher,
        JobQueueStub(),
        tag_sync_enabled=False,
    )

    detail = await service.get_detail(7, employee())

    assert detail["masked_phone"] == "138****8000"
    assert "13800138000" not in str(detail)


@pytest.mark.asyncio
async def test_staff_cannot_modify_customer() -> None:
    """普通员工不能修改客户备注、标签、摘要或合并决定。"""
    cipher = SensitiveDataCipher(Fernet.generate_key().decode("ascii"))
    service = CustomerAdminService(
        CustomerAdminRepositoryStub(cipher),
        cipher,
        JobQueueStub(),
        tag_sync_enabled=True,
    )

    with pytest.raises(PermissionError):
        await service.update_note(7, "备注", employee(EmployeeRole.STAFF))


@pytest.mark.asyncio
async def test_local_tags_succeed_when_contact_sync_not_configured() -> None:
    """未配置客户联系 Secret 时仍保存本地标签且不创建同步任务。"""
    cipher = SensitiveDataCipher(Fernet.generate_key().decode("ascii"))
    repository = CustomerAdminRepositoryStub(cipher)
    jobs = JobQueueStub()
    service = CustomerAdminService(
        repository,
        cipher,
        jobs,
        tag_sync_enabled=False,
    )

    await service.set_tags(7, [1, 2], employee())

    assert repository.tag_calls == [(7, [1, 2], 1)]
    assert jobs.items == []
    assert repository.sync_completed == [7]


@pytest.mark.asyncio
async def test_linked_customer_enqueues_internal_tag_diff_only() -> None:
    """已关联客户只把内部标签差异放入可重试任务。"""
    cipher = SensitiveDataCipher(Fernet.generate_key().decode("ascii"))
    repository = CustomerAdminRepositoryStub(cipher)
    jobs = JobQueueStub()
    service = CustomerAdminService(
        repository,
        cipher,
        jobs,
        tag_sync_enabled=True,
    )

    await service.set_tags(7, [1, 2], employee())

    assert jobs.items[0]["job_type"] == "customer_tag_sync"
    assert jobs.items[0]["payload"] == {
        "customer_id": 7,
        "add_tag_ids": [2],
        "remove_tag_ids": [3],
    }


@pytest.mark.asyncio
async def test_staff_cannot_create_manual_merge() -> None:
    """普通员工不能创建手动合并建议。"""
    cipher = SensitiveDataCipher(Fernet.generate_key().decode("ascii"))
    repository = ManualMergeRepositoryStub()
    service = CustomerAdminService(
        repository,
        cipher,
        JobQueueStub(),
        tag_sync_enabled=False,
    )

    with pytest.raises(PermissionError):
        await service.create_manual_merge(
            7,
            8,
            employee(EmployeeRole.STAFF),
        )

    assert repository.manual_merge_calls == []


@pytest.mark.asyncio
async def test_manual_merge_rejects_same_customer() -> None:
    """手动合并不能把客户档案合并到自身。"""
    cipher = SensitiveDataCipher(Fernet.generate_key().decode("ascii"))
    repository = ManualMergeRepositoryStub()
    service = CustomerAdminService(
        repository,
        cipher,
        JobQueueStub(),
        tag_sync_enabled=False,
    )

    with pytest.raises(ValueError):
        await service.create_manual_merge(7, 7, employee())

    assert repository.manual_merge_calls == []


@pytest.mark.asyncio
async def test_admin_creates_manual_merge_with_identifiers_only() -> None:
    """管理员仅提交三个编号并得到待复核建议编号。"""
    cipher = SensitiveDataCipher(Fernet.generate_key().decode("ascii"))
    repository = ManualMergeRepositoryStub()
    service = CustomerAdminService(
        repository,
        cipher,
        JobQueueStub(),
        tag_sync_enabled=False,
    )

    suggestion_id = await service.create_manual_merge(7, 8, employee())

    assert suggestion_id == 23
    assert repository.manual_merge_calls == [(7, 8, 1)]
