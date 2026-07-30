from datetime import date
from typing import Protocol

from homestay_bot.domain.enums import BusinessTaskStatus, BusinessTaskType
from homestay_bot.domain.models import BusinessTask, Employee


class InvalidTaskTransition(ValueError):
    """表示任务状态、执行信息或操作人员不满足业务规则。"""


class UnsupportedAiTaskType(ValueError):
    """表示模型建议了只允许系统创建的任务类型。"""


class BusinessTaskRepository(Protocol):
    """定义业务任务服务所需的最小仓储边界。"""

    async def create_turnover(
        self,
        *,
        property_id: int,
        service_date: date,
        order_id: int,
    ) -> BusinessTask:
        """幂等创建周转保洁任务。"""

    async def create_pending_confirmation(
        self,
        *,
        customer_id: int,
        source_message_id: str,
        task_type: BusinessTaskType,
        description: str,
        property_id: int | None = None,
        service_date: date | None = None,
    ) -> BusinessTask:
        """幂等创建待确认任务。"""

    async def require_for_update(self, task_id: int) -> BusinessTask:
        """锁定并读取任务。"""

    async def save_status(
        self,
        task: BusinessTask,
        target: BusinessTaskStatus,
        actor_employee_id: int | None,
    ) -> BusinessTask:
        """保存状态并记录安全审计。"""


class BusinessTaskService:
    """执行 AI 建议白名单与运营任务状态机。"""

    _ai_task_types = frozenset(
        {
            BusinessTaskType.CLEANING,
            BusinessTaskType.MAINTENANCE,
            BusinessTaskType.SUPPLIES,
            BusinessTaskType.SPECIAL_SERVICE,
            BusinessTaskType.EARLY_CHECK_IN,
            BusinessTaskType.LATE_CHECK_OUT,
        }
    )
    _allowed_transitions = {
        BusinessTaskStatus.PENDING_CONFIRMATION: {
            BusinessTaskStatus.PENDING_ASSIGNMENT,
            BusinessTaskStatus.CANCELLED,
        },
        BusinessTaskStatus.PENDING_ASSIGNMENT: {
            BusinessTaskStatus.ASSIGNED,
            BusinessTaskStatus.CANCELLED,
        },
        BusinessTaskStatus.ASSIGNED: {
            BusinessTaskStatus.IN_PROGRESS,
            BusinessTaskStatus.CANCELLED,
        },
        BusinessTaskStatus.IN_PROGRESS: {
            BusinessTaskStatus.PENDING_INSPECTION,
            BusinessTaskStatus.CANCELLED,
        },
        BusinessTaskStatus.PENDING_INSPECTION: {
            BusinessTaskStatus.IN_PROGRESS,
            BusinessTaskStatus.COMPLETED,
            BusinessTaskStatus.CANCELLED,
        },
    }
    _execution_statuses = frozenset(
        {
            BusinessTaskStatus.PENDING_ASSIGNMENT,
            BusinessTaskStatus.ASSIGNED,
            BusinessTaskStatus.IN_PROGRESS,
            BusinessTaskStatus.PENDING_INSPECTION,
            BusinessTaskStatus.COMPLETED,
        }
    )

    def __init__(self, tasks: BusinessTaskRepository) -> None:
        """注入任务仓储。"""
        self._tasks = tasks

    async def create_turnover(
        self,
        *,
        property_id: int,
        service_date: date,
        order_id: int,
    ) -> BusinessTask:
        """为订单退房日创建唯一周转保洁任务。"""
        return await self._tasks.create_turnover(
            property_id=property_id,
            service_date=service_date,
            order_id=order_id,
        )

    async def record_ai_suggestion(
        self,
        *,
        customer_id: int,
        source_message_id: str,
        task_type: BusinessTaskType,
        description: str,
        property_id: int | None = None,
        service_date: date | None = None,
    ) -> BusinessTask:
        """把白名单内模型建议保存为待管理员确认任务。"""
        if task_type not in self._ai_task_types:
            raise UnsupportedAiTaskType(f"AI 不允许创建任务类型：{task_type.value}")
        safe_description = " ".join(description.split()).strip()[:500]
        if not safe_description:
            raise ValueError("任务描述不能为空")
        return await self._tasks.create_pending_confirmation(
            customer_id=customer_id,
            source_message_id=source_message_id,
            task_type=task_type,
            description=safe_description,
            property_id=property_id,
            service_date=service_date,
        )

    async def transition(
        self,
        task_id: int,
        actor: Employee,
        target: BusinessTaskStatus,
    ) -> BusinessTask:
        """校验员工、执行信息和状态路径后原子推进任务。"""
        if not actor.is_active:
            raise InvalidTaskTransition("停用员工不能操作任务")
        task = await self._tasks.require_for_update(task_id)
        allowed = self._allowed_transitions.get(task.status, set())
        if target not in allowed:
            raise InvalidTaskTransition(
                f"不允许从 {task.status.value} 进入 {target.value}"
            )
        if target in self._execution_statuses and (
            task.property_id is None or task.service_date is None
        ):
            raise InvalidTaskTransition("进入可执行状态前必须补齐房间和服务日期")
        if (
            target is BusinessTaskStatus.ASSIGNED
            and task.assigned_employee_id is None
        ):
            raise InvalidTaskTransition("进入已分派状态前必须选择执行员工")
        return await self._tasks.save_status(task, target, actor.id)
