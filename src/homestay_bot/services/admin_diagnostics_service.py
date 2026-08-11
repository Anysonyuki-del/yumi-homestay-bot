"""聚合管理员系统诊断、任务概况和安全审计分页。"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class HealthPort(Protocol):
    """定义现有运行健康服务接口。"""

    async def check(self) -> dict[str, str]:
        """返回受控组件状态。"""


class DiagnosticsRepositoryPort(Protocol):
    """定义诊断所需的安全数据库投影。"""

    async def job_status_counts(self) -> dict[str, int]:
        """返回任务状态计数。"""

    async def configuration_revision(self) -> int:
        """返回数据库当前配置 revision。"""

    async def recent_job_error_codes(self, *, limit: int) -> tuple[str, ...]:
        """返回最近稳定错误码。"""

    async def list_audits(self, *, offset: int, limit: int) -> tuple[Any, ...]:
        """返回 limit+1 条安全审计投影。"""


class RuntimeStatusPort(Protocol):
    """定义当前运行配置状态读取。"""

    async def status(self) -> Any:
        """返回不含凭证的 registry 状态。"""


@dataclass(frozen=True, slots=True)
class DiagnosticsSnapshot:
    """系统诊断页面和复制报告共用的安全 view model。"""

    health: dict[str, str]
    job_status_counts: dict[str, int]
    recent_job_error_codes: tuple[str, ...]
    started_at: datetime
    version: str
    configuration_revision: int | None
    report_text: str


@dataclass(frozen=True, slots=True)
class AuditPage:
    """安全审计的稳定分页结果。"""

    items: tuple[Any, ...]
    page: int
    page_size: int
    has_previous: bool
    has_next: bool


class AdminDiagnosticsService:
    """组合既有健康服务和最小数据库投影，异常时安全降级。"""

    def __init__(
        self,
        *,
        health: HealthPort,
        repository: DiagnosticsRepositoryPort,
        registry: RuntimeStatusPort | None,
        started_at: datetime,
        version: str,
    ) -> None:
        """保存只读依赖和无秘密进程元数据。"""
        self._health = health
        self._repository = repository
        self._registry = registry
        self._started_at = started_at
        self._version = version

    async def snapshot(self) -> DiagnosticsSnapshot:
        """生成诊断快照；任一探针失败时不透传异常正文。"""
        try:
            health = await self._health.check()
        except Exception as error:
            logger.warning("管理员诊断健康聚合失败：error_type=%s", type(error).__name__)
            health = {"status": "degraded"}
        try:
            counts = await self._repository.job_status_counts()
            error_codes = await self._repository.recent_job_error_codes(limit=8)
        except Exception as error:
            logger.warning("管理员诊断任务聚合失败：error_type=%s", type(error).__name__)
            counts = {}
            error_codes = ()
            health = {**health, "status": "degraded"}
        revision: int | None = None
        if self._registry is not None:
            try:
                revision = int((await self._registry.status()).revision)
            except Exception as error:
                logger.warning("管理员诊断配置状态失败：error_type=%s", type(error).__name__)
                health = {**health, "status": "degraded"}
        else:
            try:
                revision = await self._repository.configuration_revision()
            except Exception as error:
                logger.warning("管理员诊断配置读取失败：error_type=%s", type(error).__name__)
                health = {**health, "status": "degraded"}
        report = self._build_report(health, counts, error_codes, revision)
        return DiagnosticsSnapshot(
            health=health,
            job_status_counts=counts,
            recent_job_error_codes=error_codes,
            started_at=self._started_at,
            version=self._version,
            configuration_revision=revision,
            report_text=report,
        )

    async def list_audits(self, *, page: int, page_size: int = 20) -> AuditPage:
        """使用稳定倒序与 limit+1 返回审计分页。"""
        normalized_page = max(1, page)
        normalized_size = min(50, max(1, page_size))
        rows = await self._repository.list_audits(
            offset=(normalized_page - 1) * normalized_size,
            limit=normalized_size + 1,
        )
        return AuditPage(
            items=tuple(rows[:normalized_size]),
            page=normalized_page,
            page_size=normalized_size,
            has_previous=normalized_page > 1,
            has_next=len(rows) > normalized_size,
        )

    def _build_report(
        self,
        health: dict[str, str],
        counts: dict[str, int],
        error_codes: tuple[str, ...],
        revision: int | None,
    ) -> str:
        """由服务端生成可复制的固定格式脱敏文本。"""
        lines = [
            "YuMi 系统诊断报告（已脱敏）",
            f"版本：{self._version}",
            f"启动时间：{self._started_at.isoformat()}",
            f"配置 revision：{revision if revision is not None else '不可用'}",
            f"总体状态：{health.get('status', 'degraded')}",
        ]
        lines.extend(
            f"组件 {name}：{value}"
            for name, value in sorted(health.items())
            if name != "status"
        )
        lines.extend(
            f"任务 {name}：{value}"
            for name, value in sorted(counts.items())
        )
        if error_codes:
            lines.append("最近错误码：" + "、".join(error_codes))
        return "\n".join(lines)
