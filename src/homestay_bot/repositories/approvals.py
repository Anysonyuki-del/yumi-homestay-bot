from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import ApprovalStatus, EmployeeRole
from homestay_bot.domain.models import BookingApproval, Employee


class SQLAlchemyApprovalRepository:
    """使用 SQLAlchemy 会话持久化并锁定审批单。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前请求或后台任务的数据库会话。"""
        self._session = session

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """打开原子事务，退出时自动提交或回滚。"""
        async with self._session.begin():
            yield

    async def add(self, approval: BookingApproval) -> BookingApproval:
        """加入新审批单并刷新主键。"""
        self._session.add(approval)
        await self._session.flush()
        return approval

    async def get_by_source_message_id(
        self, source_message_id: str
    ) -> BookingApproval | None:
        """按来源消息读取已创建审批单，供延迟任务幂等重放。"""
        return cast(
            BookingApproval | None,
            await self._session.scalar(
            select(BookingApproval).where(
                BookingApproval.source_message_id == source_message_id
            )
            ),
        )

    async def get_for_update(self, approval_id: int) -> BookingApproval:
        """使用数据库行锁读取审批单，阻止并发重复确认。"""
        statement = (
            select(BookingApproval).where(BookingApproval.id == approval_id).with_for_update()
        )
        approval = await self._session.scalar(statement)
        if approval is None:
            raise LookupError(f"审批单不存在: {approval_id}")
        return approval

    async def save(self, approval: BookingApproval) -> None:
        """刷新审批单变更，提交由外层事务负责。"""
        self._session.add(approval)
        await self._session.flush()

    async def recover_stale_creating(self, *, before: datetime) -> int:
        """把进程中断遗留的创建中审批转为人工核验，绝不自动重放。"""
        statement = (
            update(BookingApproval)
            .where(
                BookingApproval.status == ApprovalStatus.CREATING,
                BookingApproval.approved_at < before,
            )
            .values(
                status=ApprovalStatus.NEEDS_REVIEW,
                failure_message="创建进程中断，需人工核验百居易后台",
            )
        )
        result = cast(
            CursorResult[Any], await self._session.execute(statement)
        )
        await self._session.flush()
        return int(result.rowcount)


class SQLAlchemyPermissionChecker:
    """从员工表验证最终下单权限。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前请求的数据库会话。"""
        self._session = session

    async def require_booking_approver(self, employee_id: int) -> None:
        """仅允许管理员创建订单。"""
        employee = await self._session.get(Employee, employee_id)
        if (
            employee is None
            or not employee.is_active
            or employee.role is not EmployeeRole.ADMIN
        ):
            raise PermissionError("当前员工没有确认下单权限")
