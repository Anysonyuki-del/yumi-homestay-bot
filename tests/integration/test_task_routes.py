import re
from datetime import date
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from homestay_bot.domain.enums import (
    BusinessTaskStatus,
    BusinessTaskType,
    EmployeeRole,
)
from homestay_bot.domain.models import Employee
from homestay_bot.routes.employee_auth import router as employee_auth_router
from homestay_bot.routes.tasks import router as tasks_router


class EmployeeAuthStub:
    """返回指定两级角色的企业微信员工。"""

    def __init__(self, role: EmployeeRole) -> None:
        """保存登录后角色。"""
        self.role = role

    def authorization_url(self, redirect_uri: str, state: str) -> str:
        """返回带 state 的测试授权地址。"""
        return f"https://wecom.example/authorize?state={state}"

    async def authenticate(self, code: str) -> Employee:
        """返回启用员工。"""
        return Employee(
            id=1 if self.role is EmployeeRole.ADMIN else 2,
            wecom_userid="test-user",
            name="测试员工",
            role=self.role,
            is_active=True,
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
        )
        self.transition_calls: list[tuple[int, int, str]] = []
        self.assign_calls: list[dict[str, object]] = []

    async def list_for(self, employee):
        """返回当前角色可见任务。"""
        return [self.item]

    async def detail_for(self, task_id, employee):
        """普通员工只能读取自己的固定任务。"""
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
        return {
            "employees": [SimpleNamespace(id=2, name="阿姨")],
            "properties": [SimpleNamespace(id=101, title="长江中心")],
        }


def build_client(role: EmployeeRole) -> tuple[TestClient, TaskPageStub]:
    """创建带签名会话的任务页应用。"""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="task-test-secret")
    app.include_router(employee_auth_router)
    app.include_router(tasks_router)
    app.state.employee_auth_service = EmployeeAuthStub(role)
    tasks = TaskPageStub()
    if role is EmployeeRole.ADMIN:
        tasks.item.status = BusinessTaskStatus.PENDING_ASSIGNMENT
    app.state.task_page_service = tasks
    return TestClient(app), tasks


def login(client: TestClient) -> None:
    """走 OAuth state 流程建立员工会话。"""
    response = client.get(
        "/employee/login",
        params={"next": "/employee/tasks"},
        follow_redirects=False,
    )
    state = re.search(r"state=([^&]+)", response.headers["location"]).group(1)
    callback = client.get(
        "/employee/oauth/callback",
        params={"code": "code", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code == 303


def detail_csrf(client: TestClient, task_id: int = 1) -> str:
    """读取任务详情的一次性 CSRF 令牌。"""
    response = client.get(f"/employee/tasks/{task_id}")
    return re.search(
        r'name="csrf_token" value="([^"]+)"',
        response.text,
    ).group(1)


def test_default_employee_login_returns_to_task_center() -> None:
    """未指定目标页时，普通员工登录后应进入任务中心而不是管理员审批页。"""
    client, _ = build_client(EmployeeRole.STAFF)
    response = client.get("/employee/login", follow_redirects=False)
    state = re.search(r"state=([^&]+)", response.headers["location"]).group(1)

    callback = client.get(
        "/employee/oauth/callback",
        params={"code": "code", "state": state},
        follow_redirects=False,
    )

    assert callback.headers["location"] == "/employee/tasks"


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


def test_staff_cannot_view_other_task_id() -> None:
    """越权任务编号统一返回 403。"""
    client, _ = build_client(EmployeeRole.STAFF)
    login(client)

    response = client.get("/employee/tasks/2")

    assert response.status_code == 403
    assert "13800138000" not in response.text


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
