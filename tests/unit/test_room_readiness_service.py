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
        order_id=77,
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
        self.overrides: list[tuple[int, int, RoomOperationalStatus]] = []
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

    async def record_manual_override(
        self, *, property_id, actor_employee_id, status
    ) -> None:
        """记录人工房态覆盖审计。"""
        self.overrides.append((property_id, actor_employee_id, status))


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


@pytest.mark.asyncio
async def test_mark_ready_only_triggers_credential_safety_evaluation() -> None:
    """房态请求只登记凭证评估，不直接向客人发送任何内容。"""
    calls: list[dict[str, object]] = []

    class EvaluatorStub:
        """记录凭证安全评估参数。"""

        async def evaluate(self, **fields):
            """记录任务订单与房间关联。"""
            calls.append(fields)
            return None

    service = RoomReadinessService(
        TaskEvidenceStub(),
        RoomStateStub(),
        EvaluatorStub(),
    )

    await service.mark_ready(7, employee(2))

    assert calls == [
        {
            "order_id": 77,
            "expected_property_id": 101,
            "source_task_id": 7,
        }
    ]


@pytest.mark.asyncio
async def test_admin_can_set_any_room_status_without_evidence() -> None:
    """管理员可以直接设定房态，包括证据流程之外的清洁中与维修中。

    仓储层本就支持全部六个状态，但此前只有 mark_ready 与 revoke_ready
    两个受限入口，房间坏了无法标记为维修中。
    """
    rooms = RoomStateStub()
    service = RoomReadinessService(TaskEvidenceStub(), rooms)

    for target in (
        RoomOperationalStatus.MAINTENANCE,
        RoomOperationalStatus.CLEANING,
        RoomOperationalStatus.OCCUPIED,
    ):
        state = await service.set_status_by_admin(
            101, employee(9, EmployeeRole.ADMIN), target
        )
        assert state.status is target

    assert [call[1] for call in rooms.calls] == [
        RoomOperationalStatus.MAINTENANCE,
        RoomOperationalStatus.CLEANING,
        RoomOperationalStatus.OCCUPIED,
    ]


@pytest.mark.asyncio
async def test_admin_direct_ready_is_recorded_as_manual_override() -> None:
    """直改到可入住必须留下人工覆盖审计。

    房态 READY 是向客人发放门锁密码的前置条件之一，人工直改绕过了清单与
    照片证据；事后必须能分辨这个 READY 的来源。
    """
    rooms = RoomStateStub()
    service = RoomReadinessService(TaskEvidenceStub(), rooms)

    await service.set_status_by_admin(
        101, employee(9, EmployeeRole.ADMIN), RoomOperationalStatus.READY
    )

    assert rooms.overrides == [(101, 9, RoomOperationalStatus.READY)]


@pytest.mark.asyncio
async def test_staff_cannot_set_room_status_directly() -> None:
    """普通员工不得绕过证据流程直接设定房态。"""
    rooms = RoomStateStub()
    service = RoomReadinessService(TaskEvidenceStub(), rooms)

    with pytest.raises(PermissionError):
        await service.set_status_by_admin(
            101, employee(9, EmployeeRole.STAFF), RoomOperationalStatus.READY
        )

    assert rooms.calls == []
    assert rooms.overrides == []
