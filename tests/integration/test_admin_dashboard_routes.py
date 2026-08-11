import asyncio
from concurrent.futures import CancelledError as FutureCancelledError
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest
from admin_auth_helpers import configure_admin_auth, login_admin
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.routes.admin import router as admin_router
from homestay_bot.routes.employee_auth import router as auth_router
from homestay_bot.services.admin_dashboard_service import Snapshot
from homestay_bot.services.admin_diagnostics_service import AuditPage, DiagnosticsSnapshot


class DashboardStub:
    """返回不含任何客户信息的固定总览。"""

    async def snapshot(self, now: datetime | None = None) -> Snapshot:
        """构造空数据快照。"""
        return Snapshot.empty(date(2026, 8, 11))


class FailingDashboardStub:
    """模拟数据库读取失败且异常正文包含敏感文本。"""

    async def snapshot(self, now: datetime | None = None) -> Snapshot:
        """稳定抛出测试异常。"""
        raise RuntimeError("database-secret-detail")


class CancelledDashboardStub:
    """模拟请求任务被上游取消。"""

    async def snapshot(self, now: datetime | None = None) -> Snapshot:
        """抛出取消信号，路由必须继续向上传播。"""
        raise asyncio.CancelledError


class HealthStub:
    """返回可控健康状态。"""

    def __init__(self, status: str = "degraded") -> None:
        """保存总体状态。"""
        self.status = status

    async def check(self) -> dict[str, str]:
        """返回内部组件的枚举状态，不含原始异常。"""
        return {
            "status": self.status,
            "database": "ok",
            "worker_heartbeat": "stale",
            "configuration": "incomplete",
        }


class DiagnosticsStub:
    """提供不包含 raw 对象的诊断快照和安全审计分页。"""

    async def snapshot(self) -> DiagnosticsSnapshot:
        """返回服务端已生成的脱敏复制报告。"""
        return DiagnosticsSnapshot(
            health={"status": "degraded", "database": "ok"},
            job_status_counts={"pending": 2},
            recent_job_error_codes=("timeout",),
            started_at=datetime(2026, 8, 11, tzinfo=UTC),
            version="1.2.3",
            configuration_revision=7,
            report_text="YuMi 系统诊断报告（已脱敏）\n版本：1.2.3",
        )

    async def list_audits(self, *, page: int, page_size: int = 20) -> AuditPage:
        """返回稳定倒序的安全审计视图。"""
        items = (
            SimpleNamespace(
                id=9,
                action="admin_debug_preview",
                target_type="admin_debug",
                created_at=datetime(2026, 8, 11, tzinfo=UTC),
            ),
        )
        return AuditPage(
            items=items,
            page=page,
            page_size=page_size,
            has_previous=page > 1,
            has_next=True,
        )


class FailingHealthStub:
    """模拟健康服务异常且正文含敏感内容。"""

    async def check(self) -> dict[str, str]:
        """稳定抛出测试异常。"""
        raise RuntimeError("health-secret-detail")


class ShellParser(HTMLParser):
    """从真实渲染 HTML 收集外壳结构和属性。"""

    def __init__(self) -> None:
        """初始化标签记录。"""
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """记录开始标签及属性。"""
        self.tags.append((tag, dict(attrs)))

    def matching(self, tag: str, **attrs: str) -> list[dict[str, str | None]]:
        """返回具备全部指定属性的标签。"""
        return [
            found
            for found_tag, found in self.tags
            if found_tag == tag and all(found.get(key) == value for key, value in attrs.items())
        ]


class MustChangeVerifier:
    """模拟仍处于首次改密阶段的活动管理员。"""

    async def get_active_admin(self, admin_id: int, employee_id: int) -> object:
        """返回必须先修改密码的版本化会话。"""
        return SimpleNamespace(
            employee_id=employee_id,
            role=EmployeeRole.ADMIN,
            is_active=True,
            session_version=1,
            must_change_password=True,
        )


def build_client() -> TestClient:
    """装配真实认证路由与后台页面。"""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="dashboard-test-secret")
    app.include_router(auth_router)
    app.include_router(admin_router)
    configure_admin_auth(app, EmployeeRole.ADMIN)
    app.state.admin_dashboard_service = DashboardStub()
    app.state.health_service = HealthStub()
    app.state.started_at = datetime(2026, 8, 11, tzinfo=UTC)
    return TestClient(app)


