import logging
import re
from datetime import date
from types import SimpleNamespace

from admin_auth_helpers import configure_admin_auth, login_admin
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from homestay_bot.domain.enums import (
    BusinessTaskStatus,
    BusinessTaskType,
    EmployeeRole,
)
from homestay_bot.routes.employee_auth import router as employee_auth_router
from homestay_bot.routes.private_files import router as private_files_router
from homestay_bot.routes.tasks import router as tasks_router
from homestay_bot.services.private_file_storage import StoredPrivateFile

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00"
    b"\x90wS\xde"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


class TaskPageStub:
    """返回固定安全任务详情并记录写操作。"""

    def __init__(self) -> None:
        """初始化一条员工任务。"""
        self.item = SimpleNamespace(
            id=1,
            task_type=BusinessTaskType.CLEANING,
            status=BusinessTaskStatus.ASSIGNED,
            property_id=101,
            service_date=date(2026, 8, 2),
            assigned_employee_id=2,
            description="完成房间保洁",
            checklist={},
        )
        self.transition_calls: list[tuple[int, int, str]] = []
        self.assign_calls: list[dict[str, object]] = []
        self.checklist_calls: list[dict[str, object]] = []
        self.photo_calls: list[dict[str, object]] = []
        self.ready_calls: list[tuple[int, int]] = []
        self.revoke_calls: list[tuple[int, int]] = []
        self.private_file = None
        self.detail_error: Exception | None = None
        self.list_error: Exception | None = None
        self.assignment_error: Exception | None = None
        self.list_calls: list[tuple[int, int]] = []

    async def list_for(self, employee, *, offset: int, limit: int):
        """返回当前角色可见任务。"""
        self.list_calls.append((offset, limit))
        if self.list_error is not None:
            raise self.list_error
        return [self.item] * (limit if offset == 50 else 1)

    async def detail_for(self, task_id, employee):
        """普通员工只能读取自己的固定任务。"""
        if self.detail_error is not None:
            raise self.detail_error
        if employee.role is EmployeeRole.STAFF and task_id != 1:
            raise PermissionError("任务不可见")
        if task_id == 404:
            raise LookupError("任务不存在")
        return {
            "task": self.item,
            "safe_description": self.item.description,
        }

    async def transition(self, task_id, employee, target):
        """记录状态推进。"""
        if employee.role is EmployeeRole.STAFF and target == "cancelled":
            raise PermissionError("普通员工不能取消任务")
        self.transition_calls.append((task_id, employee.id, target))
        return self.item

    async def assign(self, task_id, employee, **kwargs):
        """只允许管理员分派。"""
        if employee.role is not EmployeeRole.ADMIN:
            raise PermissionError("只有管理员可以分派")
        self.assign_calls.append(
            {"task_id": task_id, "employee_id": employee.id, **kwargs}
        )
        return self.item

    async def assignment_options(self):
        """返回管理员可选员工和房间。"""
        if self.assignment_error is not None:
            raise self.assignment_error
        return {
            "employees": [SimpleNamespace(id=2, name="阿姨")],
            "properties": [SimpleNamespace(id=101, title="长江中心")],
        }

    async def update_checklist(self, task_id, employee, checklist):
        """记录员工提交的清单。"""
        self.checklist_calls.append(
            {
                "task_id": task_id,
                "employee_id": employee.id,
                "checklist": checklist,
            }
        )
        return self.item

    async def upload_photo(
        self,
        task_id,
        employee,
        stream,
        content_type,
    ):
        """记录照片字节和 MIME。"""
        self.photo_calls.append(
            {
                "task_id": task_id,
                "employee_id": employee.id,
                "content": stream.read(),
                "content_type": content_type,
            }
        )
        return SimpleNamespace(private_file_id="a" * 32 + ".png")

    async def mark_ready(self, task_id, employee):
        """记录执行员工标记可入住。"""
        self.ready_calls.append((task_id, employee.id))
        return SimpleNamespace(status="ready")

    async def revoke_ready(self, task_id, employee):
        """只允许管理员撤回可入住。"""
        if employee.role is not EmployeeRole.ADMIN:
            raise PermissionError("只有管理员可以撤回")
        self.revoke_calls.append((task_id, employee.id))
        return SimpleNamespace(status="pending_inspection")

    async def file_for(self, file_id, employee):
        """返回当前员工有权读取的测试文件。"""
        if file_id == "b" * 32 + ".png":
            raise PermissionError("附件不可见")
        return self.private_file


