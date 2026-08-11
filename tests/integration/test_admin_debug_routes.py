"""验证管理员 AI 调试路由的权限、CSRF、缓存和脱敏边界。"""

import re
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date

from admin_auth_helpers import configure_admin_auth, login_admin
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from homestay_bot.domain.enums import EmployeeRole, Language
from homestay_bot.integrations.deepseek_client import AssistantToolTrace
from homestay_bot.routes.admin_debug import router as admin_debug_router
from homestay_bot.routes.employee_auth import router as auth_router
from homestay_bot.services.admin_debug_service import (
    AdminDebugRateLimiter,
    AdminDebugService,
    DebugPreviewResult,
    DebugProperty,
)


class DebugServiceStub:
    """返回固定安全调试结果并记录命令。"""

    def __init__(self) -> None:
        """初始化调用记录。"""
        self.commands = []

    async def list_properties(self):
        """返回房源安全投影。"""
        return (DebugProperty(11, "江汉路一号房"),)

    async def preview(self, command):
        """保存命令并返回不触发生产写操作的固定结果。"""
        self.commands.append(command)
        return DebugPreviewResult(
            reply_text="当前有房，请稍候确认具体房型。",
            intent="availability_query",
            confidence=0.9,
            knowledge_gap=False,
            knowledge_gap_topic=None,
            tool_trace=(
                AssistantToolTrace(
                    name="search_availability",
                    succeeded=True,
                    duration_ms=5,
                    check_in_date=date(2026, 8, 12),
                    check_out_date=date(2026, 8, 13),
                ),
            ),
            selected_property_id=11,
            selected_property_title="江汉路一号房",
            check_in_date=date(2026, 8, 12),
            check_out_date=date(2026, 8, 13),
            staff_confirmation_required=True,
            staff_confirmation_reason="availability_result_confirmation",
            task_suggestion=None,
            faq_candidate=True,
            faq_candidate_id=23,
            faq_canonical_question="民宿是否提供<script>alert(1)</script>停车位？",
            faq_category="停车",
            revision=7,
        )


class GetMustNotPreviewStub(DebugServiceStub):
    """验证 GET 只能读取本地房源投影，不能触发模型或外联。"""

    async def preview(self, command):
        """GET 若误触发预览则立即失败。"""
        raise AssertionError("GET 不得触发模型或网络")


class MissingFaqStub(DebugServiceStub):
    """返回没有 FAQ 候选字段的安全结果。"""

    async def preview(self, command):
        """清空可选 FAQ 字段以验证模板 fallback。"""
        result = await super().preview(command)
        return replace(
            result,
            faq_candidate=False,
            faq_candidate_id=None,
            faq_canonical_question=None,
            faq_category=None,
        )


class RoutePropertyStub:
    """为真实 route service 提供本地房源投影。"""

    async def list_debug_properties(self):
        """返回一个启用房源。"""
        return (DebugProperty(11, "江汉路一号房"),)

    async def get_debug_property(self, property_id: int):
        """按编号读取固定房源。"""
        return DebugProperty(11, "江汉路一号房") if property_id == 11 else None


class RouteAuditStub:
    """接受安全审计元数据。"""

    async def record_debug_preview(self, **details):
        """测试无需持久化审计。"""


class RouteAssistantStub:
    """返回固定预览用于真实限频服务。"""

    async def respond(self, **kwargs):
        """生成固定客服决定。"""
        from homestay_bot.integrations.deepseek_client import AssistantDecision

        return AssistantDecision(
            reply_text="仅预览",
            language=Language.ZH,
            intent="faq",
            confidence=0.9,
        )


class RouteRegistryStub:
    """提供固定 revision 助手。"""

    @asynccontextmanager
    async def acquire(self):
        """租用一次固定 bundle。"""
        yield type("Bundle", (), {"revision": 1, "assistant": RouteAssistantStub()})()


def build_client(role: EmployeeRole = EmployeeRole.ADMIN) -> TestClient:
    """装配真实管理员认证与调试路由。"""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="debug-route-secret")
    app.include_router(auth_router)
    app.include_router(admin_debug_router)
    configure_admin_auth(app, role)
    app.state.admin_debug_service = DebugServiceStub()
    return TestClient(app)


