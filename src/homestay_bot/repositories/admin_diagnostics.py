"""提供管理员调试审计与系统诊断所需的最小数据库投影。"""

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import JobStatus
from homestay_bot.domain.models import (
    AuditLog,
    ExternalRequest,
    Job,
    PropertyProfile,
    RuntimeConfigState,
)
from homestay_bot.services.admin_debug_service import (
    DebugProperty,
    normalize_debug_intent,
    normalize_debug_tool_names,
)


@dataclass(frozen=True, slots=True)
class SafeAuditEntry:
    """管理员可见的审计投影，不包含 details、UID 或正文。"""

    id: int
    action: str
    target_type: str
    created_at: datetime


@dataclass(frozen=True)
class SafeExternalCallRollup:
    """按端点汇总的外部调用结果，只含机器码与计数。"""

    provider: str
    method: str
    path: str
    total: int
    failed: int
    last_at: datetime
    last_succeeded: bool


class SQLAlchemyAdminDiagnosticsRepository:
    """用显式列投影读取任务状态和安全审计。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定请求期数据库会话。"""
        self._session = session

    async def get_debug_property(self, property_id: int) -> DebugProperty | None:
        """只读取启用房源编号与标题，禁止读取地址和运营秘密。"""
        row = (
            await self._session.execute(
                select(PropertyProfile.id, PropertyProfile.title).where(
                    PropertyProfile.id == property_id,
                    PropertyProfile.is_active.is_(True),
                )
            )
        ).one_or_none()
        if row is None:
            return None
        return DebugProperty(id=int(row.id), title=str(row.title))

    async def list_debug_properties(self) -> tuple[DebugProperty, ...]:
        """按标题和编号稳定返回所有启用房源的安全投影。"""
        rows = await self._session.execute(
            select(PropertyProfile.id, PropertyProfile.title)
            .where(PropertyProfile.is_active.is_(True))
            .order_by(PropertyProfile.title, PropertyProfile.id)
        )
        return tuple(DebugProperty(id=int(row.id), title=str(row.title)) for row in rows)

    async def record_debug_preview(self, **details: object) -> None:
        """写入白名单调试元数据，不保存问题、回复或外部身份。"""
        actor_employee_id = details.get("actor_employee_id")
        question_length = details.get("question_length")
        tool_names = details.get("tool_names")
        if not isinstance(actor_employee_id, int) or not isinstance(
            question_length, int
        ):
            raise ValueError("调试审计编号或长度无效")
        if not isinstance(tool_names, (list, tuple)):
            raise ValueError("调试审计工具列表无效")
        question_hash = details.get("question_hash")
        if not isinstance(question_hash, str) or re.fullmatch(
            r"[0-9a-f]{64}", question_hash
        ) is None:
            question_hash = "0" * 64
        safe_details = {
            "question_hash": question_hash,
            "question_length": question_length,
            "intent": normalize_debug_intent(details.get("intent")),
            "tool_names": normalize_debug_tool_names(tool_names),
            "succeeded": bool(details["succeeded"]),
        }
        self._session.add(
            AuditLog(
                actor_employee_id=actor_employee_id,
                action="admin_debug_preview",
                target_type="admin_debug",
                target_id="preview",
                details=safe_details,
            )
        )
        await self._session.flush()

    async def pending_due_count(self, *, now: datetime) -> int:
        """统计已到期仍未处理的任务数。

        「待处理」在这套队列里同时包含「已到期没人做」和「排到未来还没轮到」。
        生产上 49 条待处理全部属于后者（入住提醒按 available_at 排在未来数日到
        两周），用一个告警色徽标显示总数，会被读成积压。分开数才说得清。
        """
        return int(
            await self._session.scalar(
                select(func.count(Job.id)).where(
                    Job.status == JobStatus.PENDING,
                    Job.available_at <= now,
                )
            )
            or 0
        )

    async def job_status_counts(self) -> dict[str, int]:
        """按状态统计任务数量，不选择 payload。"""
        rows = await self._session.execute(
            select(Job.status, func.count(Job.id)).group_by(Job.status)
        )
        return {
            str(getattr(status, "value", status)): int(count)
            for status, count in rows
        }

    async def configuration_revision(self) -> int:
        """读取运行配置单例 revision，不选择任何密文或掩码字段。"""
        revision = await self._session.scalar(
            select(RuntimeConfigState.revision).where(RuntimeConfigState.id == 1)
        )
        return int(revision or 0)

    async def recent_job_error_codes(self, *, limit: int) -> tuple[str, ...]:
        """按最近更新时间稳定倒序返回有限错误码，不读取异常正文。"""
        recent_at = func.max(Job.updated_at).label("recent_at")
        rows = await self._session.execute(
            select(Job.last_error_code, recent_at)
            .where(Job.last_error_code.is_not(None))
            .group_by(Job.last_error_code)
            .order_by(desc(recent_at), desc(Job.last_error_code))
            .limit(max(0, limit))
        )
        return tuple(self._safe_code(str(code), "unknown_error") for code, _ in rows if code)

    async def list_external_calls(
        self,
        *,
        limit: int,
    ) -> tuple[SafeExternalCallRollup, ...]:
        """按端点汇总最近的外部调用结果。

        诊断页的「订单对账轮询已超时」曾指引用户去审计记录查看调用结果，但审计
        只读 AuditLog，根本没有这些行——承诺兑现不了。这里补上真正的来源。

        逐条罗列没有意义：对账轮询每 15 分钟一次，同一端点在生产上已有上百条
        完全相同的记录。要回答的是「这个接口现在还通不通」，所以按端点聚合出
        总次数、失败次数和最近一次的时间与结果。

        字段仍按机器码校验：ExternalCallRecord 本就刻意不存参数与正文，这里再
        拦一道，避免将来有人往 path 里塞查询串把客户信息带进页面。
        """
        rows = await self._session.execute(
            select(
                ExternalRequest.provider,
                ExternalRequest.method,
                ExternalRequest.path,
                func.count().label("total"),
                func.sum(
                    case((ExternalRequest.succeeded.is_(False), 1), else_=0)
                ).label("failed"),
                func.max(ExternalRequest.created_at).label("last_at"),
            )
            .group_by(
                ExternalRequest.provider,
                ExternalRequest.method,
                ExternalRequest.path,
            )
            .order_by(desc("last_at"))
            .limit(max(0, limit))
        )
        summaries = rows.all()
        results: list[SafeExternalCallRollup] = []
        for row in summaries:
            last_succeeded = await self._session.scalar(
                select(ExternalRequest.succeeded)
                .where(
                    ExternalRequest.provider == row.provider,
                    ExternalRequest.method == row.method,
                    ExternalRequest.path == row.path,
                    ExternalRequest.created_at == row.last_at,
                )
                .order_by(ExternalRequest.id.desc())
                .limit(1)
            )
            results.append(
                SafeExternalCallRollup(
                    provider=self._safe_code(str(row.provider), "unknown_provider"),
                    method=self._safe_code(str(row.method), "unknown_method"),
                    path=self._safe_path(str(row.path)),
                    total=int(row.total),
                    failed=int(row.failed or 0),
                    last_at=row.last_at,
                    last_succeeded=bool(last_succeeded),
                )
            )
        return tuple(results)

    @staticmethod
    def _safe_path(value: str) -> str:
        """只允许稳定的端点路径，拒绝查询串、正文和非 ASCII 内容。"""
        normalized = value.strip()
        if not normalized or len(normalized) > 128:
            return "unknown_path"
        if re.fullmatch(r"/[A-Za-z0-9_./:-]*", normalized) is None:
            return "unknown_path"
        return normalized

    async def list_audits(
        self,
        *,
        offset: int,
        limit: int,
    ) -> tuple[SafeAuditEntry, ...]:
        """按 id 稳定倒序分页，调用方传 page_size+1 判断下一页。"""
        rows = await self._session.execute(
            select(
                AuditLog.id,
                AuditLog.action,
                AuditLog.target_type,
                AuditLog.created_at,
            )
            .order_by(AuditLog.id.desc())
            .offset(max(0, offset))
            .limit(max(0, limit))
        )
        return tuple(
            SafeAuditEntry(
                id=int(row.id),
                action=self._safe_code(str(row.action), "unknown_action"),
                target_type=self._safe_code(str(row.target_type), "unknown_target"),
                created_at=row.created_at,
            )
            for row in rows
        )

    @staticmethod
    def _safe_code(value: str, fallback: str) -> str:
        """只允许稳定机器码进入诊断页面，拒绝正文、URL 和查询参数。"""
        normalized = value.strip()
        if not normalized or len(normalized) > 64:
            return fallback
        if re.fullmatch(r"[A-Za-z0-9_.:-]+", normalized) is None:
            return fallback
        return normalized
