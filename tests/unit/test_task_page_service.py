from datetime import date
from types import SimpleNamespace

import pytest

from homestay_bot.domain.enums import (
    BusinessTaskStatus,
    BusinessTaskType,
    EmployeeRole,
)
from homestay_bot.domain.models import Job
from homestay_bot.services.task_page_service import (
    ATTACHMENT_CLEANUP_JOB_TYPE,
    TaskPageService,
    build_attachment_cleanup_dedupe_key,
)


def employee(
    employee_id: int,
    role: EmployeeRole,
) -> SimpleNamespace:
    """构造一个启用员工。"""
    return SimpleNamespace(
        id=employee_id,
        role=role,
        is_active=True,
    )


def task(
    task_id: int,
    *,
    assigned_employee_id: int | None,
    status: BusinessTaskStatus = BusinessTaskStatus.ASSIGNED,
) -> SimpleNamespace:
    """构造一条不含客户敏感数据的业务任务。"""
    return SimpleNamespace(
        id=task_id,
        task_type=BusinessTaskType.CLEANING,
        status=status,
        property_id=101,
        service_date=date(2026, 8, 2),
        assigned_employee_id=assigned_employee_id,
        description="完成房间保洁",
    )


class TaskRepositoryStub:
    """模拟任务列表、详情和管理员分派。"""

    def __init__(self) -> None:
        """初始化管理员可见的两条任务。"""
        self.items = {
            1: task(1, assigned_employee_id=2),
            2: task(2, assigned_employee_id=3),
        }
        self.assign_calls: list[dict[str, object]] = []
        self.checklist_calls: list[dict[str, object]] = []
        self.attachments = {
            "a" * 32 + ".png": SimpleNamespace(
                task_id=1,
                private_file_id="a" * 32 + ".png",
            ),
            "b" * 32 + ".png": SimpleNamespace(
                task_id=2,
                private_file_id="b" * 32 + ".png",
            ),
        }

    async def list_all_open(self, *, offset: int, limit: int):
        """返回全部未完成任务。"""
        return list(self.items.values())[offset : offset + limit]

    async def list_assigned_open(
        self, employee_id: int, *, offset: int, limit: int
    ):
        """只返回分派给指定员工的未完成任务。"""
        items = [
            item
            for item in self.items.values()
            if item.assigned_employee_id == employee_id
        ]
        return items[offset : offset + limit]

    async def get_task(self, task_id: int):
        """返回指定任务。"""
        return self.items.get(task_id)

    async def prepare_assignment(self, **kwargs):
        """记录管理员补齐的分派字段但不改变状态。"""
        self.assign_calls.append(kwargs)
        item = self.items[kwargs["task_id"]]
        item.assigned_employee_id = kwargs["assigned_employee_id"]
        item.property_id = kwargs["property_id"]
        item.service_date = kwargs["service_date"]
        return item

    async def update_task_checklist(self, **kwargs):
        """记录检查清单更新。"""
        self.checklist_calls.append(kwargs)
        item = self.items[kwargs["task_id"]]
        item.checklist = kwargs["checklist"]
        return item

    async def list_task_attachments(self, task_id):
        """返回任务的安全附件引用。"""
        return [
            item
            for item in self.attachments.values()
            if item.task_id == task_id
        ]

    async def get_attachment_by_file_id(self, file_id):
        """按私有文件编号返回附件。"""
        return self.attachments.get(file_id)

    async def get_room_state(self, property_id):
        """返回尚未建立的房态。"""
        assert property_id == 101
        return None


class TaskStateStub:
    """记录任务状态机调用。"""

    def __init__(self) -> None:
        """初始化调用记录。"""
        self.calls: list[tuple[int, int, BusinessTaskStatus]] = []

    async def transition(self, task_id, actor, target):
        """记录任务、员工和目标状态。"""
        self.calls.append((task_id, actor.id, target))
        return SimpleNamespace(id=task_id, status=target)


@pytest.mark.asyncio
async def test_staff_only_sees_assigned_tasks() -> None:
    """普通员工列表只能看到分派给自己的任务。"""
    repository = TaskRepositoryStub()
    service = TaskPageService(repository, TaskStateStub())

    items = await service.list_for(
        employee(2, EmployeeRole.STAFF),
        offset=0,
        limit=51,
    )

    assert [item.id for item in items] == [1]


@pytest.mark.asyncio
async def test_admin_sees_all_open_tasks() -> None:
    """管理员可以查看全部未完成任务。"""
    repository = TaskRepositoryStub()
    service = TaskPageService(repository, TaskStateStub())

    items = await service.list_for(
        employee(1, EmployeeRole.ADMIN),
        offset=0,
        limit=51,
    )

    assert [item.id for item in items] == [1, 2]


