from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

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
            "configuration": "incomplete",
            "web_search": "not_configured",
        }


class OperationalHealthService:
    """以只读方式检查数据库、worker 心跳和配置完整性。"""

    def __init__(
        self,
        *,
        database_probe: Callable[[], Awaitable[bool]],
        heartbeat_getter: Callable[[], datetime | None],
        poll_heartbeat_getter: Callable[[], datetime | None],
        configuration_ok: bool,
        web_search_status_getter: Callable[[], str],
        heartbeat_max_age: timedelta = timedelta(minutes=2),
        poll_max_age: timedelta = timedelta(minutes=1),
    ) -> None:
        """注入无副作用探针和心跳读取器。"""
        self._database_probe = database_probe
        self._heartbeat_getter = heartbeat_getter
        self._poll_heartbeat_getter = poll_heartbeat_getter
        self._configuration_ok = configuration_ok
        self._web_search_status_getter = web_search_status_getter
        self._heartbeat_max_age = heartbeat_max_age
        self._poll_max_age = poll_max_age

    async def check(self) -> dict[str, str]:
        """执行只读检查，不在健康接口创建任何外部资源。"""
        database_ok = await self._database_probe()
        heartbeat = self._heartbeat_getter()
        worker_ok = (
            isinstance(heartbeat, datetime)
            and datetime.now(UTC) - heartbeat <= self._heartbeat_max_age
        )
        poll_heartbeat = self._poll_heartbeat_getter()
        poll_ok = (
            isinstance(poll_heartbeat, datetime)
            and datetime.now(UTC) - poll_heartbeat <= self._poll_max_age
        )
        web_search_status = self._web_search_status_getter()
        web_search_ok = web_search_status in {"unknown", "ok"}
        result = {
            "status": (
                "ok"
                if database_ok
                and worker_ok
                and poll_ok
                and self._configuration_ok
                and web_search_ok
                else "degraded"
            ),
            "database": "ok" if database_ok else "error",
            "worker_heartbeat": "ok" if worker_ok else "stale",
            "wecom_polling": "ok" if poll_ok else "stale",
            "configuration": "ok" if self._configuration_ok else "incomplete",
            "web_search": web_search_status,
        }
        return result


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    """返回分层健康状态，降级时使用 503 便于监控识别。"""
    service = cast(
        HealthServicePort,
        getattr(request.app.state, "health_service", UnconfiguredHealthService()),
    )
    result = await service.check()
    response_status = (
        status.HTTP_200_OK
        if result["status"] == "ok"
        else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return JSONResponse(result, status_code=response_status)