def test_dashboard_requires_login_and_first_password_change() -> None:
    """总览沿用统一会话门控，匿名用户不得读取页面。"""
    client = build_client()

    response = client.get(
        "/employee/admin",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/employee/login?")

    login_admin(client, next_path="/employee/admin")
    client.app.state.employee_access_verifier = MustChangeVerifier()
    forced_change = client.get("/employee/admin", follow_redirects=False)
    assert forced_change.status_code == 303
    assert forced_change.headers["location"] == "/employee/account"


def test_dashboard_renders_unified_safe_shell_for_empty_data() -> None:
    """空数据页面仍应包含统一导航、移动 viewport 和安全空状态。"""
    client = build_client()
    login_admin(client, next_path="/employee/admin")

    response = client.get("/employee/admin")
    parser = ShellParser()
    parser.feed(response.text)

    assert response.status_code == 200
    assert parser.matching("html", lang="zh-CN")
    assert parser.matching("meta", name="viewport")
    assert parser.matching("aside", id="admin-drawer", **{"data-drawer": None})
    assert parser.matching("script", src="/static/admin.js")
    assert parser.matching("a", href="/employee/admin", **{"aria-current": "page"})
    assert "总览" in response.text
    assert "任务中心" in response.text
    assert "今日暂无入住" in response.text
    assert "系统当前处于降级状态" in response.text
    for secret in ("uid-secret", "message-secret", "lock-secret", "password", "secret"):
        assert secret not in response.text.lower()
    assert "<pre" not in response.text.lower()


def test_diagnostics_keeps_http_200_when_health_is_degraded() -> None:
    """诊断页面应明确降级但保持可访问，且不输出原始 JSON。"""
    client = build_client()
    login_admin(client, next_path="/employee/admin/diagnostics")

    response = client.get("/employee/admin/diagnostics")
    parser = ShellParser()
    parser.feed(response.text)

    assert response.status_code == 200
    assert parser.matching("aside", id="admin-drawer", **{"data-drawer": None})
    assert parser.matching("script", src="/static/admin.js")
    assert parser.matching(
        "a",
        href="/employee/admin/diagnostics",
        **{"aria-current": "page"},
    )
    assert "系统诊断" in response.text
    assert "需要关注" in response.text
    assert "{&quot;status&quot;" not in response.text
    assert "worker_heartbeat" not in response.text
    assert "incomplete" not in response.text
    assert "admin.js" in response.text


def test_dashboard_query_failure_renders_safe_degraded_page(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """总览查询失败时应返回安全空态，不能让诊断入口一并失效。"""
    client = build_client()
    login_admin(client, next_path="/employee/admin")
    client.app.state.admin_dashboard_service = FailingDashboardStub()

    with caplog.at_level("WARNING", logger="homestay_bot.routes.admin"):
        response = client.get("/employee/admin")

    assert response.status_code == 200
    assert "运营数据暂时不可用" in response.text
    assert "今日暂无入住" in response.text
    assert "系统当前处于降级状态" in response.text
    assert "database-secret-detail" not in response.text
    assert "database-secret-detail" not in caplog.text


def test_staff_cannot_access_admin_dashboard_or_diagnostics() -> None:
    """普通员工对两个老板页面均应得到 403。"""
    client = build_client()
    configure_admin_auth(client.app, EmployeeRole.STAFF)
    login_admin(client, next_path="/employee/admin")

    dashboard = client.get("/employee/admin")
    diagnostics = client.get("/employee/admin/diagnostics")

    assert dashboard.status_code == 403
    assert diagnostics.status_code == 403


def test_dashboard_does_not_swallow_request_cancellation() -> None:
    """取消信号不是普通降级异常，必须继续向上传播。"""
    client = build_client()
    login_admin(client, next_path="/employee/admin")
    client.app.state.admin_dashboard_service = CancelledDashboardStub()

    # TestClient 的跨线程 portal 会把 asyncio 取消转换成 concurrent.futures 取消。
    with pytest.raises(FutureCancelledError):
        client.get("/employee/admin")


def test_health_failure_logs_only_type_and_returns_degraded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """健康检查异常应安全记录类型，并继续渲染降级页面。"""
    client = build_client()
    login_admin(client, next_path="/employee/admin/diagnostics")
    client.app.state.health_service = FailingHealthStub()

    with caplog.at_level("WARNING", logger="homestay_bot.routes.admin"):
        response = client.get("/employee/admin/diagnostics")

    assert response.status_code == 200
    assert "系统当前处于降级状态" in response.text
    assert "error_type=RuntimeError" in caplog.text
    assert "health-secret-detail" not in caplog.text
    assert "health-secret-detail" not in response.text


def test_diagnostics_detail_and_audit_page_use_safe_server_view_models() -> None:
    """详情和审计页应 no-store，复制报告不得依赖浏览器过滤 raw 对象。"""
    client = build_client()
    client.app.state.admin_diagnostics_service = DiagnosticsStub()
    login_admin(client, next_path="/employee/admin/diagnostics")

    diagnostics = client.get("/employee/admin/diagnostics")
    audits = client.get("/employee/admin/diagnostics/audits", params={"page": 2})

    assert diagnostics.status_code == 200
    assert diagnostics.headers["cache-control"] == "no-store"
    assert "revision 7" in diagnostics.text
    assert "YuMi 系统诊断报告（已脱敏）" in diagnostics.text
    assert "data-copy-target" in diagnostics.text
    assert audits.status_code == 200
    assert audits.headers["cache-control"] == "no-store"
    assert "admin_debug_preview" in audits.text
    assert "上一页" in audits.text
    assert "下一页" in audits.text
    for secret in ("UID-SECRET", "RAW-MESSAGE", "https://", "token=", "LOCK-SECRET"):
        assert secret not in diagnostics.text
        assert secret not in audits.text

    invalid_query = client.get(
        "/employee/admin/diagnostics/audits",
        params={"page": "UID-SECRET?token=RAW"},
    )
    assert invalid_query.status_code == 422
    assert invalid_query.headers["cache-control"] == "no-store"
    assert "UID-SECRET" not in invalid_query.text
    assert "token=" not in invalid_query.text