@pytest.mark.asyncio
async def test_staff_cannot_view_another_employee_task() -> None:
    """普通员工越权访问其他人的任务编号必须返回权限错误。"""
    service = TaskPageService(TaskRepositoryStub(), TaskStateStub())

    with pytest.raises(PermissionError):
        await service.detail_for(2, employee(2, EmployeeRole.STAFF))


@pytest.mark.asyncio
async def test_staff_detail_redacts_phone_and_full_address() -> None:
    """任务详情不得向执行员工展示完整手机号和门牌地址。"""
    repository = TaskRepositoryStub()
    repository.items[1].description = (
        "请联系13800138000，到珞喻路123号补矿泉水"
    )
    service = TaskPageService(repository, TaskStateStub())

    detail = await service.detail_for(1, employee(2, EmployeeRole.STAFF))

    assert "13800138000" not in detail["safe_description"]
    assert "珞喻路123号" not in detail["safe_description"]
    assert "[手机号已隐藏]" in detail["safe_description"]
    assert "[详细地址已隐藏]" in detail["safe_description"]


@pytest.mark.asyncio
async def test_staff_cannot_assign_or_cancel_task() -> None:
    """分派和取消属于管理员经营决策。"""
    repository = TaskRepositoryStub()
    states = TaskStateStub()
    service = TaskPageService(repository, states)
    staff = employee(2, EmployeeRole.STAFF)

    with pytest.raises(PermissionError):
        await service.assign(
            1,
            staff,
            assigned_employee_id=3,
            property_id=101,
            service_date=date(2026, 8, 2),
        )
    with pytest.raises(PermissionError):
        await service.transition(
            1,
            staff,
            BusinessTaskStatus.CANCELLED.value,
        )


@pytest.mark.asyncio
async def test_admin_assignment_follows_two_state_transitions() -> None:
    """待确认任务必须先进入待分派，再进入已分派。"""
    repository = TaskRepositoryStub()
    repository.items[1].status = BusinessTaskStatus.PENDING_CONFIRMATION
    states = TaskStateStub()
    service = TaskPageService(repository, states)
    admin = employee(1, EmployeeRole.ADMIN)

    await service.assign(
        1,
        admin,
        assigned_employee_id=2,
        property_id=101,
        service_date=date(2026, 8, 2),
    )

    assert states.calls == [
        (1, 1, BusinessTaskStatus.PENDING_ASSIGNMENT),
        (1, 1, BusinessTaskStatus.ASSIGNED),
    ]


@pytest.mark.asyncio
async def test_assigned_staff_can_start_own_task() -> None:
    """执行员工可以把自己的已分派任务推进到执行中。"""
    repository = TaskRepositoryStub()
    states = TaskStateStub()
    service = TaskPageService(repository, states)

    result = await service.transition(
        1,
        employee(2, EmployeeRole.STAFF),
        BusinessTaskStatus.IN_PROGRESS.value,
    )

    assert result.status is BusinessTaskStatus.IN_PROGRESS
    assert states.calls == [(1, 2, BusinessTaskStatus.IN_PROGRESS)]


@pytest.mark.asyncio
async def test_only_assigned_staff_can_update_checklist() -> None:
    """检查清单只允许任务执行员工维护。"""
    repository = TaskRepositoryStub()
    service = TaskPageService(repository, TaskStateStub())

    result = await service.update_checklist(
        1,
        employee(2, EmployeeRole.STAFF),
        {"clean": True, "supplies": True, "damage": False},
    )
    with pytest.raises(PermissionError):
        await service.update_checklist(
            1,
            employee(3, EmployeeRole.STAFF),
            {"clean": True, "supplies": True, "damage": True},
        )

    assert result.checklist["damage"] is False
    assert len(repository.checklist_calls) == 1


@pytest.mark.asyncio
async def test_closed_task_rejects_new_evidence() -> None:
    """已完成或取消的任务不得继续追加检查证据。"""
    repository = TaskRepositoryStub()
    repository.items[1].status = BusinessTaskStatus.COMPLETED
    service = TaskPageService(repository, TaskStateStub())

    with pytest.raises(ValueError, match="当前任务状态"):
        await service.require_evidence_editor(
            1,
            employee(2, EmployeeRole.STAFF),
        )


