import re
from datetime import date
from typing import Protocol

from homestay_bot.domain.enums import BusinessTaskStatus, EmployeeRole
from homestay_bot.domain.models import (
    BusinessTask,
    Employee,
    RoomOperationalState,
    TaskAttachment,
)
from homestay_bot.services.business_task_service import BusinessTaskService


class TaskPageRepository(Protocol):
    """定义任务移动页所需的查询和分派操作。"""

    async def list_all_open(
        self, *, offset: int, limit: int
    ) -> list[BusinessTask]:
        """分页返回未关闭任务。"""

    async def list_assigned_open(
        self,
        employee_id: int,
        *,
        offset: int,
        limit: int,
    ) -> list[BusinessTask]:
        """分页返回分派给指定员工的未关闭任务。"""

    async def get_task(self, task_id: int) -> BusinessTask | None:
        """按主键读取任务。"""

    async def prepare_assignment(
        self,
        *,
        task_id: int,
        assigned_employee_id: int,
        property_id: int,
        service_date: date,
        actor_employee_id: int,
    ) -> BusinessTask:
        """校验执行员工并补齐分派字段，不改变任务状态。"""

    async def assignment_options(self) -> dict[str, list[object]]:
        """返回可分派员工和可用房间。"""

    async def update_task_checklist(
        self,
        *,
        task_id: int,
        employee_id: int,
        checklist: dict[str, bool],
    ) -> BusinessTask:
        """保存任务检查清单。"""

    async def list_task_attachments(self, task_id: int) -> list[TaskAttachment]:
        """返回任务的附件元数据。"""

    async def get_attachment_by_file_id(
        self,
        file_id: str,
    ) -> TaskAttachment | None:
        """按私有文件编号读取附件元数据。"""

    async def get_room_state(
        self,
        property_id: int,
    ) -> RoomOperationalState | None:
        """读取房间当前运营状态。"""


