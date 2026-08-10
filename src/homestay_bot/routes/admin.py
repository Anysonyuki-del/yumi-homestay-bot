"""唯一管理员的运营总览与只读诊断页面。"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol, cast

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.routes.employee_auth import require_employee_session
from homestay_bot.services.admin_dashboard_service import WUHAN_TIMEZONE, Snapshot
from homestay_bot.web import templates

router = APIRouter(prefix="/employee/admin")
logger = logging.getLogger(__name__)


class AdminDashboardServicePort(Protocol):
    """定义后台路由所需的只读快照接口。"""

    async def snapshot(self, now: datetime | None = None) -> Snapshot:
        """返回指定观察时间的运营快照。"""


class HealthServicePort(Protocol):
    """定义复用现有健康服务所需的最小接口。"""

    async def check(self) -> dict[str, str]:
        """返回受控组件状态。"""


_CHECK_LABELS = {
    "database": "数据库连接",
    "worker_heartbeat": "后台任务处理",
    "wecom_polling": "企业微信消息同步",
    "hostex_webhook_sync": "订单与房态同步",
    "context_maintenance": "对话上下文维护",
    "lifecycle_scheduler": "入住提醒调度",
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
    "degraded": ("降级", "warning"),
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


def _health_service(request: Request) -> HealthServicePort:
    """直接复用应用健康服务，不发起内部 HTTP 请求。"""
    service = getattr(request.app.state, "health_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="系统诊断服务尚未配置")
    return cast(HealthServicePort, service)


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
    health = await _safe_health(request)
    return templates.TemplateResponse(
        request=request,
        name="admin/dashboard.html",
        context={
            "page_title": "运营总览",
            "active_nav": "dashboard",
            "snapshot": snapshot,
            "health_degraded": snapshot_error is not None or health.get("status") != "ok",
            "error": snapshot_error,
        },
    )


@router.get("/diagnostics", response_class=HTMLResponse)
async def admin_diagnostics(request: Request) -> Response:
    """以固定中文标签展示现有健康服务结果，降级时仍返回 HTTP 200。"""
    await _require_admin(request)
    health = await _safe_health(request)
    checks: list[dict[str, str]] = []
    for key, label in _CHECK_LABELS.items():
        if key not in health:
            continue
        status_label, tone = _STATUS_PRESENTATION.get(
            health[key], ("需检查", "warning")
        )
        checks.append({"label": label, "status_label": status_label, "tone": tone})
    started_at = getattr(request.app.state, "started_at", None)
    return templates.TemplateResponse(
        request=request,
        name="admin/diagnostics.html",
        context={
            "page_title": "系统诊断",
            "active_nav": "diagnostics",
            "overall_ok": health.get("status") == "ok",
            "health_degraded": health.get("status") != "ok",
            "checks": checks,
            "started_at": started_at,
        },
    )