def csrf_token(html: str) -> str:
    """提取服务端签发的一次性调试 nonce。"""
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def test_debug_page_is_admin_only_no_store_and_explains_side_effects() -> None:
    """页面仅管理员可见，并说明费用、零发送和零订单修改。"""
    client = build_client()
    anonymous = client.get(
        "/employee/admin/debug",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )
    assert anonymous.status_code == 303
    login_admin(client, next_path="/employee/admin/debug")

    response = client.get("/employee/admin/debug")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "少量模型费用" in response.text
    assert "不会向客人发送消息" in response.text
    assert "不会修改订单" in response.text
    staff = build_client(EmployeeRole.STAFF)
    login_admin(staff, next_path="/employee/admin/debug")
    assert staff.get("/employee/admin/debug").status_code == 403


def test_debug_post_consumes_atomic_csrf_and_never_echoes_invalid_input() -> None:
    """POST 必须消费一次性 nonce，重放和超长输入都不得回显原文。"""
    client = build_client()
    login_admin(client, next_path="/employee/admin/debug")
    page = client.get("/employee/admin/debug")
    token = csrf_token(page.text)
    data = {
        "csrf_token": token,
        "question": "明天有房吗？",
        "language": "zh",
        "property_id": "11",
        "check_in_date": "2026-08-12",
        "check_out_date": "2026-08-13",
    }

    response = client.post("/employee/admin/debug", data=data)
    replay = client.post("/employee/admin/debug", data=data)
    secret = "UID-SECRET-RAW" * 200
    fresh = csrf_token(client.get("/employee/admin/debug").text)
    invalid = client.post(
        "/employee/admin/debug",
        data={**data, "csrf_token": fresh, "question": secret},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "当前有房" in response.text
    assert "FAQ 候选" in response.text
    assert "候选编号 23" in response.text
    assert "民宿是否提供&lt;script&gt;alert(1)&lt;/script&gt;停车位？" in response.text
    assert "<script>alert(1)</script>" not in response.text
    assert "停车" in response.text
    assert replay.status_code == 409
    assert replay.headers["cache-control"] == "no-store"
    assert invalid.status_code == 422
    assert secret not in invalid.text


def test_debug_get_never_calls_preview_or_external_connection() -> None:
    """打开页面只能获取本地表单投影，不能执行任何连接测试。"""
    client = build_client()
    client.app.state.admin_debug_service = GetMustNotPreviewStub()
    login_admin(client, next_path="/employee/admin/debug")

    response = client.get("/employee/admin/debug")

    assert response.status_code == 200


def test_debug_route_enforces_real_per_admin_rate_limit() -> None:
    """两个有效 nonce 也不能绕过同一管理员的模型费用限额。"""
    client = build_client()
    client.app.state.admin_debug_service = AdminDebugService(
        registry=RouteRegistryStub(),
        properties=RoutePropertyStub(),
        audits=RouteAuditStub(),
        limiter=AdminDebugRateLimiter(limit=1),
        local_date_provider=lambda: date(2026, 8, 11),
    )
    login_admin(client, next_path="/employee/admin/debug")
    data = {
        "question": "几点入住？",
        "language": "zh",
        "property_id": "11",
        "check_in_date": "2026-08-12",
        "check_out_date": "2026-08-13",
    }
    first_token = csrf_token(client.get("/employee/admin/debug").text)
    first = client.post(
        "/employee/admin/debug", data={**data, "csrf_token": first_token}
    )
    second_token = csrf_token(client.get("/employee/admin/debug").text)
    second = client.post(
        "/employee/admin/debug", data={**data, "csrf_token": second_token}
    )

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers["cache-control"] == "no-store"


def test_debug_template_falls_back_when_faq_fields_are_missing() -> None:
    """无候选时模板必须显示受控 fallback，不能渲染 None。"""
    client = build_client()
    client.app.state.admin_debug_service = MissingFaqStub()
    login_admin(client, next_path="/employee/admin/debug")
    token = csrf_token(client.get("/employee/admin/debug").text)

    response = client.post(
        "/employee/admin/debug",
        data={
            "csrf_token": token,
            "question": "几点入住？",
            "language": "zh",
        },
    )

    assert response.status_code == 200
    assert "<dt>FAQ 候选</dt><dd>否</dd>" in response.text
    assert response.text.count("<dd>未提供</dd>") >= 2
    assert "None" not in response.text
