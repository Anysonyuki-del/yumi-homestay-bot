"""提供管理员调试审计与系统诊断所需的最小数据库投影。"""

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.models import (
    AuditLog,
    Job,
    PropertyProfile,
    RuntimeConfigState,
)
from homestay_bot.services.admin_debug_service import DebugProperty


@dataclass(frozen=True, slots=True)
class SafeAuditEntry:
    """管理员可见的审计投影，不包含 details、UID 或正文。"""

    id: int
    action: str
    target_type: str
    created_at: datetime


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
        safe_details = {
            "question_hash": str(details["question_hash"]),
            "question_length": question_length,
            "intent": str(details["intent"])[:64],
            "tool_names": [str(item)[:64] for item in tool_names],
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
