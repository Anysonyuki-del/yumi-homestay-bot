import re
from types import SimpleNamespace

import pytest
from admin_auth_helpers import configure_admin_auth, login_admin
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.routes.customers import router as customers_router
from homestay_bot.routes.employee_auth import router as employee_auth_router
from homestay_bot.services.customer_admin_service import CustomerCard
from homestay_bot.services.customer_errors import (
    CustomerConflictError,
    CustomerNotFoundError,
    CustomerPermissionError,
)


class CustomerAdminStub:
    """提供安全客户详情并记录所有管理员写操作。"""

    def __init__(self) -> None:
        """初始化客户、标签、摘要和合并建议。"""
        self.card = CustomerCard(
            id=7,
            display_name="测试客户",
            note="ROUTE_SOURCE_SECRET_NOTE",
            masked_phone="138****8000",
        )
        self.target_card = CustomerCard(
            id=8,
            display_name="订单客户",
            note="ROUTE_TARGET_SECRET_NOTE",
            masked_phone="139****9000",
        )
        self.tags = [
            SimpleNamespace(id=1, name="VIP"),
            SimpleNamespace(id=2, name="老客户"),
        ]
        self.summary = SimpleNamespace(
            short_summary="偏好安静",
            long_summary="曾咨询武汉旅游安排",
            unresolved_items=["待确认到达时间"],
        )
        self.suggestion = SimpleNamespace(
            id=9,
            reason="verified_phone",
            source_customer_id=7,
            target_customer_id=8,
        )
        self.tag_calls: list[tuple[int, list[int], int]] = []
        self.note_calls: list[tuple[int, str, int]] = []
        self.summary_calls: list[dict[str, object]] = []
        self.delete_calls: list[tuple[int, int]] = []
        self.merge_calls: list[tuple[int, int, bool]] = []
        self.manual_merge_calls: list[tuple[int, int, int]] = []
        self.list_calls: list[tuple[str | None, int, int, int]] = []
        self.manual_merge_error: Exception | None = None

    async def list_customers(
        self,
        query,
        administrator,
        *,
        offset: int = 0,
        limit: int = 50,
    ):
        """按测试查询返回安全客户卡片并记录管理员编号。"""
        self._require_admin(administrator)
        self.list_calls.append((query, administrator.id, offset, limit))
        if query == "订单":
            return [self.card, self.target_card]
        return [self.card] * (limit if offset == 50 else 1)

    async def get_detail(self, customer_id, administrator):
        """返回不包含手机号明文和密文的客户详情。"""
        self._require_admin(administrator)
        if customer_id != 7:
            raise CustomerNotFoundError("客户不存在")
        return {
            "customer": self.card,
            "masked_phone": self.card.masked_phone,
            "tags": self.tags,
            "selected_tag_ids": [1],
            "summary": self.summary,
            "merge_suggestions": [self.suggestion],
        }

    async def get_merge_detail(self, suggestion_id, administrator):
        """返回合并前人工对比所需的脱敏信息。"""
        self._require_admin(administrator)
        if suggestion_id != 9:
            raise CustomerNotFoundError("合并建议不存在")
        return {
            "suggestion": self.suggestion,
            "source": SimpleNamespace(
                id=7,
                display_name="测试客户",
                identity_count=1,
                conversation_count=2,
                order_count=0,
                task_count=1,
            ),
            "target": SimpleNamespace(
                id=8,
                display_name="订单客户",
                identity_count=1,
                conversation_count=0,
                order_count=3,
                task_count=2,
            ),
        }

    async def create_manual_merge(
        self,
        source_customer_id,
        target_customer_id,
        administrator,
    ):
        """记录手动建议并稳定模拟自合并领域错误。"""
        self._require_admin(administrator)
        if self.manual_merge_error is not None:
            raise self.manual_merge_error
        if source_customer_id == target_customer_id:
            raise CustomerConflictError("不能将客户档案合并到自身")
        self.manual_merge_calls.append(
            (source_customer_id, target_customer_id, administrator.id)
        )
        return 9

    async def set_tags(self, customer_id, tag_ids, administrator):
        """记录标签多选提交。"""
        self._require_admin(administrator)
        self.tag_calls.append((customer_id, tag_ids, administrator.id))

    async def update_note(self, customer_id, note, administrator):
        """记录备注提交。"""
        self._require_admin(administrator)
        self.note_calls.append((customer_id, note, administrator.id))

    async def update_summary(
        self,
        customer_id,
        administrator,
        *,
        short_summary,
        long_summary,
        unresolved_items,
    ):
        """记录摘要更正提交。"""
        self._require_admin(administrator)
        self.summary_calls.append(
            {
                "customer_id": customer_id,
                "administrator_id": administrator.id,
                "short_summary": short_summary,
                "long_summary": long_summary,
                "unresolved_items": unresolved_items,
            }
        )

    async def delete_summary(self, customer_id, administrator):
        """记录摘要删除提交。"""
        self._require_admin(administrator)
        self.delete_calls.append((customer_id, administrator.id))

    async def review_merge(
        self,
        suggestion_id,
        administrator,
        *,
        accepted,
    ):
        """记录合并确认或拒绝。"""
        self._require_admin(administrator)
        self.merge_calls.append(
            (suggestion_id, administrator.id, accepted)
        )

    @staticmethod
    def _require_admin(administrator) -> None:
        """模拟服务层的管理员复核。"""
        if administrator.role is not EmployeeRole.ADMIN:
            raise CustomerPermissionError("只有管理员可以管理客户")


