"""唯一管理员的运营总览与只读诊断页面。"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol, cast

from fastapi import APIRouter, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from homestay_bot.domain.enums import EmployeeRole, RoomOperationalStatus
from homestay_bot.domain.errors import OperationRefused
from homestay_bot.routes.admin_form_csrf import (
    PROPERTY_CSRF_FAMILY,
    REMINDER_CSRF_FAMILY,
    consume_form_csrf,
    drop_legacy_session_key,
    issue_form_csrf,
)
from homestay_bot.routes.employee_auth import require_employee_session
from homestay_bot.services.admin_dashboard_service import WUHAN_TIMEZONE, Snapshot
from homestay_bot.services.admin_diagnostics_service import AuditPage, DiagnosticsSnapshot
from homestay_bot.services.admin_operations_service import (
    OperationsSnapshot,
    RoomOperationItem,
)
from homestay_bot.web import templates

router = APIRouter(prefix="/employee/admin")
logger = logging.getLogger(__name__)
_BULK_CSRF_ENTITY = 0


class AdminDashboardServicePort(Protocol):
    """定义后台路由所需的只读快照接口。"""

    async def snapshot(self, now: datetime | None = None) -> Snapshot:
        """返回指定观察时间的运营快照。"""


class AdminOperationsServicePort(Protocol):
    """定义待关注中心与房态工作台的只读快照接口。"""

    async def snapshot(
        self,
        now: datetime | None = None,
        *,
        horizon_days: int = 3,
        source_synced_at: datetime | None = None,
    ) -> OperationsSnapshot:
        """返回同一观察时间下的待办与房态快照。"""


class HealthServicePort(Protocol):
    """定义复用现有健康服务所需的最小接口。"""

    async def check(self) -> dict[str, str]:
        """返回受控组件状态。"""


class AdminDiagnosticsServicePort(Protocol):
    """定义诊断详情和审计分页所需的安全服务接口。"""

    async def snapshot(self) -> DiagnosticsSnapshot:
        """返回服务端生成的安全诊断 view model。"""

    async def list_audits(self, *, page: int, page_size: int = 20) -> AuditPage:
        """返回稳定倒序的安全审计分页。"""


_CHECK_LABELS = {
    "database": "数据库连接",
    "worker_heartbeat": "后台任务处理",
    "wecom_polling": "企业微信消息同步",
    "hostex_reconcile": "订单对账轮询",
    "hostex_webhook": "百居易回调接收",
    "context_maintenance": "对话上下文维护",
    "lifecycle_scheduler": "入住提醒调度",
    "task_lifecycle": "任务生命周期巡检",
    "configuration": "必要配置",
    "web_search": "联网信息查询",
    "wecom_contact_sync": "客户联系同步",
}
_STATUS_PRESENTATION = {
    "ok": ("正常", "success"),
    "unknown": ("待确认", "neutral"),
    "stale": ("已超时", "warning"),
    "error": ("异常", "danger"),
    "incomplete": ("未完整配置", "warning"),
    "not_configured": ("未配置", "neutral"),
    "never_received": ("从未收到", "warning"),
    "degraded": ("降级", "warning"),
}
# 并非每一项「非正常」都会拉低整体状态：这些组合在 routes/health.py 里被显式
# 视为可接受，列出来是为了让人知道它们不是降级原因。这份表与 health.py 的判定
# 由 tests/unit/test_health.py::test_benign_check_states_do_not_degrade 校验一致。
_BENIGN_CHECK_STATES = frozenset(
    {
        ("hostex_webhook", "not_configured"),
        ("task_lifecycle", "not_configured"),
        ("web_search", "unknown"),
        ("wecom_contact_sync", "ok"),
        ("wecom_contact_sync", "not_configured"),
    }
)
# 每一项非正常状态都要给出「这是什么意思、现在该做什么」。只显示一个状态词，
# 等于把判断成本原样丢给看页面的人；而这些判断依据都在代码里，本来就能写清楚。
# 没有可靠去处时如实说明要去哪里核对，不编造看起来能点的入口。
_CHECK_GUIDANCE: dict[tuple[str, str], tuple[str, str, str]] = {
    ("hostex_webhook", "never_received"): (
        "回调密钥已配置，但从未收到过任何一次百居易推送。订单目前全部依靠"
        "每 15 分钟一次的对账轮询补回，实时性受影响。",
        "到百居易后台核对回调地址是否填写为 https://akros.icu/webhooks/hostex，"
        "以及密钥是否与本系统一致。若确定不使用回调，在接口设置里勾选"
        "「明确清除 Webhook Secret」，该项会转为「未配置」且不再降级。",
        "/employee/admin/settings",
    ),
    ("hostex_webhook", "stale"): (
        "曾经收到过回调，但最近一个对账周期内没有新的推送。",
        "先看订单对账轮询是否正常；若轮询正常而回调长期无推送，到百居易后台核对回调配置。",
        "",
    ),
    ("hostex_reconcile", "stale"): (
        "订单对账轮询超过预期周期没有成功。房态与订单可能不是最新的。",
        "检查百居易访问令牌是否有效、接口是否可达；操作记录页的「外部接口调用」"
        "会按端点列出最近一次调用的时间与成败。",
        "/employee/admin/diagnostics/audits",
    ),
    ("web_search", "unknown"): (
        "本次启动后还没有发生过真实联网查询，因此系统不声称这项能力可用——"
        "这是「尚未验证」，不是故障。",
        "发生一次需要联网的客人问题后会自动转为正常；"
        "也可以在 AI 调试台发起一次含实时信息的提问来验证。",
        "/employee/admin/debug",
    ),
    ("web_search", "error"): (
        "最近一次联网查询失败。涉及天气、交通等实时信息的问题会退回安全回复。",
        "在接口设置中确认联网检索相关配置与配额。",
        "/employee/admin/settings",
    ),
    ("wecom_contact_sync", "not_configured"): (
        "未配置客户联系功能，因此不做客户标签同步。这是配置选择，不是故障。",
        "如果需要客户标签同步，在接口设置中补齐企业微信客户联系相关配置。",
        "/employee/admin/settings",
    ),
    ("database", "error"): (
        "数据库连接探测失败，后台大部分功能会不可用。",
        "这是最高优先级故障，需要检查数据库服务与连接配置。",
        "",
    ),
    ("worker_heartbeat", "stale"): (
        "后台任务处理器超过预期时间没有心跳，排队中的发送与同步可能已经停滞。",
        "查看待处理与失败任务数量；持续停滞需要重启应用进程。",
        "/employee/admin/diagnostics/audits",
    ),
    ("wecom_polling", "stale"): (
        "企业微信消息拉取超时，新的客人消息可能没有进入系统。",
        "确认企业微信相关配置与网络可达性。",
        "/employee/admin/settings",
    ),
    ("configuration", "incomplete"): (
        "必要配置尚未填写完整，部分能力不会启用。",
        "到接口设置逐项补齐标记为必填的配置。",
        "/employee/admin/settings",
    ),
}
_TASK_STATUS_PRESENTATION = {
    "failed": ("失败任务", "danger"),
    "pending": ("待处理任务", "warning"),
    "running": ("运行中任务", "neutral"),
}


def _clock(request: Request) -> datetime:
    """读取测试可注入时钟，生产使用当前 UTC 时间。"""
    provider = getattr(request.app.state, "admin_dashboard_clock", None)
    if callable(provider):
        return cast(Callable[[], datetime], provider)()
    return datetime.now(UTC)


def _dashboard_service(request: Request) -> AdminDashboardServicePort:
    """读取生命周期装配的总览服务。"""
    service = getattr(request.app.state, "admin_dashboard_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="运营总览服务尚未配置")
    return cast(AdminDashboardServicePort, service)


def _operations_service(request: Request) -> AdminOperationsServicePort:
    """读取生命周期装配的本地运营聚合服务。"""
    service = getattr(request.app.state, "admin_operations_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="运营工作台服务尚未配置")
    return cast(AdminOperationsServicePort, service)


def _health_service(request: Request) -> HealthServicePort:
    """直接复用应用健康服务，不发起内部 HTTP 请求。"""
    service = getattr(request.app.state, "health_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="系统诊断服务尚未配置")
    return cast(HealthServicePort, service)


def _diagnostics_service(request: Request) -> AdminDiagnosticsServicePort | None:
    """读取批次七诊断服务；测试兼容期允许回退既有健康展示。"""
    service = getattr(request.app.state, "admin_diagnostics_service", None)
    return cast(AdminDiagnosticsServicePort | None, service)


def _no_store(response: Response) -> Response:
    """禁止诊断和审计信息进入浏览器或代理缓存。"""
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


async def _require_admin(request: Request) -> None:
    """持续复核会话并显式限制唯一管理员角色。"""
    _, role = await require_employee_session(request)
    if role is not EmployeeRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅管理员可访问")


async def _safe_health(request: Request) -> dict[str, str]:
    """把健康探针异常收敛为降级状态，诊断页面自身保持可用。"""
    try:
        return await _health_service(request).check()
    except Exception as error:
        logger.warning("管理员健康检查失败：error_type=%s", type(error).__name__)
        return {"status": "degraded"}


@router.get("", response_class=HTMLResponse)
async def admin_dashboard(request: Request) -> Response:
    """展示不含客户身份、消息正文和门锁凭证的运营总览。"""
    await _require_admin(request)
    observed_at = _clock(request)
    snapshot_error: str | None = None
    try:
        snapshot = await _dashboard_service(request).snapshot(observed_at)
    except Exception as error:
        # 只记录异常类型，禁止把数据库错误正文或查询参数写入页面和日志。
        logger.warning("管理员总览读取失败：error_type=%s", type(error).__name__)
        aware_time = observed_at.replace(tzinfo=UTC) if observed_at.tzinfo is None else observed_at
        snapshot = Snapshot.empty(aware_time.astimezone(WUHAN_TIMEZONE).date())
        snapshot_error = "运营数据暂时不可用，当前显示安全空态。"
    # 「先处理」直接用运营快照里已经排好风险序的房间，不另建第四套待办投影。
    # 读失败时安静降级为空列表：工作台的其余部分仍然可用。
    attention_rooms: tuple[RoomOperationItem, ...] = ()
    try:
        operations = await _operations_service(request).snapshot(
            observed_at,
            # 必须与房间视图取同一个同步时间：不传就默认 None，快照会把每间房都
            # 判成来源过期，于是「先处理」整列变成「先确认百居易实时房态」且没有
            # 任何可点的去向——同一份数据在两个视图里给出互相矛盾的可信度。
            source_synced_at=getattr(
                request.app.state,
                "hostex_data_last_success",
                None,
            ),
        )
        attention_rooms = operations.attention_rooms
    except Exception as error:
        logger.warning("工作台先处理读取失败：error_type=%s", type(error).__name__)
    health = await _safe_health(request)
    return templates.TemplateResponse(
        request=request,
        name="admin/dashboard.html",
        context={
            "page_title": "运营总览",
            "active_nav": "dashboard",
            "snapshot": snapshot,
            "attention_rooms": attention_rooms,
            "health_degraded": snapshot_error is not None or health.get("status") != "ok",
            "error": snapshot_error,
        },
    )


@router.get("/attention", response_class=HTMLResponse)
async def admin_attention(request: Request) -> Response:
    """展示所有需要管理员采取行动的本地安全投影。"""
    await _require_admin(request)
    snapshot = await _operations_service(request).snapshot(_clock(request))
    drop_legacy_session_key(request, "attention_csrf")
    return templates.TemplateResponse(
        request=request,
        name="admin/attention.html",
        context={
            "page_title": "待我关注",
            "active_nav": "attention",
            "snapshot": snapshot,
            "csrf_token": await issue_form_csrf(
                request,
                family=REMINDER_CSRF_FAMILY,
                entity_id=_BULK_CSRF_ENTITY,
            ),
        },
    )


@router.post("/attention/resolve-reminders")
async def resolve_reminders(
    request: Request,
    csrf_token: str = Form(min_length=1, max_length=128),
    reminder_ids: Annotated[list[str] | None, Form()] = None,
) -> RedirectResponse:
    """把管理员确认过的人工跟进提醒批量标记为已处理。

    提醒原本没有出口状态：一旦转入人工跟进就只能停在「待我关注」里，即使它
    移交出去的那条任务早已处理完或被删除。这个入口是那条出路。
    """
    employee_id, role = await require_employee_session(request)
    if role is not EmployeeRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅管理员可访问")
    await consume_form_csrf(
        request,
        family=REMINDER_CSRF_FAMILY,
        entity_id=_BULK_CSRF_ENTITY,
        token=csrf_token,
    )
    service = getattr(request.app.state, "lifecycle_reminder_admin", None)
    if service is None:
        raise HTTPException(status_code=503, detail="提醒服务未就绪")
    # 每个勾选框携带它那一组的全部提醒编号，页面上显示的集合与提交的集合因此
    # 逐一对应，不需要服务端「按同样的条件再查一次」——那会让两次查询之间的
    # 变化悄悄改变处置范围。
    selected: list[int] = []
    for group in reminder_ids or []:
        for raw in group.split(","):
            value = raw.strip()
            if not value.isdigit():
                raise OperationRefused(
                    "提醒编号无法识别，请刷新页面后重试",
                    return_to="/employee/admin/attention",
                )
            selected.append(int(value))
    try:
        await service.resolve_many(selected, employee_id)
    except OperationRefused as refused:
        refused.return_to = "/employee/admin/attention"
        raise
    except LookupError as error:
        raise HTTPException(status_code=404, detail="提醒不存在") from error
    return RedirectResponse(
        "/employee/admin/attention",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/operations", response_class=HTMLResponse)
async def admin_operations(
    request: Request,
    days: Literal["3", "7", "14"] = Query("3"),
) -> Response:
    """展示房间近期入住事实、运营准备度与优先行动。"""
    await _require_admin(request)
    # 查询字符串天然是文本；通过白名单后再转换为运营服务需要的整数。
    horizon_days = int(days)
    snapshot = await _operations_service(request).snapshot(
        _clock(request),
        horizon_days=horizon_days,
        source_synced_at=getattr(
            request.app.state,
            "hostex_data_last_success",
            None,
        ),
    )
    # 房态就地修改复用房源写操作家族的一次性令牌，逐房间绑定，跨房间重放会失败。
    room_csrf_tokens = {
        room.property_id: await issue_form_csrf(
            request,
            family=PROPERTY_CSRF_FAMILY,
            entity_id=room.property_id,
        )
        for room in snapshot.rooms
    }
    return templates.TemplateResponse(
        request=request,
        name="admin/operations.html",
        context={
            "page_title": "房态与运营",
            "active_nav": "operations",
            "snapshot": snapshot,
            "room_csrf_tokens": room_csrf_tokens,
            "room_statuses": list(RoomOperationalStatus),
        },
    )


@router.get("/diagnostics", response_class=HTMLResponse)
async def admin_diagnostics(request: Request) -> Response:
    """优先展示偏离正常状态的诊断结果，降级时仍返回 HTTP 200。"""
    await _require_admin(request)
    service = _diagnostics_service(request)
    diagnostic_snapshot: DiagnosticsSnapshot | None = None
    if service is not None:
        try:
            diagnostic_snapshot = await service.snapshot()
            health = diagnostic_snapshot.health
        except Exception as error:
            logger.warning("管理员诊断详情失败：error_type=%s", type(error).__name__)
            health = {"status": "degraded"}
    else:
        health = await _safe_health(request)
    attention_checks: list[dict[str, str]] = []
    healthy_check_count = 0
    # 遍历健康检查本身而不是标签表：健康检查新增字段时，缺标签只会显示成原始键，
    # 不会像 v1.15.0 那样被整项静默丢掉，让降级原因在页面上无处可见。
    for key, value in health.items():
        if key == "status":
            continue
        if value == "ok":
            healthy_check_count += 1
            continue
        status_label, tone = _STATUS_PRESENTATION.get(value, ("需检查", "warning"))
        meaning, action, action_url = _CHECK_GUIDANCE.get(
            (key, value), ("", "", "")
        )
        attention_checks.append(
            {
                "label": _CHECK_LABELS.get(key, key),
                "status_label": status_label,
                "tone": tone,
                "meaning": meaning,
                "action": action,
                "action_url": action_url,
                "benign": "1" if (key, value) in _BENIGN_CHECK_STATES else "",
            }
        )

    attention_tasks: list[dict[str, str | int]] = []
    if diagnostic_snapshot is not None and diagnostic_snapshot.job_status_counts:
        for key, (label, tone) in _TASK_STATUS_PRESENTATION.items():
            count = diagnostic_snapshot.job_status_counts.get(key, 0)
            if count <= 0:
                continue
            if key != "pending":
                attention_tasks.append({"label": label, "count": count, "tone": tone})
                continue
            # 「待处理」同时包含已到期没人做的和排在未来还没轮到的。生产上 49 条
            # 全属后者（入住提醒排在次日到两周后），一个告警色徽标显示总数会被
            # 读成积压。拆开：到期的才告警，排期的是中性事实。
            due = diagnostic_snapshot.pending_due_count
            if due is None:
                # 到期数未知时不做拆分：宁可沿用原来的告警，也不把未知说成排期。
                attention_tasks.append(
                    {"label": label, "count": count, "tone": tone}
                )
                continue
            scheduled = max(0, count - due)
            if due > 0:
                attention_tasks.append(
                    {"label": "已到期待处理", "count": due, "tone": "warning"}
                )
            if scheduled > 0:
                attention_tasks.append(
                    {"label": "排期待发", "count": scheduled, "tone": "neutral"}
                )

    started_at = getattr(request.app.state, "started_at", None)
    response = templates.TemplateResponse(
        request=request,
        name="admin/diagnostics.html",
        context={
            "page_title": "系统诊断",
            "active_nav": "diagnostics",
            "overall_ok": health.get("status") == "ok",
            "health_degraded": health.get("status") != "ok",
            "health_available": (
                diagnostic_snapshot.health_available
                if diagnostic_snapshot is not None
                else any(key != "status" for key in health)
            ),
            "attention_checks": attention_checks,
            "healthy_check_count": healthy_check_count,
            "attention_tasks": attention_tasks,
            "started_at": (
                diagnostic_snapshot.started_at if diagnostic_snapshot else started_at
            ),
            "diagnostics": diagnostic_snapshot,
        },
    )
    return _no_store(response)


@router.get("/diagnostics/audits", response_class=HTMLResponse)
async def admin_audits(
    request: Request,
) -> Response:
    """展示不含 details、目标编号或客户身份的稳定审计分页。"""
    await _require_admin(request)
    raw_page = request.query_params.get("page", "1")
    if (
        not raw_page.isascii()
        or not raw_page.isdigit()
        or not 1 <= len(raw_page) <= 6
        or not 1 <= int(raw_page) <= 100000
    ):
        return _no_store(
            HTMLResponse(
                "操作记录页码无效，请返回诊断页重试。",
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )
        )
    page = int(raw_page)
    service = _diagnostics_service(request)
    if service is None:
        raise HTTPException(status_code=503, detail="系统诊断服务尚未配置")
    try:
        audit_page = await service.list_audits(page=page, page_size=20)
    except Exception as error:
        logger.warning("管理员审计列表失败：error_type=%s", type(error).__name__)
        audit_page = AuditPage(
            items=(),
            page=page,
            page_size=20,
            has_previous=page > 1,
            has_next=False,
        )
    # 诊断页的「对账轮询已超时」指引到这里查看最近的调用结果，此前这一页只读
    # AuditLog，根本没有外部调用记录——承诺兑现不了。补上真正的来源。
    external_calls = ()
    if hasattr(service, "list_external_calls"):
        external_calls = await service.list_external_calls(limit=20)
    response = templates.TemplateResponse(
        request=request,
        name="admin/audits.html",
        context={
            "page_title": "操作记录",
            "active_nav": "diagnostics",
            "audit_page": audit_page,
            "external_calls": external_calls,
        },
    )
    return _no_store(response)
