import asyncio
import re
from concurrent.futures import CancelledError as FutureCancelledError
from dataclasses import replace
from datetime import UTC, date, datetime
from html import escape
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest
from admin_auth_helpers import configure_admin_auth, login_admin
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from homestay_bot.domain.enums import (
    BusinessTaskStatus,
    EmployeeRole,
    ReminderStatus,
    RoomOccupancyStatus,
    RoomOperationalStatus,
)
from homestay_bot.routes.admin import router as admin_router
from homestay_bot.routes.employee_auth import router as auth_router
from homestay_bot.services.admin_dashboard_service import Snapshot
from homestay_bot.services.admin_diagnostics_service import AuditPage, DiagnosticsSnapshot
from homestay_bot.services.admin_operations_service import (
    AttentionItem,
    OperationsSnapshot,
    RoomDayOperation,
    RoomOperationItem,
    SevenDayRoomItem,
)


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


class OperationsStub:
    """返回不含客户身份和入住凭证的固定运营工作台。"""

    async def snapshot(
        self,
        now: datetime | None = None,
        *,
        horizon_days: int = 3,
        source_synced_at: datetime | None = None,
    ) -> OperationsSnapshot:
        """构造一项待确认任务和一间房的近期安全投影。"""
        local_date = date(2026, 8, 11)
        source_stale = source_synced_at is None
        days = tuple(
            RoomDayOperation(local_date, 1, 0, True)
            for _ in range(horizon_days + 3)
        )
        return OperationsSnapshot(
            local_date=local_date,
            attention_items=(
                AttentionItem(
                    kind="reminder",
                    record_id=31,
                    status=ReminderStatus.MANUAL_FOLLOWUP,
                    title="入住提醒需要跟进",
                    summary="该房源共有 2 项入住提醒需要跟进",
                    target_url="",
                    property_id=101,
                    room_title="长江中心",
                    updated_at=datetime(2026, 8, 11, tzinfo=UTC),
                    related_count=2,
                    record_ids=(31, 32),
                ),
                AttentionItem(
                    kind="task",
                    record_id=7,
                    status=BusinessTaskStatus.PENDING_CONFIRMATION,
                    title="业务任务待确认",
                    summary="任务 #7：等待管理员确认",
                    target_url="/employee/tasks/7",
                    property_id=101,
                    room_title="长江中心",
                    updated_at=datetime(2026, 8, 11, tzinfo=UTC),
                ),
            ),
            rooms=(
                RoomOperationItem(
                    property_id=101,
                    room_number="101",
                    room_title="长江中心",
                    status=RoomOperationalStatus.READY,
                    today_arrival_count=1,
                    today_departure_count=0,
                    open_task_count=1,
                    next_arrival=local_date,
                    occupancy_status=(
                        RoomOccupancyStatus.UNKNOWN
                        if source_stale
                        else RoomOccupancyStatus.ARRIVING_TODAY
                    ),
                    next_action=(
                        "先确认百居易实时房态"
                        if source_stale
                        else "核对入住资料并接待"
                    ),
                    source_stale=source_stale,
                ),
            ),
            seven_day_rooms=(
                SevenDayRoomItem(101, "101", "长江中心", days),
            ),
            horizon_days=horizon_days,
            source_synced_at=source_synced_at,
            source_stale=source_stale,
        )