def build_client(
    role: EmployeeRole,
) -> tuple[TestClient, CustomerAdminStub]:
    """创建带签名会话的客户管理测试应用。"""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="customer-test-secret")
    app.include_router(employee_auth_router)
    app.include_router(customers_router)
    configure_admin_auth(app, role)
    customers = CustomerAdminStub()
    app.state.customer_admin_service = customers
    return TestClient(app), customers


def login(client: TestClient) -> None:
    """通过独立账号密码表单建立版本化员工会话。"""
    login_admin(client, next_path="/employee/customers")


def detail_csrf(client: TestClient, customer_id: int = 7) -> str:
    """读取客户详情页的一次性 CSRF 令牌。"""
    response = client.get(f"/employee/customers/{customer_id}")
    assert response.status_code == 200
    return re.search(
        r'name="csrf_token" value="([^"]+)"',
        response.text,
    ).group(1)


def merge_csrf(client: TestClient, suggestion_id: int = 9) -> str:
    """读取合并确认页的一次性 CSRF 令牌。"""
    response = client.get(f"/employee/customers/merge/{suggestion_id}")
    assert response.status_code == 200
    return re.search(
        r'name="csrf_token" value="([^"]+)"',
        response.text,
    ).group(1)


def test_staff_cannot_open_customer_crm() -> None:
    """普通员工不能进入客户列表或伪造合并请求。"""
    client, customers = build_client(EmployeeRole.STAFF)
    login(client)

    index = client.get("/employee/customers")
    detail = client.get(
        "/employee/customers/7",
        params={"merge_query": "订单"},
    )
    merge_detail = client.get("/employee/customers/merge/9")
    manual_merge = client.post(
        "/employee/customers/7/merge/manual",
        data={"target_customer_id": "8", "csrf_token": "forged"},
    )
    merge = client.post(
        "/employee/customers/merge/9/confirm",
        data={"csrf_token": "forged"},
    )

    assert index.status_code == 403
    assert detail.status_code == 403
    assert merge_detail.status_code == 403
    assert manual_merge.status_code == 403
    assert merge.status_code == 403
    assert customers.list_calls == []
    assert customers.manual_merge_calls == []
    assert customers.merge_calls == []


def test_admin_sees_masked_customer_and_multi_select_tags() -> None:
    """详情页只显示脱敏手机号，并支持标签多选。"""
    client, _ = build_client(EmployeeRole.ADMIN)
    login(client)

    index = client.get("/employee/customers")
    detail = client.get("/employee/customers/7")

    assert index.status_code == 200
    assert "测试客户" in index.text
    assert "138****8000" in detail.text
    assert "13800138000" not in detail.text
    assert "phone_ciphertext" not in detail.text
    assert detail.text.count('name="tag_ids"') == 2
    assert "/employee/customers/merge/9" in detail.text