@pytest.mark.asyncio
async def test_attachment_visibility_follows_its_task() -> None:
    """私有附件必须复用关联任务的员工可见性。"""
    service = TaskPageService(TaskRepositoryStub(), TaskStateStub())

    visible = await service.require_attachment_visible(
        "a" * 32 + ".png",
        employee(2, EmployeeRole.STAFF),
    )
    with pytest.raises(PermissionError):
        await service.require_attachment_visible(
            "b" * 32 + ".png",
            employee(2, EmployeeRole.STAFF),
        )

    assert visible.task_id == 1


def test_attachment_cleanup_key_is_bounded_and_order_independent() -> None:
    """同一集合无论顺序和重复都得同一个键；来源隔离；多大批量都不超长。"""
    key = build_attachment_cleanup_dedupe_key([3, 1, 2, 3], source="manual")

    assert key == build_attachment_cleanup_dedupe_key([1, 2, 3], source="manual")
    assert key.startswith("task-manual-batch:")
    assert len(key) == len("task-manual-batch:") + 64
    retention_key = build_attachment_cleanup_dedupe_key([1, 2, 3], source="retention")
    assert retention_key.startswith("task-retention-batch:")
    assert retention_key != key
    assert build_attachment_cleanup_dedupe_key([1, 2], source="manual") != key

    # 旧格式在 40 个五位数编号时就有 254 字符，超出 jobs.dedupe_key 的列宽；
    # 新键长度与批量无关。
    column_length = Job.__table__.c.dedupe_key.type.length
    many = range(10_000, 10_040)
    legacy = "task-retention:" + ",".join(str(value) for value in many)
    assert len(legacy) == 254 > column_length
    for source in ("manual", "retention"):
        bounded = build_attachment_cleanup_dedupe_key(
            range(10_000, 20_000),
            source=source,  # type: ignore[arg-type]
        )
        assert len(bounded) <= column_length


def test_attachment_cleanup_key_rejects_unknown_source_and_empty_batch() -> None:
    """来源只接受两个内部固定值；空批次没有可清理的照片，不生成共用键。"""
    with pytest.raises(ValueError):
        build_attachment_cleanup_dedupe_key([1], source="other")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        build_attachment_cleanup_dedupe_key([], source="manual")


class PurgeRepositoryStub:
    """记录批量永久删除的调用顺序。"""

    def __init__(self) -> None:
        """初始化调用记录。"""
        self.calls: list[tuple[str, list[int]]] = []

    async def require_purgeable(self, task_ids: list[int]) -> list[str]:
        """返回两张待清理照片。"""
        self.calls.append(("require_purgeable", list(task_ids)))
        return ["a" * 32 + ".png", "b" * 32 + ".png"]

    async def purge_selected(self, task_ids: list[int], actor_employee_id: int) -> int:
        """返回去重后的删除数量。"""
        self.calls.append(("purge_selected", list(task_ids)))
        return len(set(task_ids))


class CleanupQueueStub:
    """记录登记的照片清理任务。"""

    def __init__(self) -> None:
        """初始化登记记录。"""
        self.enqueued: list[tuple[str, dict[str, object], str | None]] = []

    async def enqueue(self, job_type, payload, *, dedupe_key=None):
        """记录一次登记。"""
        self.enqueued.append((job_type, payload, dedupe_key))
        return SimpleNamespace(id=1)


@pytest.mark.asyncio
async def test_bulk_purge_registers_cleanup_with_bounded_manual_key() -> None:
    """人工批量删除实际使用共享的有界键，与输入顺序和重复无关。"""
    repository = PurgeRepositoryStub()
    queue = CleanupQueueStub()
    service = TaskPageService(repository, TaskStateStub(), queue)  # type: ignore[arg-type]

    purged = await service.purge_many([12, 11, 12], employee(1, EmployeeRole.ADMIN))

    assert purged == 2
    assert queue.enqueued == [
        (
            ATTACHMENT_CLEANUP_JOB_TYPE,
            {"file_ids": ["a" * 32 + ".png", "b" * 32 + ".png"]},
            build_attachment_cleanup_dedupe_key([11, 12], source="manual"),
        )
    ]


@pytest.mark.asyncio
async def test_staff_bulk_purge_is_refused_before_touching_anything() -> None:
    """普通员工批量永久删除仍被拒绝，仓储与清理队列都不被调用。"""
    repository = PurgeRepositoryStub()
    queue = CleanupQueueStub()
    service = TaskPageService(repository, TaskStateStub(), queue)  # type: ignore[arg-type]

    with pytest.raises(PermissionError):
        await service.purge_many([11, 12], employee(2, EmployeeRole.STAFF))

    assert repository.calls == []
    assert queue.enqueued == []
