from datetime import date
from types import SimpleNamespace

import pytest

from homestay_bot.domain.enums import (
    BusinessTaskStatus,
    BusinessTaskType,
    EmployeeRole,
)
from homestay_bot.services.business_task_service import (
    BusinessTaskService,
    InvalidTaskTransition,
    UnsupportedAiTaskType,
)


class TaskRepositoryStub:
    """记录任务服务发出的仓储命令，并模拟消息级幂等。"""

    def __init__(self) -> None:
        """初始化空任务集合。"""
        self.tasks: dict[int, SimpleNamespace] = {}
        self.by_source_message: dict[str, SimpleNamespace] = {}
        self.audit_calls: list[tuple[int, int | None, BusinessTaskStatus]] = []
        self.next_id = 1
        self.turnovers: dict[str, SimpleNamespace] = {}

    async def create_turnover(
        self,
        *,
        property_id: int,
        service_date: date,
        order_id: int,
    ):
        """按房间和退房日模拟周转任务幂等。"""
        key = f"turnover:{property_id}:{service_date.isoformat()}"
        if key not in self.turnovers:
            self.turnovers[key] = SimpleNamespace(
                id=len(self.turnovers) + 1,
                property_id=property_id,
                service_date=service_date,
                order_id=order_id,
                status=BusinessTaskStatus.PENDING_ASSIGNMENT,
            )
        return self.turnovers[key]

    async def create_pending_confirmation(self, **values):
        """按来源消息返回同一条待确认任务。"""
        source_message_id = values["source_message_id"]
        existing = self.by_source_message.get(source_message_id)
        if existing is not None:
            return existing
        task = SimpleNamespace(
            id=self.next_id,
            status=BusinessTaskStatus.PENDING_CONFIRMATION,
            assigned_employee_id=None,
            **values,
        )
        self.next_id += 1
        self.tasks[task.id] = task
        self.by_source_message[source_message_id] = task
        return task

    async def require_for_update(self, task_id: int):
        """返回指定任务。"""
        return self.tasks[task_id]

    async def save_status(self, task, target, actor_employee_id):
        """记录安全审计所需的状态变化元数据。"""
        task.status = target
        self.audit_calls.append((task.id, actor_employee_id, target))
        return task


def employee(*, employee_id: int = 1):
    """构造一个在职管理员。"""
    return SimpleNamespace(
        id=employee_id,
        role=EmployeeRole.ADMIN,
        is_active=True,
    )


@pytest.mark.asyncio
async def test_ai_suggestion_creates_pending_confirmation_task() -> None:
    """AI 建议即使缺少房间和日期，也只能进入待确认状态。"""
    repository = TaskRepositoryStub()
    service = BusinessTaskService(repository)

    task = await service.record_ai_suggestion(
        customer_id=1,
        source_message_id="msg-1",
        task_type=BusinessTaskType.SUPPLIES,
        description="补两瓶矿泉水",
    )

    assert task.status is BusinessTaskStatus.PENDING_CONFIRMATION
    assert task.assigned_employee_id is None
    assert task.property_id is None
    assert task.service_date is None


@pytest.mark.asyncio
async def test_turnover_task_is_created_idempotently() -> None:
    """同一房间同一退房日只返回一条周转保洁任务。"""
    repository = TaskRepositoryStub()
    service = BusinessTaskService(repository)

    first = await service.create_turnover(
        property_id=101,
        service_date=date(2026, 8, 2),
        order_id=7,
    )
    second = await service.create_turnover(
        property_id=101,
        service_date=date(2026, 8, 2),
        order_id=7,
    )

    assert first.id == second.id
    assert len(repository.turnovers) == 1


@pytest.mark.asyncio
async def test_duplicate_source_message_returns_same_task() -> None:
    """同一客人消息重试时不得生成重复任务。"""
    repository = TaskRepositoryStub()
    service = BusinessTaskService(repository)

    first = await service.record_ai_suggestion(
        customer_id=1,
        source_message_id="msg-1",
        task_type=BusinessTaskType.SUPPLIES,
        description="补矿泉水",
    )
    second = await service.record_ai_suggestion(
        customer_id=1,
        source_message_id="msg-1",
        task_type=BusinessTaskType.SUPPLIES,
        description="补矿泉水",
    )

    assert first.id == second.id
    assert len(repository.tasks) == 1


@pytest.mark.asyncio
async def test_ai_cannot_create_system_only_manual_contact_task() -> None:
    """人工接管任务只能由本地规则创建，不能接受模型建议。"""
    service = BusinessTaskService(TaskRepositoryStub())

    with pytest.raises(UnsupportedAiTaskType):
        await service.record_ai_suggestion(
            customer_id=1,
            source_message_id="msg-1",
            task_type=BusinessTaskType.MANUAL_CONTACT,
            description="模型请求人工联系",
        )


@pytest.mark.asyncio
async def test_executable_status_requires_property_and_service_date() -> None:
    """待确认任务缺少房间或日期时不得进入可执行流程。"""
    repository = TaskRepositoryStub()
    service = BusinessTaskService(repository)
    task = await service.record_ai_suggestion(
        customer_id=1,
        source_message_id="msg-1",
        task_type=BusinessTaskType.SUPPLIES,
        description="补矿泉水",
    )

    with pytest.raises(InvalidTaskTransition, match="房间和服务日期"):
        await service.transition(
            task.id,
            employee(),
            BusinessTaskStatus.PENDING_ASSIGNMENT,
        )


@pytest.mark.asyncio
async def test_task_follows_controlled_state_machine() -> None:
    """任务只允许沿确认、分派、执行、检查、完成的受控路径推进。"""
    repository = TaskRepositoryStub()
    service = BusinessTaskService(repository)
    task = await service.record_ai_suggestion(
        customer_id=1,
        source_message_id="msg-1",
        task_type=BusinessTaskType.SPECIAL_SERVICE,
        description="加一床被子",
        property_id=101,
        service_date=date(2026, 8, 1),
    )

    await service.transition(
        task.id,
        employee(),
        BusinessTaskStatus.PENDING_ASSIGNMENT,
    )

    with pytest.raises(InvalidTaskTransition):
        await service.transition(
            task.id,
            employee(),
            BusinessTaskStatus.COMPLETED,
        )