class TaskPageService:
    """执行两级员工任务可见性和操作权限。"""

    _phone_pattern = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
    _address_pattern = re.compile(
        r"[\u4e00-\u9fff]{2,12}(?:路|街|大道|巷|弄)"
        r"\d{1,5}(?:号|栋|单元|室)?"
    )

    def __init__(
        self,
        tasks: TaskPageRepository,
        task_state: BusinessTaskService,
    ) -> None:
        """注入任务仓储和状态机。"""
        self._tasks = tasks
        self._task_state = task_state

    async def list_for(
        self,
        employee: Employee,
        *,
        offset: int,
        limit: int,
    ) -> list[BusinessTask]:
        """按分页边界返回管理员全部或普通员工自己的任务。"""
        self._require_active(employee)
        if employee.role is EmployeeRole.ADMIN:
            return await self._tasks.list_all_open(offset=offset, limit=limit)
        return await self._tasks.list_assigned_open(
            employee.id,
            offset=offset,
            limit=limit,
        )

    async def detail_for(
        self,
        task_id: int,
        employee: Employee,
    ) -> dict[str, object]:
        """返回当前员工可见的最小任务详情。"""
        task = await self._require_visible(task_id, employee)
        return {
            "task": task,
            "safe_description": self._safe_description(task.description),
            "attachments": await self._tasks.list_task_attachments(task.id),
            "room_state": (
                await self._tasks.get_room_state(task.property_id)
                if task.property_id is not None
                else None
            ),
        }

    async def transition(
        self,
        task_id: int,
        employee: Employee,
        target: str,
    ) -> BusinessTask:
        """校验页面权限后交给统一状态机推进。"""
        task = await self._require_visible(task_id, employee)
        try:
            target_status = BusinessTaskStatus(target)
        except ValueError as error:
            raise ValueError("未知任务状态") from error
        if (
            employee.role is not EmployeeRole.ADMIN
            and target_status is BusinessTaskStatus.CANCELLED
        ):
            raise PermissionError("普通员工不能取消任务")
        if (
            employee.role is not EmployeeRole.ADMIN
            and task.assigned_employee_id != employee.id
        ):
            raise PermissionError("只能操作分派给自己的任务")
        return await self._task_state.transition(
            task_id,
            employee,
            target_status,
        )

    async def assign(
        self,
        task_id: int,
        employee: Employee,
        *,
        assigned_employee_id: int,
        property_id: int,
        service_date: date,
    ) -> BusinessTask:
        """只允许管理员补齐房间日期并分派给启用员工。"""
        self._require_admin(employee)
        task = await self._tasks.prepare_assignment(
            task_id=task_id,
            assigned_employee_id=assigned_employee_id,
            property_id=property_id,
            service_date=service_date,
            actor_employee_id=employee.id,
        )
        if task.status is BusinessTaskStatus.PENDING_CONFIRMATION:
            await self._task_state.transition(
                task_id,
                employee,
                BusinessTaskStatus.PENDING_ASSIGNMENT,
            )
        elif task.status is not BusinessTaskStatus.PENDING_ASSIGNMENT:
            raise ValueError("当前任务状态不能分派")
        return await self._task_state.transition(
            task_id,
            employee,
            BusinessTaskStatus.ASSIGNED,
        )

    async def assignment_options(self) -> dict[str, list[object]]:
        """返回管理员分派表单需要的安全选项。"""
        return await self._tasks.assignment_options()

    async def update_checklist(
        self,
        task_id: int,
        employee: Employee,
        checklist: dict[str, bool],
    ) -> BusinessTask:
        """只允许执行员工更新自己任务的检查清单。"""
        task = await self.require_evidence_editor(task_id, employee)
        return await self._tasks.update_task_checklist(
            task_id=task.id,
            employee_id=employee.id,
            checklist=checklist,
        )

    async def require_evidence_editor(
        self,
        task_id: int,
        employee: Employee,
    ) -> BusinessTask:
        """要求当前员工是任务的实际执行人。"""
        task = await self._require_visible(task_id, employee)
        if task.assigned_employee_id != employee.id:
            raise PermissionError("只有任务执行员工可以提交现场证据")
        if task.status not in {
            BusinessTaskStatus.ASSIGNED,
            BusinessTaskStatus.IN_PROGRESS,
            BusinessTaskStatus.PENDING_INSPECTION,
        }:
            raise ValueError("当前任务状态不能提交现场证据")
        return task

    async def require_attachment_visible(
        self,
        file_id: str,
        employee: Employee,
    ) -> TaskAttachment:
        """按附件关联任务复用管理员或执行员工可见性。"""
        attachment = await self._tasks.get_attachment_by_file_id(file_id)
        if attachment is None:
            raise LookupError("附件不存在")
        await self._require_visible(attachment.task_id, employee)
        return attachment

    async def _require_visible(
        self,
        task_id: int,
        employee: Employee,
    ) -> BusinessTask:
        """读取任务并拒绝普通员工跨人访问。"""
        self._require_active(employee)
        task = await self._tasks.get_task(task_id)
        if task is None:
            raise LookupError("任务不存在")
        if (
            employee.role is not EmployeeRole.ADMIN
            and task.assigned_employee_id != employee.id
        ):
            raise PermissionError("任务不可见")
        return task

    @staticmethod
    def _require_active(employee: Employee) -> None:
        """拒绝停用员工。"""
        if not employee.is_active:
            raise PermissionError("员工已停用")

    @classmethod
    def _require_admin(cls, employee: Employee) -> None:
        """拒绝普通员工执行管理员操作。"""
        cls._require_active(employee)
        if employee.role is not EmployeeRole.ADMIN:
            raise PermissionError("只有管理员可以分派任务")

    @classmethod
    def _safe_description(cls, description: str) -> str:
        """移除任务页不需要展示的手机号和详细门牌地址。"""
        value = cls._phone_pattern.sub("[手机号已隐藏]", description)
        return cls._address_pattern.sub("[详细地址已隐藏]", value)
