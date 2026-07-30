from typing import Protocol

from homestay_bot.domain.enums import (
    BusinessTaskStatus,
    EmployeeRole,
    RoomOperationalStatus,
)
from homestay_bot.domain.models import (
    BusinessTask,
    Employee,
    RoomOperationalState,
)


class ReadinessRuleError(ValueError):
    """表示房间可入住所需证据或任务状态不完整。"""


class TaskEvidenceRepository(Protocol):
    """定义可入住校验所需的任务证据查询。"""

    async def require_for_update(self, task_id: int) -> BusinessTask:
        """锁定并返回任务。"""

    async def has_photo_attachment(self, task_id: int) -> bool:
        """判断任务是否至少关联一张有效照片。"""


class RoomStateRepository(Protocol):
    """定义房态更新入口。"""

    async def require_room_state_for_update(
        self,
        property_id: int,
    ) -> RoomOperationalState:
        """锁定并返回已有房态。"""

    async def set_room_status(
        self,
        property_id: int,
        status: RoomOperationalStatus,
        actor_employee_id: int,
    ) -> RoomOperationalState:
        """锁定并更新房态，同时记录安全审计。"""


class RoomReadinessService:
    """校验证据后允许执行员工标记可入住、管理员撤回。"""

    _required_checklist = frozenset({"clean", "supplies", "damage"})

    def __init__(
        self,
        tasks: TaskEvidenceRepository,
        rooms: RoomStateRepository,
    ) -> None:
        """注入任务证据和房态仓储。"""
        self._tasks = tasks
        self._rooms = rooms

    async def mark_ready(
        self,
        task_id: int,
        actor: Employee,
    ) -> RoomOperationalState:
        """验证执行人、待检查状态、清单和照片后标记可入住。"""
        if not actor.is_active:
            raise PermissionError("员工已停用")
        task = await self._tasks.require_for_update(task_id)
        if task.assigned_employee_id != actor.id:
            raise PermissionError("只有该任务的执行员工可以标记可入住")
        if task.status is not BusinessTaskStatus.PENDING_INSPECTION:
            raise ReadinessRuleError("任务必须处于待检查状态")
        if task.property_id is None:
            raise ReadinessRuleError("任务尚未关联房间")
        if not self._required_checklist.issubset(task.checklist) or not all(
            task.checklist.get(key) is True
            for key in self._required_checklist
        ):
            raise ReadinessRuleError("保洁检查清单尚未全部完成")
        if not await self._tasks.has_photo_attachment(task.id):
            raise ReadinessRuleError("至少需要一张有效现场照片")
        return await self._rooms.set_room_status(
            task.property_id,
            RoomOperationalStatus.READY,
            actor.id,
        )

    async def revoke_ready(
        self,
        property_id: int,
        administrator: Employee,
    ) -> RoomOperationalState:
        """只允许启用管理员把房间撤回待检查。"""
        if (
            not administrator.is_active
            or administrator.role is not EmployeeRole.ADMIN
        ):
            raise PermissionError("只有管理员可以撤回可入住状态")
        current = await self._rooms.require_room_state_for_update(property_id)
        if current.status is not RoomOperationalStatus.READY:
            raise ReadinessRuleError("只有可入住状态可以撤回")
        return await self._rooms.set_room_status(
            property_id,
            RoomOperationalStatus.PENDING_INSPECTION,
            administrator.id,
        )
