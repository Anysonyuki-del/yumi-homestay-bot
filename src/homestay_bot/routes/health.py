from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.routes.employee_auth import require_employee_session

router = APIRouter()


class HealthServicePort(Protocol):
    """定义健康路由所需的分层检查接口。"""

    async def check(self) -> dict[str, str]:
        """返回数据库、worker 和配置状态。"""


class UnconfiguredHealthService:
    """表示应用尚未完成运行依赖装配。"""

    async def check(self) -> dict[str, str]:
        """明确报告未配置状态，避免进程存活被误判为系统健康。"""
        return {
            "status": "degraded",
            "database": "not_configured",
            "worker_heartbeat": "not_configured",
            "wecom_polling": "not_configured",
            "hostex_webhook_sync": "not_configured",
            "context_maintenance": "not_configured",
            "lifecycle_scheduler": "not_configured",
            "configuration": "incomplete",
            "web_search": "not_configured",
            "wecom_contact_sync": "not_configured",
        }


class OperationalHealthService:
    """以只读方式检查数据库、worker 心跳和配置完整性。"""

    def __init__(
        self,
        *,
        database_probe: Callable[[], Awaitable[bool]],
        heartbeat_getter: Callable[[], datetime | None],
        poll_heartbeat_getter: Callable[[], datetime | None],
        hostex_heartbeat_getter: Callable[[], datetime | None],
        context_heartbeat_getter: Callable[[], datetime | None],
        lifecycle_heartbeat_getter: Callable[[], datetime | None],
        configuration_ok: bool | Callable[[], bool],
        web_search_status_getter: Callable[[], str],
        contact_sync_configured: bool = False,
        heartbeat_max_age: timedelta = timedelta(minutes=2),
        poll_max_age: timedelta = timedelta(minutes=1),
        operational_max_age: timedelta | None = None,
        hostex_max_age: timedelta = timedelta(minutes=30),
        context_max_age: timedelta = timedelta(hours=2),
        lifecycle_max_age: timedelta = timedelta(minutes=30),
    ) -> None:
        """注入无副作用探针和心跳读取器。"""
        self._database_probe = database_probe
        self._heartbeat_getter = heartbeat_getter
        self._poll_heartbeat_getter = poll_heartbeat_getter
        self._hostex_heartbeat_getter = hostex_heartbeat_getter
        self._context_heartbeat_getter = context_heartbeat_getter
        self._lifecycle_heartbeat_getter = lifecycle_heartbeat_getter
        self._configuration_ok_getter = (
            configuration_ok if callable(configuration_ok) else lambda: configuration_ok
        )
        self._web_search_status_getter = web_search_status_getter
        self._contact_sync_configured = contact_sync_configured
        self._heartbeat_max_age = heartbeat_max_age
        self._poll_max_age = poll_max_age
        self._hostex_max_age = operational_max_age or hostex_max_age
        self._context_max_age = operational_max_age or context_max_age
        self._lifecycle_max_age = (
            operational_max_age or lifecycle_max_age
        )

    @staticmethod
    def _is_recent(
        value: datetime | None,
        max_age: timedelta,
    ) -> bool:
        """按 UTC 判断心跳是否仍在允许时间内。"""
        if not isinstance(value, datetime):
            return False
        age = datetime.now(UTC) - value
        return timedelta(0) <= age <= max_age

    async def check(self) -> dict[str, str]:
        """执行只读检查，不在健康接口创建任何外部资源。"""
        database_ok = await self._database_probe()
        heartbeat = self._heartbeat_getter()
        worker_ok = self._is_recent(
            heartbeat,
            self._heartbeat_max_age,
        )
        poll_heartbeat = self._poll_heartbeat_getter()
        poll_ok = self._is_recent(
            poll_heartbeat,
            self._poll_max_age,
        )
        hostex_ok = self._is_recent(
            self._hostex_heartbeat_getter(),
            self._hostex_max_age,
        )
        context_ok = self._is_recent(
            self._context_heartbeat_getter(),
            self._context_max_age,
        )
        lifecycle_ok = self._is_recent(
            self._lifecycle_heartbeat_getter(),
            self._lifecycle_max_age,
        )
        web_search_status = self._web_search_status_getter()
        web_search_ok = web_search_status in {"unknown", "ok"}
        configuration_ok = self._configuration_ok_getter()
        result = {
            "status": (
                "ok"
                if database_ok
                and worker_ok
                and poll_ok
                and hostex_ok
                and context_ok
                and lifecycle_ok
                and configuration_ok
                and web_search_ok
                else "degraded"
            ),
            "database": "ok" if database_ok else "error",
            "worker_heartbeat": "ok" if worker_ok else "stale",
            "wecom_polling": "ok" if poll_ok else "stale",
            "hostex_webhook_sync": "ok" if hostex_ok else "stale",
            "context_maintenance": "ok" if context_ok else "stale",
            "lifecycle_scheduler": (
                "ok" if lifecycle_ok else "stale"
            ),
            "configuration": "ok" if configuration_ok else "incomplete",
            "web_search": web_search_status,
            "wecom_contact_sync": (
                "ok" if self._contact_sync_configured else "not_configured"
            ),
        }
        return result


def _health_service(request: Request) -> HealthServicePort:
    """读取当前健康服务；应用未装配时返回明确降级实现。"""
    return cast(
        HealthServicePort,
        getattr(request.app.state, "health_service", UnconfiguredHealthService()),
    )


def _health_status_code(result: dict[str, str]) -> int:
    """把总体健康状态转换为监控可识别的 HTTP 状态码。"""
    return (
        status.HTTP_200_OK
        if result.get("status") == "ok"
        else status.HTTP_503_SERVICE_UNAVAILABLE
    )


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    """公网只返回总体健康状态，避免暴露内部组件和配置。"""
    result = await _health_service(request).check()
    public_result = {"status": result.get("status", "degraded")}
    return JSONResponse(
        public_result,
        status_code=_health_status_code(result),
    )


@router.get("/employee/health")
async def health_details(request: Request) -> JSONResponse:
    """只向已登录管理员返回内部组件详细诊断。"""
    _, role = await require_employee_session(request)
    if role is not EmployeeRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有管理员可以查看详细健康状态",
        )
    result = await _health_service(request).check()
    return JSONResponse(result, status_code=_health_status_code(result))