def build_client(role: EmployeeRole) -> tuple[TestClient, TaskPageStub]:
    """创建带签名会话的任务页应用。"""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="task-test-secret")
    app.include_router(employee_auth_router)
    app.include_router(tasks_router)
    app.include_router(private_files_router)
    configure_admin_auth(app, role)
    tasks = TaskPageStub()
    if role is EmployeeRole.ADMIN:
        tasks.item.status = BusinessTaskStatus.PENDING_ASSIGNMENT
    app.state.task_page_service = tasks
    return TestClient(app), tasks


def login(client: TestClient) -> None:
    """通过独立账号密码表单建立版本化员工会话。"""
    login_admin(client)


def detail_csrf(client: TestClient, task_id: int = 1) -> str:
    """读取任务详情的一次性 CSRF 令牌。"""
    response = client.get(f"/employee/tasks/{task_id}")
    return re.search(
        r'name="csrf_token" value="([^"]+)"',
        response.text,
    ).group(1)


def test_default_employee_login_returns_to_task_center() -> None:
    """未指定目标页时，独立管理员登录后应进入任务中心。"""
    client, _ = build_client(EmployeeRole.STAFF)
    page = client.get("/employee/login")
    csrf = re.search(
        r'name="csrf_token" value="([^"]+)"',
        page.text,
    ).group(1)
    response = client.post(
        "/employee/login",
        data={
            "username": "admin",
            "password": "test-password",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )

    assert response.headers["location"] == "/employee/tasks"


def test_staff_task_page_only_labels_own_tasks() -> None:
    """员工任务首页明确只展示自己的任务。"""
    client, _ = build_client(EmployeeRole.STAFF)
    login(client)

    response = client.get("/employee/tasks")

    assert response.status_code == 200
    assert "自己的任务" in response.text
    assert "全部待办任务" not in response.text


def test_admin_sees_all_tasks_and_assignment_form() -> None:
    """管理员可查看全部任务并使用分派表单。"""
    client, _ = build_client(EmployeeRole.ADMIN)
    login(client)

    index = client.get("/employee/tasks")
    detail = client.get("/employee/tasks/1")

    assert "全部待办任务" in index.text
    assert 'name="assigned_employee_id"' in detail.text


def test_task_list_uses_bounded_pagination() -> None:
    """任务第二页必须按固定边界查询并展示前后页入口。"""
    client, tasks = build_client(EmployeeRole.ADMIN)
    login(client)

    response = client.get("/employee/tasks?page=2")

    assert response.status_code == 200
    assert tasks.list_calls == [(50, 51)]
    assert 'href="/employee/tasks?page=1"' in response.text
    assert 'href="/employee/tasks?page=3"' in response.text


def test_staff_cannot_view_other_task_id() -> None:
    """越权任务编号统一返回 403。"""
    client, _ = build_client(EmployeeRole.STAFF)
    login(client)

    response = client.get("/employee/tasks/2")

    assert response.status_code == 403
    assert "13800138000" not in response.text


def test_task_unknown_error_uses_stable_detail_and_safe_log(caplog) -> None:
    """任务未知异常不得把内部异常原文返回给页面。"""
    client, tasks = build_client(EmployeeRole.STAFF)
    tasks.detail_error = RuntimeError("secret database value")
    login(client)

    with caplog.at_level(logging.ERROR):
        response = client.get("/employee/tasks/1")

    assert response.status_code == 409
    assert response.json()["detail"] == "任务操作未完成"
    assert "secret database value" not in response.text
    assert any(
        record.getMessage().startswith("任务页面操作失败")
        and "RuntimeError" in record.getMessage()
        for record in caplog.records
    )


def test_task_list_unknown_error_uses_stable_detail_and_safe_log(caplog) -> None:
    """任务列表未知异常不得把内部异常原文返回给页面。"""
    client, tasks = build_client(EmployeeRole.STAFF)
    tasks.list_error = RuntimeError("secret task list value")
    login(client)

    with caplog.at_level(logging.ERROR):
        response = client.get("/employee/tasks")

    assert response.status_code == 409
    assert response.json()["detail"] == "任务操作未完成"
    assert "secret task list value" not in response.text
    assert any(
        record.getMessage().startswith("任务页面操作失败")
        and "RuntimeError" in record.getMessage()
        for record in caplog.records
    )


def test_task_assignment_options_unknown_error_uses_stable_detail_and_safe_log(
    caplog,
) -> None:
    """管理员详情的分派选项异常也不得泄露内部原文。"""
    client, tasks = build_client(EmployeeRole.ADMIN)
    tasks.assignment_error = RuntimeError("secret assignment value")
    login(client)

    with caplog.at_level(logging.ERROR):
        response = client.get("/employee/tasks/1")

    assert response.status_code == 409
    assert response.json()["detail"] == "任务操作未完成"
    assert "secret assignment value" not in response.text
    assert any(
        record.getMessage().startswith("任务页面操作失败")
        and "RuntimeError" in record.getMessage()
        for record in caplog.records
    )


def test_task_transition_requires_one_time_csrf() -> None:
    """任务写操作必须校验并消耗一次性 CSRF 令牌。"""
    client, tasks = build_client(EmployeeRole.STAFF)
    login(client)
    csrf_token = detail_csrf(client)

    first = client.post(
        "/employee/tasks/1/transition",
        data={"target": "in_progress", "csrf_token": csrf_token},
        follow_redirects=False,
    )
    replay = client.post(
        "/employee/tasks/1/transition",
        data={"target": "in_progress", "csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert first.status_code == 303
    assert replay.status_code == 409
    assert tasks.transition_calls == [(1, 2, "in_progress")]


def test_staff_cannot_assign_or_cancel() -> None:
    """普通员工不能通过伪造表单执行管理员动作。"""
    client, tasks = build_client(EmployeeRole.STAFF)
    login(client)
    cancel_token = detail_csrf(client)
    cancel = client.post(
        "/employee/tasks/1/transition",
        data={"target": "cancelled", "csrf_token": cancel_token},
    )
    assign_token = detail_csrf(client)
    assign = client.post(
        "/employee/tasks/1/assign",
        data={
            "assigned_employee_id": "2",
            "property_id": "101",
            "service_date": "2026-08-02",
            "csrf_token": assign_token,
        },
    )

    assert cancel.status_code == 403
    assert assign.status_code == 403
    assert tasks.transition_calls == []
    assert tasks.assign_calls == []


def test_staff_can_submit_checklist_and_photo() -> None:
    """执行员工可以用一次性令牌提交清单和现场照片。"""
    client, tasks = build_client(EmployeeRole.STAFF)
    login(client)
    checklist_token = detail_csrf(client)
    checklist = client.post(
        "/employee/tasks/1/checklist",
        data={
            "clean": "true",
            "supplies": "true",
            "damage": "true",
            "csrf_token": checklist_token,
        },
        follow_redirects=False,
    )
    photo_token = detail_csrf(client)
    photo = client.post(
        "/employee/tasks/1/photos",
        data={"csrf_token": photo_token},
        files={"photo": ("room.png", PNG_BYTES, "image/png")},
        follow_redirects=False,
    )

    assert checklist.status_code == 303
    assert photo.status_code == 303
    assert tasks.checklist_calls[0]["checklist"] == {
        "clean": True,
        "supplies": True,
        "damage": True,
    }
    assert tasks.photo_calls[0]["content"] == PNG_BYTES


def test_assigned_staff_can_mark_ready_and_admin_can_revoke() -> None:
    """执行员工可以标记可入住，管理员可以撤回待检查。"""
    staff_client, staff_tasks = build_client(EmployeeRole.STAFF)
    login(staff_client)
    ready_token = detail_csrf(staff_client)
    ready = staff_client.post(
        "/employee/tasks/1/ready",
        data={"csrf_token": ready_token},
        follow_redirects=False,
    )

    admin_client, admin_tasks = build_client(EmployeeRole.ADMIN)
    login(admin_client)
    revoke_token = detail_csrf(admin_client)
    revoke = admin_client.post(
        "/employee/tasks/1/revoke-ready",
        data={"csrf_token": revoke_token},
        follow_redirects=False,
    )

    assert ready.status_code == 303
    assert staff_tasks.ready_calls == [(1, 2)]
    assert revoke.status_code == 303
    assert admin_tasks.revoke_calls == [(1, 1)]


def test_private_file_download_requires_task_visibility(tmp_path) -> None:
    """员工只能下载自己任务关联的私有照片。"""
    client, tasks = build_client(EmployeeRole.STAFF)
    visible_id = "a" * 32 + ".png"
    path = tmp_path / visible_id
    path.write_bytes(PNG_BYTES)
    tasks.private_file = StoredPrivateFile(
        file_id=visible_id,
        path=path,
        content_type="image/png",
        size=len(PNG_BYTES),
    )
    login(client)

    visible = client.get(f"/employee/private-files/{visible_id}")
    forbidden = client.get(
        f"/employee/private-files/{'b' * 32}.png"
    )

    assert visible.status_code == 200
    assert visible.content == PNG_BYTES
    assert visible.headers["cache-control"] == "no-store"
    assert forbidden.status_code == 403