def test_admin_can_update_tags_note_and_summary_with_one_time_csrf() -> None:
    """客户写操作使用一次性令牌并向服务层传递清理前的表单值。"""
    client, customers = build_client(EmployeeRole.ADMIN)
    login(client)

    tag_token = detail_csrf(client)
    tags = client.post(
        "/employee/customers/7/tags",
        data={
            "tag_ids": ["1", "2"],
            "csrf_token": tag_token,
        },
        follow_redirects=False,
    )
    replay = client.post(
        "/employee/customers/7/tags",
        data={"tag_ids": ["1"], "csrf_token": tag_token},
        follow_redirects=False,
    )
    note_token = detail_csrf(client)
    note = client.post(
        "/employee/customers/7/note",
        data={"note": " 新备注 ", "csrf_token": note_token},
        follow_redirects=False,
    )
    summary_token = detail_csrf(client)
    summary = client.post(
        "/employee/customers/7/summary",
        data={
            "short_summary": "短摘要",
            "long_summary": "长期摘要",
            "unresolved_items": "待确认时间\n待确认停车",
            "csrf_token": summary_token,
        },
        follow_redirects=False,
    )

    assert tags.status_code == 303
    assert replay.status_code == 409
    assert note.status_code == 303
    assert summary.status_code == 303
    assert customers.tag_calls == [(7, [1, 2], 1)]
    assert customers.note_calls == [(7, " 新备注 ", 1)]
    assert customers.summary_calls[0]["unresolved_items"] == [
        "待确认时间",
        "待确认停车",
    ]


def test_admin_can_delete_summary_and_review_merge() -> None:
    """管理员可以删除摘要，并明确确认或拒绝合并建议。"""
    client, customers = build_client(EmployeeRole.ADMIN)
    login(client)

    delete_token = detail_csrf(client)
    deleted = client.post(
        "/employee/customers/7/summary/delete",
        data={"csrf_token": delete_token},
        follow_redirects=False,
    )
    confirm_token = merge_csrf(client)
    confirmed = client.post(
        "/employee/customers/merge/9/confirm",
        data={"csrf_token": confirm_token},
        follow_redirects=False,
    )
    reject_token = merge_csrf(client)
    rejected = client.post(
        "/employee/customers/merge/9/reject",
        data={"csrf_token": reject_token},
        follow_redirects=False,
    )

    assert deleted.status_code == 303
    assert confirmed.status_code == 303
    assert rejected.status_code == 303
    assert customers.delete_calls == [(7, 1)]
    assert customers.merge_calls == [(9, 1, True), (9, 1, False)]


def test_admin_searches_masked_manual_merge_targets_from_detail() -> None:
    """详情页保留既有内容，并只显示排除来源后的脱敏目标卡片。"""
    client, customers = build_client(EmployeeRole.ADMIN)
    login(client)

    detail = client.get(
        "/employee/customers/7",
        params={"merge_query": "订单"},
    )

    assert detail.status_code == 200
    assert "AI 客户摘要" in detail.text
    assert "合并客户档案" in detail.text
    assert "来源档案：测试客户（客户 #7）" in detail.text
    assert "目标档案：订单客户（客户 #8）" in detail.text
    assert "139****9000" in detail.text
    assert "13900139000" not in detail.text
    assert "phone_ciphertext" not in detail.text
    assert "目标档案：测试客户（客户 #7）" not in detail.text
    assert customers.list_calls == [("订单", 1, 0, 50)]


def test_customer_list_uses_bounded_pagination_and_preserves_search() -> None:
    """客户第二页必须有查询上限，导航链接应保留搜索词。"""
    client, customers = build_client(EmployeeRole.ADMIN)
    login(client)

    response = client.get("/employee/customers?query=老客户&page=2")

    assert response.status_code == 200
    assert customers.list_calls == [("老客户", 1, 50, 51)]
    assert (
        'href="/employee/customers?query=%E8%80%81%E5%AE%A2%E6%88%B7&amp;page=1"'
        in response.text
    )
    assert (
        'href="/employee/customers?query=%E8%80%81%E5%AE%A2%E6%88%B7&amp;page=3"'
        in response.text
    )


def test_manual_merge_uses_one_time_csrf_and_redirects_to_review() -> None:
    """创建建议消耗详情页令牌，并跳转到现有二次复核页。"""
    client, customers = build_client(EmployeeRole.ADMIN)
    login(client)
    token = detail_csrf(client)

    created = client.post(
        "/employee/customers/7/merge/manual",
        data={"target_customer_id": "8", "csrf_token": token},
        follow_redirects=False,
    )
    replay = client.post(
        "/employee/customers/7/merge/manual",
        data={"target_customer_id": "8", "csrf_token": token},
        follow_redirects=False,
    )
    forged = client.post(
        "/employee/customers/7/merge/manual",
        data={"target_customer_id": "8", "csrf_token": "forged"},
        follow_redirects=False,
    )

    assert created.status_code == 303
    assert created.headers["location"] == "/employee/customers/merge/9"
    assert replay.status_code == 409
    assert forged.status_code == 409
    assert customers.manual_merge_calls == [(7, 8, 1)]