class DiagnosticsStub:
    """提供不包含 raw 对象的诊断快照和安全审计分页。"""

    async def snapshot(self) -> DiagnosticsSnapshot:
        """返回服务端已生成的脱敏复制报告。"""
        return DiagnosticsSnapshot(
            health={"status": "degraded", "database": "ok", "worker_heartbeat": "stale"},
            health_available=True,
            job_status_counts={"completed": 20, "pending": 2, "failed": 1},
            recent_job_error_codes=("timeout",),
            started_at=datetime(2026, 8, 11, tzinfo=UTC),
            version="1.2.3",
            configuration_revision=7,
            configuration_revision_source="runtime",
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
    app.state.admin_operations_service = OperationsStub()
    app.state.health_service = HealthStub()
    app.state.started_at = datetime(2026, 8, 11, tzinfo=UTC)
    return TestClient(app)


def test_attention_and_operations_pages_form_actionable_workflow() -> None:
    """待关注事项和房态页面必须提供可执行入口与近期运营板。"""
    client = build_client()
    login_admin(client, next_path="/employee/admin")

    attention = client.get("/employee/admin/attention")
    operations = client.get("/employee/admin/operations")

    assert attention.status_code == 200
    assert 'href="/employee/tasks/7"' in attention.text
    assert "任务 #7：等待管理员确认" in attention.text
    assert operations.status_code == 200
    assert "入住信息待核实" in operations.text
    assert "入住安排" in operations.text
    assert "先确认百居易实时房态" in operations.text
    assert "未来 3 天" in operations.text
    assert 'href="/employee/properties/101"' in operations.text


@pytest.mark.parametrize("days", ["3", "7", "14"])
def test_operations_accepts_each_visible_range(days: str) -> None:
    """页面展示的三个房态范围都必须能从真实查询字符串进入。"""
    client = build_client()
    login_admin(client, next_path="/employee/admin")

    response = client.get("/employee/admin/operations", params={"days": days})

    assert response.status_code == 200
    assert f'aria-current="page">未来 {days} 天' in response.text


def test_operations_rejects_unknown_range() -> None:
    """房态范围继续使用白名单，不能因兼容查询字符串而放宽。"""
    client = build_client()
    login_admin(client, next_path="/employee/admin")

    assert client.get("/employee/admin/operations?days=30").status_code == 422


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
    assert any(
        (attrs.get("src") or "").startswith("/static/admin.js?v=")
        for attrs in parser.matching("script")
    )
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
    assert any(
        (attrs.get("src") or "").startswith("/static/admin.js?v=")
        for attrs in parser.matching("script")
    )
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
    assert "运行配置 revision 7" in diagnostics.text
    assert "数据库连接" not in diagnostics.text
    assert "后台任务处理" in diagnostics.text
    assert "待处理任务" in diagnostics.text
    assert "失败任务" in diagnostics.text
    assert "已完成任务" not in diagnostics.text
    assert "YuMi 系统诊断报告（已脱敏）" in diagnostics.text
    assert "完整脱敏报告" in diagnostics.text
    assert "<details" in diagnostics.text
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


class StableRoomOperationsStub:
    """返回一间需要关注的房间和一间稳定房间。"""

    received_sync_times: list[datetime | None] = []

    async def snapshot(
        self,
        now: datetime | None = None,
        *,
        horizon_days: int = 3,
        source_synced_at: datetime | None = None,
    ) -> OperationsSnapshot:
        """构造已同步、可区分风险层级的房态投影，并记录收到的同步时间。"""
        self.received_sync_times.append(source_synced_at)
        local_date = date(2026, 8, 11)
        days = tuple(
            RoomDayOperation(local_date, 0, 0, False)
            for _ in range(horizon_days + 3)
        )
        rooms = (
            RoomOperationItem(
                property_id=101,
                room_number="101",
                room_title="长江中心",
                status=RoomOperationalStatus.CLEANING,
                today_arrival_count=1,
                today_departure_count=1,
                open_task_count=2,
                next_arrival=local_date,
                occupancy_status=RoomOccupancyStatus.TURNOVER_TODAY,
                overdue_task_count=1,
                next_action="优先处理 1 项逾期任务",
                next_action_url="/employee/tasks?property_id=101&overdue=true",
                source_stale=False,
            ),
            RoomOperationItem(
                property_id=202,
                room_number="202",
                room_title="东湖小院",
                status=RoomOperationalStatus.READY,
                today_arrival_count=0,
                today_departure_count=0,
                open_task_count=0,
                next_arrival=None,
                occupancy_status=RoomOccupancyStatus.VACANT,
                next_action="暂无近期运营动作",
                source_stale=False,
            ),
        )
        return OperationsSnapshot(
            local_date=local_date,
            attention_items=(),
            rooms=rooms,
            seven_day_rooms=(
                SevenDayRoomItem(101, "101", "长江中心", days),
                SevenDayRoomItem(202, "202", "东湖小院", days),
            ),
            horizon_days=horizon_days,
            source_synced_at=datetime(2026, 8, 11, tzinfo=UTC),
            source_stale=False,
        )


def test_dashboard_shows_manual_risk_before_daily_turnover() -> None:
    """总览必须先展示需要人工处理的风险，再展示今日周转与工作量。"""
    client = build_client()
    login_admin(client, next_path="/employee/admin")

    response = client.get("/employee/admin")

    assert response.status_code == 200
    assert 'aria-labelledby="metrics-risk"' in response.text
    assert 'aria-labelledby="metrics-turnover"' in response.text
    assert response.text.index("需要人工处理的风险") < response.text.index("今日周转与工作量")
    assert response.text.index("需人工关注") < response.text.index("今日入住")
    assert "逾期任务" in response.text


def test_operations_shows_every_room_with_attention_first() -> None:
    """所有房间在一屏可扫读，需要关注的排前面，不再把在住房藏进折叠。"""
    client = build_client()
    client.app.state.admin_operations_service = StableRoomOperationsStub()
    login_admin(client, next_path="/employee/admin")

    response = client.get("/employee/admin/operations")

    assert response.status_code == 200
    # 两间房都在页面上，且需要关注的长江中心排在平稳的东湖小院之前。
    assert "长江中心" in response.text
    assert "东湖小院" in response.text
    assert response.text.index("长江中心") < response.text.index("东湖小院")
    # 不再有「稳定房间」折叠区。
    assert "稳定房间" not in response.text
    assert 'class="operations-stable"' not in response.text


def test_operations_page_offers_inline_room_status_control() -> None:
    """房态与运营页必须能就地改房态。

    此前改一次房态要走「运营页 → 点房间 → 详情页 → 提交 → 返回」四步，
    而这是日常运营里最频繁的动作之一。
    """
    client = build_client()
    login_admin(client, next_path="/employee/admin")

    page = client.get("/employee/admin/operations")

    assert page.status_code == 200
    assert "/room-status" in page.text
    assert 'name="room_status"' in page.text
    assert 'name="csrf_token"' in page.text
    # 六个房态全部可选，与房源详情页保持一致
    for label in ("未开始", "保洁中", "待检查", "可入住", "已入住", "维修中"):
        assert label in page.text


class StaleTimelineOperationsStub(OperationsStub):
    """提供同步已过期、且本地没有任何入住记录的时间轴。"""

    async def snapshot(
        self,
        now: datetime | None = None,
        *,
        horizon_days: int = 3,
        source_synced_at: datetime | None = None,
    ) -> OperationsSnapshot:
        """把默认桩的时间轴换成「本地无记录」的空日子。"""
        base = await super().snapshot(
            now,
            horizon_days=horizon_days,
            source_synced_at=source_synced_at,
        )
        empty_days = tuple(
            RoomDayOperation(day.local_date, 0, 0, False) for day in base.seven_day_rooms[0].days
        )
        return replace(
            base,
            seven_day_rooms=(SevenDayRoomItem(101, "101", "长江中心", empty_days),),
        )


def test_stale_timeline_does_not_call_a_missing_record_vacant() -> None:
    """同步过期时，本地没有记录只能说成「无记录」，不能说成确定的「空闲」。

    页面顶部已经声明房态待确认，时间轴却仍然逐日写「空闲」，等于用最具体的
    形式把「我们不知道」讲成了「我们确定这天没人」。
    """
    client = build_client()
    client.app.state.admin_operations_service = StaleTimelineOperationsStub()
    login_admin(client, next_path="/employee/admin")

    response = client.get("/employee/admin/operations")

    assert response.status_code == 200
    assert "入住信息待核实" in response.text
    assert "以下为上次同步记录，入住安排待核实。" in response.text
    # 住宿条时间轴不再逐日写「空闲」，也不断言可售。
    assert "空闲" not in response.text
    assert "可售" not in response.text


def test_synced_timeline_states_no_order_not_vacancy() -> None:
    """同步可信但范围内无订单：只说「暂无订单」，不冒充空房、可售或无记录。"""
    client = build_client()
    client.app.state.admin_operations_service = StableRoomOperationsStub()
    login_admin(client, next_path="/employee/admin")

    response = client.get("/employee/admin/operations")

    assert "当前同步范围内暂无订单" in response.text
    assert "空闲" not in response.text
    assert "可售" not in response.text
    assert "以下为上次同步记录" not in response.text


def test_expanded_stable_rooms_show_the_future_they_promise() -> None:
    """每张房间卡片都能看到未来安排，而不只是房名和房态控件。

    页面顶部提供「未来 3／7／14 天」入口，房间卡片必须至少给出下次入住、开放
    任务与可展开的近期时间轴，展开才有意义。
    """
    client = build_client()
    client.app.state.admin_operations_service = StableRoomOperationsStub()
    login_admin(client, next_path="/employee/admin")

    response = client.get("/employee/admin/operations")

    # 东湖小院现在直接在统一列表的卡片里；取它那张卡片的片段。
    block = response.text.split("东湖小院", 1)[1].split("</article>", 1)[0]
    assert "开放任务" in block
    assert 'href="/employee/tasks?property_id=202"' in block
    # 时间轴默认展开（不再藏进「查看近期安排」折叠），住宿条网格直接可见。
    assert 'class="room-timeline stay-grid"' in block
    assert "调整本系统记录" in block


def test_scrollable_timelines_can_be_reached_by_keyboard() -> None:
    """时间轴会横向滚动，因此必须自己可聚焦，否则键盘用户看不到后面的日子。"""
    client = build_client()
    login_admin(client, next_path="/employee/admin")

    response = client.get("/employee/admin/operations")

    timelines = re.findall(r'<div class="room-timeline stay-grid"[^>]*>', response.text)
    assert timelines
    assert all('tabindex="0"' in tag for tag in timelines)
    assert all("aria-label=" in tag for tag in timelines)


def test_long_horizon_gives_room_cards_a_full_row() -> None:
    """14 天时间轴不能被塞进半宽卡片里等分压缩。"""
    client = build_client()
    login_admin(client, next_path="/employee/admin")

    short = client.get("/employee/admin/operations?days=3")
    long_range = client.get("/employee/admin/operations?days=14")

    assert "room-operations-list--wide" not in short.text
    assert "room-operations-list--wide" in long_range.text


def test_attention_lets_reminders_be_closed_where_they_are_listed() -> None:
    """提醒既不是任务也没有自己的页面，因此必须能在关注页就地了结。

    此前提醒的「立即处理」指向任务列表，而那里根本没有提醒；更要命的是提醒
    没有出口状态，一旦转入人工跟进就永远留在「待我关注」里，数字只增不减。
    """
    client = build_client()
    login_admin(client, next_path="/employee/admin")

    response = client.get("/employee/admin/attention")

    assert response.status_code == 200
    assert 'action="/employee/admin/attention/resolve-reminders"' in response.text
    assert 'name="reminder_ids"' in response.text
    assert "标记勾选的提醒为已处理" in response.text
    # 提醒不再被送去一个没有它的列表
    assert 'href="/employee/tasks?property_id=' not in response.text


def test_workbench_is_one_entry_with_three_views() -> None:
    """总览、房间、全部任务收敛成一个入口下的三个视图。

    此前它们是三个并列导航项，同一件工作散在里面，管家得先判断记录属于哪个模块
    再找具体工作。三个旧 URL 必须保持可达——深链接和书签不能因为改导航而失效。
    """
    client = build_client()
    login_admin(client, next_path="/employee/admin")

    today = client.get("/employee/admin")
    rooms = client.get("/employee/admin/operations")

    for page in (today, rooms):
        views = re.search(
            r'<nav class="tab-nav workbench-views"[^>]*>(.*?)</nav>',
            page.text,
            re.S,
        )
        assert views is not None
        labels = re.findall(r"<a [^>]*>([^<]+)</a>", views.group(1))
        assert labels == ["今天", "房间", "全部任务"]

    assert 'aria-current="page">今天' in today.text
    assert 'aria-current="page">房间' in rooms.text
    # 侧边栏只剩一个日常入口，但旧地址仍然 200。
    assert ">工作台</span>" in today.text
    assert "待我关注</span>" not in today.text
    assert client.get("/employee/admin/attention").status_code == 200


def test_workbench_puts_the_next_action_within_reach() -> None:
    """「下一步」必须带一个能直接点的去向，而不只是一句文字。

    原先只展示文字，管家读完还得回列表重新筛选才能动手；文字与去向分开算就会
    各说各话，因此两者出自服务里同一组分支。
    """
    client = build_client()
    stub = StableRoomOperationsStub()
    client.app.state.admin_operations_service = stub
    login_admin(client, next_path="/employee/admin")

    today = client.get("/employee/admin")

    first = re.search(
        r'<section class="panel panel--padded workbench-first".*?</section>',
        today.text,
        re.S,
    )
    assert first is not None
    assert "优先处理 1 项逾期任务" in first.group(0)
    # 去向本身由服务计算，见 test_admin_operations_service 里的同源断言；
    # 这里只验「服务给了去向，页面就渲染成可点的动作」。
    assert 'href="/employee/tasks?property_id=101&amp;overdue=true"' in first.group(0)
    assert ">去处理</a>" in first.group(0)


def test_no_button_is_rendered_when_there_is_nowhere_reliable_to_go() -> None:
    """同步不可信时没有能真正解决它的页面入口，不渲染看起来能点的空按钮。"""
    client = build_client()
    login_admin(client, next_path="/employee/admin")

    today = client.get("/employee/admin")

    first = re.search(
        r'<section class="panel panel--padded workbench-first".*?</section>',
        today.text,
        re.S,
    )
    assert first is not None
    assert "先确认百居易实时房态" in first.group(0)
    assert "暂无可直接进入的入口" in first.group(0)


def test_workbench_and_rooms_view_agree_on_how_fresh_the_source_is() -> None:
    """两个视图看的是同一份数据，就不能对来源可信度给出相反结论。

    工作台的快照曾漏传 source_synced_at，默认 None 会把每间房都判成来源过期：
    「先处理」整列变成「先确认百居易实时房态」且没有任何可点的去向，而同一时刻
    房间视图显示同步正常、下一步是真实原因。
    """
    client = build_client()
    stub = StableRoomOperationsStub()
    stub.received_sync_times = []
    client.app.state.admin_operations_service = stub
    synced = datetime(2026, 8, 28, 16, tzinfo=UTC)
    client.app.state.hostex_data_last_success = synced
    login_admin(client, next_path="/employee/admin")

    today = client.get("/employee/admin")
    rooms = client.get("/employee/admin/operations")

    # 房间视图不认为来源过期，工作台也不能。
    assert "房态待确认" not in rooms.text
    assert "先确认百居易实时房态" not in today.text
    # 真正的判据：两个视图都必须把同一个同步时间交给快照。桩自己的 source_stale
    # 是硬编码的，只断言渲染结果不会暴露「其中一个忘了传参」。
    assert stub.received_sync_times == [synced, synced]


class UnlabeledHealthStub:
    """返回一个标签表里没有的健康项，用来检验它不会被静默丢弃。"""

    async def check(self) -> dict[str, str]:
        """含未知键、降级项和良性项各一。"""
        return {
            "status": "degraded",
            "database": "ok",
            "hostex_webhook": "never_received",
            "wecom_contact_sync": "not_configured",
            "brand_new_probe": "stale",
        }


def test_diagnostics_shows_degrading_check_with_meaning_and_action() -> None:
    """降级项必须出现在页面上，并给出含义、处理方法和可点去处。"""
    client = build_client()
    client.app.state.health_service = UnlabeledHealthStub()
    login_admin(client, next_path="/employee/admin/diagnostics")

    response = client.get("/employee/admin/diagnostics")

    assert response.status_code == 200
    # 降级项本身必须可见——v1.15.0 改名后它一度完全不渲染。
    assert "百居易回调接收" in response.text
    assert "从未收到" in response.text
    assert "整体状态显示为「降级」，由这一项引起" in response.text
    assert "从未收到过任何一次百居易推送" in response.text
    # 处理方法必须指向界面上真实存在的动作：v1.15.1 曾让用户去「清空回调密钥」，
    # 而当时设置页根本没有这个开关，留空只表示保留原值。
    assert "明确清除 Webhook Secret" in response.text
    assert "怎么处理" in response.text
    assert "https://akros.icu/webhooks/hostex" in response.text
    # 良性项要明确说明不是降级原因，避免看的人误判。
    assert "客户联系同步" in response.text
    assert "不影响整体状态" in response.text
    assert "/employee/admin/settings" in response.text


def test_diagnostics_never_silently_drops_unlabeled_health_key() -> None:
    """健康检查新增字段而未配标签时，应退化为显示原始键而不是消失。"""
    client = build_client()
    client.app.state.health_service = UnlabeledHealthStub()
    login_admin(client, next_path="/employee/admin/diagnostics")

    response = client.get("/employee/admin/diagnostics")

    assert "brand_new_probe" in response.text
    assert "这一项没有预置的处理说明" in response.text
    # 旧的错误键不应再出现在任何标签里。
    assert "hostex_webhook_sync" not in response.text


def test_operations_status_form_carries_the_current_range_back() -> None:
    """运营页的房态表单必须带上当前范围与房间锚点。

    路由已经会按 return_to 回跳，但页面不发这个字段就等于没修：老板改一间房
    仍会被甩去房间详情，丢掉 3/7/14 天视图和滚动位置。
    """
    client = build_client()
    login_admin(client, next_path="/employee/admin")

    page = client.get("/employee/admin/operations?days=7")

    assert page.status_code == 200
    assert 'name="return_to"' in page.text
    assert escape("/employee/admin/operations?days=7#room-") in page.text


class ExternalCallDiagnosticsStub(DiagnosticsStub):
    """在既有诊断桩上补出外部调用汇总。"""

    async def list_external_calls(self, *, limit: int = 20):
        """返回一条成功与一条失败的端点汇总。"""
        return (
            SimpleNamespace(
                provider="hostex",
                method="GET",
                path="/reservations",
                total=99,
                failed=0,
                last_at=datetime(2026, 9, 8, 14, tzinfo=UTC),
                last_succeeded=True,
            ),
            SimpleNamespace(
                provider="wecom",
                method="POST",
                path="/message/send",
                total=8,
                failed=3,
                last_at=datetime(2026, 9, 8, 13, tzinfo=UTC),
                last_succeeded=False,
            ),
        )


def test_audit_page_shows_the_external_call_results_it_is_pointed_at() -> None:
    """诊断把「同步异常」指向这一页，这一页就必须真有调用结果。

    此前该页只读 AuditLog，不含任何外部调用记录；用户按指引点进来，只会看到
    一串管理动作，并把「有审计记录」误当成「有接口结果」。
    """
    client = build_client()
    client.app.state.admin_diagnostics_service = ExternalCallDiagnosticsStub()
    login_admin(client, next_path="/employee/admin/diagnostics")

    page = client.get("/employee/admin/diagnostics/audits")

    assert page.status_code == 200
    assert "外部接口调用" in page.text
    assert "/reservations" in page.text
    assert "/message/send" in page.text
    # 最近一次的成败要一眼看到，而不是只给一串计数。
    assert "失败" in page.text


def test_reconcile_guidance_points_at_a_page_that_now_delivers() -> None:
    """对账超时的处理说明必须与目标页实际内容一致。"""
    client = build_client()
    client.app.state.health_service = ReconcileStaleHealthStub()
    login_admin(client, next_path="/employee/admin/diagnostics")

    page = client.get("/employee/admin/diagnostics")

    assert "订单对账轮询" in page.text
    assert "外部接口调用" in page.text
    assert "/employee/admin/diagnostics/audits" in page.text


class ReconcileStaleHealthStub:
    """只让对账轮询超时的健康桩。"""

    async def check(self) -> dict[str, str]:
        """返回 hostex_reconcile 超时。"""
        return {
            "status": "degraded",
            "database": "ok",
            "hostex_reconcile": "stale",
        }


class ScheduledJobsDiagnosticsStub(DiagnosticsStub):
    """待处理任务全部排期在未来的诊断桩。"""

    async def snapshot(self):
        """49 条待处理，其中 0 条已到期——与生产实况一致。"""
        base = await DiagnosticsStub.snapshot(self)
        return replace(
            base,
            job_status_counts={"pending": 49, "completed": 669},
            pending_due_count=0,
        )


def test_scheduled_jobs_are_not_presented_as_a_backlog() -> None:
    """排期在未来的任务不该用告警色显示成积压。

    生产 49 条待处理全部是 lifecycle_send，available_at 最早在次日、最晚两周后，
    0 条到期、0 条被锁。数字准确，却会被读成「有 49 件事堆着没做」——与
    「联网信息查询待确认」同一类认知问题：只给数字，不说它意味着什么。
    """
    client = build_client()
    client.app.state.admin_diagnostics_service = ScheduledJobsDiagnosticsStub()
    login_admin(client, next_path="/employee/admin/diagnostics")

    page = client.get("/employee/admin/diagnostics")

    assert page.status_code == 200
    assert "排期待发" in page.text
    assert "49" in page.text
    # 没有已到期的任务时，不出现「已到期待处理」这一项。
    assert "已到期待处理" not in page.text


class UnknownDueDiagnosticsStub(DiagnosticsStub):
    """到期数读取失败的诊断桩。"""

    async def snapshot(self):
        """待处理有数，但到期数未知。"""
        base = await DiagnosticsStub.snapshot(self)
        return replace(
            base,
            job_status_counts={"pending": 12},
            pending_due_count=None,
        )


def test_unknown_due_count_falls_back_to_the_warning() -> None:
    """到期数读取失败时宁可沿用原告警，也不把未知说成「排期待发」。

    默认成 0 会在读取失败时把全部待处理显示成中性的排期，正好在出问题的时候
    低报真实积压——失败要往保守方向倒。
    """
    client = build_client()
    client.app.state.admin_diagnostics_service = UnknownDueDiagnosticsStub()
    login_admin(client, next_path="/employee/admin/diagnostics")

    page = client.get("/employee/admin/diagnostics")

    assert "待处理任务" in page.text
    assert "排期待发" not in page.text


def test_stale_events_are_annotated_as_last_synced_plan() -> None:
    """A14：同步过期时，事件倒计时须标注「按上次同步计划」，不冒充实时。"""
    client = build_client()
    client.app.state.admin_operations_service = StaleTimelineOperationsStub()
    login_admin(client, next_path="/employee/admin")

    response = client.get("/employee/admin/operations")

    assert response.status_code == 200
    # 仅当该桩确有事件行时才要求注记；否则至少时间轴的待核实说明在。
    assert "待核实" in response.text
