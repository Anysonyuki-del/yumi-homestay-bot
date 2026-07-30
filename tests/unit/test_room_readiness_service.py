from types import SimpleNamespace

import pytest

from homestay_bot.domain.enums import (
    BusinessTaskStatus,
    EmployeeRole,
    RoomOperationalStatus,
)
from homestay_bot.services.room_readiness_service import (
    ReadinessRuleError,
    RoomReadinessService,
)


def employee(
    employee_id: int,
    role: EmployeeRole = EmployeeRole.STAFF,
) -> SimpleNamespace:
    """构造启用员工。"""
    return SimpleNamespace(
        id=employee_id,
        role=role,
        is_active=True,
    )


def ready_task() -> SimpleNamespace:
    """构造具备可入住证据的待检查任务。"""
    return SimpleNamespace(
        id=7,
        property_id=101,
        assigned_employee_id=2,
        status=BusinessTaskStatus.PENDING_INSPECTION,
        checklist={
            "clean": True,
            "supplies": True,
            "damage": True,
        },
    )


class TaskEvidenceStub:
    """返回固定任务和照片证据。"""

    def __init__(self, task=None, *, has_photo: bool = True) -> None:
        """配置任务及照片存在性。"""
        self.task = task or ready_task()
        self.has_photo = has_photo

    async def require_for_update(self, task_id: int):
        """锁定并返回任务。"""
        assert task_id == self.task.id
        return self.task

    async def has_photo_attachment(self, task_id: int) -> bool:
        """返回任务是否有照片。"""
        return self.has_photo


class RoomStateStub:
    """记录房态更新。"""

    def __init__(self) -> None:
        """初始化房态调用。"""
        self.calls: list[tuple[int, RoomOperationalStatus, int]] = []
        self.current = SimpleNamespace(
            property_id=101,
            status=RoomOperationalStatus.READY,
        )

    async def require_room_state_for_update(self, property_id):
        """返回锁定后的当前房态。"""
        assert property_id == self.current.property_id
        return self.current

    async def set_room_status(self, property_id, status, actor_employee_id):
        """记录目标房态并返回状态对象。"""
        self.calls.append((property_id, status, actor_employee_id))
        return SimpleNamespace(
            property_id=property_id,
            status=status,
            changed_by=actor_employee_id,
        )


@pytest.mark.asyncio
async def test_assigned_employee_can_mark_ready_after_checklist_and_photo() -> None:
    """执行员工提供完整证据后可以把房间标记为可入住。"""
    tasks = TaskEvidenceStub()
    rooms = RoomStateStub()
    service = RoomReadinessService(tasks, rooms)

    state = await service.mark_ready(7, employee(2))

    assert state.status is RoomOperationalStatus.READY
    assert rooms.calls == [(101, RoomOperationalStatus.READY, 2)]


@pytest.mark.asyncio
async def test_other_employee_cannot_mark_room_ready() -> None:
    """非执行员工不得修改该任务关联房态。"""
    service = RoomReadinessService(TaskEvidenceStub(), RoomStateStub())

    with pytest.raises(PermissionError):
        await service.mark_ready(7, employee(3))


@pytest.mark.asyncio
async def test_missing_photo_or_checklist_rejects_ready() -> None:
    """缺照片或任一必检项未完成时不得标记可入住。"""
    no_photo = RoomReadinessService(
        TaskEvidenceStub(has_photo=False),
        RoomStateStub(),
    )
    incomplete = ready_task()
    incomplete.checklist["damage"] = False
    no_checklist = RoomReadinessService(
        TaskEvidenceStub(incomplete),
        RoomStateStub(),
    )

    with pytest.raises(ReadinessRuleError, match="照片"):
        await no_photo.mark_ready(7, employee(2))
    with pytest.raises(ReadinessRuleError, match="清单"):
        await no_checklist.mark_ready(7, employee(2))


@pytest.mark.asyncio
async def test_non_inspection_task_cannot_mark_ready() -> None:
    """任务不在待检查状态时不得提前把房间设为可入住。"""
    task = ready_task()
    task.status = BusinessTaskStatus.IN_PROGRESS
    service = RoomReadinessService(
        TaskEvidenceStub(task),
        RoomStateStub(),
    )

    with pytest.raises(ReadinessRuleError, match="待检查"):
        await service.mark_ready(7, employee(2))


@pytest.mark.asyncio
async def test_only_admin_can_revoke_ready_room() -> None:
    """撤回可入住属于管理员权限。"""
    rooms = RoomStateStub()
    service = RoomReadinessService(TaskEvidenceStub(), rooms)

    with pytest.raises(PermissionError):
        await service.revoke_ready(101, employee(2))
    state = await service.revoke_ready(
        101,
        employee(1, EmployeeRole.ADMIN),
    )

    assert state.status is RoomOperationalStatus.PENDING_INSPECTION


@pytest.mark.asyncio
async def test_admin_cannot_revoke_room_that_is_not_ready() -> None:
    """管理员也只能撤回当前确为可入住的房间。"""
    rooms = RoomStateStub()
    rooms.current.status = RoomOperationalStatus.CLEANING
    service = RoomReadinessService(TaskEvidenceStub(), rooms)

    with pytest.raises(ReadinessRuleError, match="可入住"):
        await service.revoke_ready(
            101,
            employee(1, EmployeeRole.ADMIN),
        )