def test_manual_merge_self_target_returns_stable_conflict() -> None:
    """自合并领域错误稳定返回冲突，且令牌已经被消耗。"""
    client, customers = build_client(EmployeeRole.ADMIN)
    login(client)
    token = detail_csrf(client)

    response = client.post(
        "/employee/customers/7/merge/manual",
        data={"target_customer_id": "7", "csrf_token": token},
        follow_redirects=False,
    )
    replay = client.post(
        "/employee/customers/7/merge/manual",
        data={"target_customer_id": "8", "csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "不能将客户档案合并到自身"
    assert replay.status_code == 409
    assert customers.manual_merge_calls == []


def test_unknown_manual_merge_error_returns_redacted_server_error() -> None:
    """未知异常返回统一错误，不能向管理员泄露 SQL 或秘密文本。"""
    client, customers = build_client(EmployeeRole.ADMIN)
    login(client)
    customers.manual_merge_error = RuntimeError(
        "SELECT phone_ciphertext FROM customers; SECRET_DATABASE_VALUE"
    )
    token = detail_csrf(client)

    response = client.post(
        "/employee/customers/7/merge/manual",
        data={"target_customer_id": "8", "csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "客户管理操作失败"
    assert "phone_ciphertext" not in response.text
    assert "SECRET_DATABASE_VALUE" not in response.text


@pytest.mark.parametrize(
    "error, secret",
    [
        (KeyError("SECRET_KEY"), "SECRET_KEY"),
        (UnicodeError("SECRET_UNICODE_VALUE"), "SECRET_UNICODE_VALUE"),
        (
            UnicodeDecodeError(
                "utf-8",
                b"SECRET_DECODE_VALUE",
                0,
                1,
                "SECRET_DECODE_REASON",
            ),
            "SECRET_DECODE_REASON",
        ),
    ],
)
def test_builtin_error_subclasses_return_redacted_server_error(
    error: Exception,
    secret: str,
) -> None:
    """内建异常即使继承查找或值错误，也不能碰撞领域映射。"""
    client, customers = build_client(EmployeeRole.ADMIN)
    login(client)
    customers.manual_merge_error = error
    token = detail_csrf(client)

    response = client.post(
        "/employee/customers/7/merge/manual",
        data={"target_customer_id": "8", "csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "客户管理操作失败"
    assert secret not in response.text


@pytest.mark.parametrize(
    "error, expected_status",
    [
        (CustomerPermissionError("只有管理员可以管理客户"), 403),
        (CustomerNotFoundError("目标客户不存在"), 404),
        (CustomerConflictError("客户状态冲突"), 409),
    ],
)
def test_customer_domain_errors_keep_stable_status(
    error: Exception,
    expected_status: int,
) -> None:
    """CRM 专用异常继续映射为稳定且可读的页面状态。"""
    client, customers = build_client(EmployeeRole.ADMIN)
    login(client)
    customers.manual_merge_error = error
    token = detail_csrf(client)

    response = client.post(
        "/employee/customers/7/merge/manual",
        data={"target_customer_id": "8", "csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == expected_status
    assert response.json()["detail"] == str(error)


def test_merge_review_explains_direction_and_safe_association_counts() -> None:
    """复核页明确来源停用、目标保留，并只展示聚合关联数量。"""
    client, _ = build_client(EmployeeRole.ADMIN)
    login(client)

    response = client.get("/employee/customers/merge/9")

    assert response.status_code == 200
    assert "来源档案将停用" in response.text
    assert "目标档案将保留" in response.text
    assert "会话 2 个" in response.text
    assert "订单 3 笔" in response.text
    assert "任务 2 项" in response.text
    assert "13800138000" not in response.text
    assert "13900139000" not in response.text
    assert "phone_ciphertext" not in response.text
    assert "ROUTE_SOURCE_SECRET_NOTE" not in response.text
    assert "ROUTE_TARGET_SECRET_NOTE" not in response.text
    assert "138****8000" not in response.text
    assert "139****9000" not in response.text
