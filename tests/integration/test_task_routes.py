import json
import logging
import re
from base64 import b64decode, b64encode
from datetime import date
from types import SimpleNamespace

from admin_auth_helpers import configure_admin_auth, login_admin
from fastapi import FastAPI
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
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
            archived_at=None,
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
        self.archive_calls: list[int] = []
        self.restore_calls: list[int] = []
        self.bulk_archive_calls: list[object] = []
        self.selected_archive_calls: list[list[int]] = []
        self.private_file = None
        self.detail_error: Exception | None = None
        self.list_error: Exception | None = None
        self.assignment_error: Exception | None = None
        self.list_calls: list[tuple[int, int]] = []
        self.filter_calls: list[object] = []

    async def list_for(self, employee, *, offset: int, limit: int, filters=None):
        """返回当前角色可见任务。"""
        self.list_calls.append((offset, limit))
        self.filter_calls.append(filters)
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

    async def archive(self, task_id, employee):
        """记录单条归档。"""
        if employee.role is not EmployeeRole.ADMIN:
            raise PermissionError("只有管理员可以归档")
        self.archive_calls.append(task_id)

    async def restore(self, task_id, employee):
        """记录恢复。"""
        if employee.role is not EmployeeRole.ADMIN:
            raise PermissionError("只有管理员可以恢复")
        self.restore_calls.append(task_id)

    async def archive_many(self, employee, task_ids):
        """记录勾选归档使用的编号。"""
        if employee.role is not EmployeeRole.ADMIN:
            raise PermissionError("只有管理员可以归档")
        self.selected_archive_calls.append(list(task_ids))
        return len(task_ids)

    async def archive_filtered(self, employee, filters):
        """记录批量归档使用的筛选条件。"""
        if employee.role is not EmployeeRole.ADMIN:
            raise PermissionError("只有管理员可以归档")
        self.bulk_archive_calls.append(filters)
        return 394

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


def test_task_pages_use_admin_shell_and_protect_risky_forms() -> None:
    """任务列表和详情应进入统一后台，并明确保护编辑与破坏性操作。"""
    client, tasks = build_client(EmployeeRole.ADMIN)
    login(client)

    index = client.get("/employee/tasks")
    detail = client.get("/employee/tasks/1")

    assert '/static/admin.js' in index.text
    assert 'href="/employee/tasks" aria-current="page"' in index.text
    assert '<title>全部待办任务 · YuMi 管理后台</title>' in index.text
    assert 'data-unsaved-warning' in detail.text
    assert 'action="/employee/tasks/1/transition" data-confirm=' in detail.text

    tasks.item.status = BusinessTaskStatus.COMPLETED
    tasks.item.description = "安全长文本"
    completed = client.get("/employee/tasks/1")
    assert 'class="detail-section' in completed.text


def test_task_list_uses_bounded_pagination() -> None:
    """任务第二页必须按固定边界查询并展示前后页入口。"""
    client, tasks = build_client(EmployeeRole.ADMIN)
    login(client)

    response = client.get("/employee/tasks?page=2")

    assert response.status_code == 200
    assert tasks.list_calls == [(50, 51)]
    assert 'href="/employee/tasks?page=1"' in response.text
    assert 'href="/employee/tasks?page=3"' in response.text


def test_task_filters_are_forwarded_and_persist_in_pagination() -> None:
    """任务筛选必须进入查询对象，并在翻页链接中保持。"""
    client, tasks = build_client(EmployeeRole.ADMIN)
    login(client)

    response = client.get(
        "/employee/tasks",
        params={
            "page": 2,
            "status_filter": "pending_assignment",
            "task_type": "cleaning",
            "service_date": "2026-08-02",
            "property_id": 101,
            "assigned_employee_id": 2,
            "overdue": "true",
        },
    )

    assert response.status_code == 200
    filters = tasks.filter_calls[-1]
    assert filters.status is BusinessTaskStatus.PENDING_ASSIGNMENT
    assert filters.task_type is BusinessTaskType.CLEANING
    assert filters.service_date == date(2026, 8, 2)
    assert filters.property_id == 101
    assert filters.assigned_employee_id == 2
    assert filters.overdue is True
    assert "status_filter=pending_assignment" in response.text
    assert "task_type=cleaning" in response.text
    assert "overdue=true" in response.text
    assert '<option value="expired"' in response.text


def test_task_filter_form_treats_empty_controls_as_inactive() -> None:
    """浏览器 GET 表单提交空控件时不得在进入任务页面前返回 422。"""
    client, tasks = build_client(EmployeeRole.ADMIN)
    login(client)

    response = client.get(
        "/employee/tasks",
        params={
            "status_filter": "",
            "task_type": "cleaning",
            "service_date": "",
            "property_id": "",
            "assigned_employee_id": "",
        },
    )

    assert response.status_code == 200
    filters = tasks.filter_calls[-1]
    assert filters.status is None
    assert filters.task_type is BusinessTaskType.CLEANING
    assert filters.service_date is None
    assert filters.property_id is None
    assert filters.assigned_employee_id is None
    assert "data-filter-form" in response.text
    assert client.get("/employee/tasks?service_date=not-a-date").status_code == 422
    assert client.get("/employee/tasks?property_id=0").status_code == 422


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


def _quick_queue_links(html: str) -> list[str]:
    """提取任务中心快速队列里的全部链接文本。"""
    block = re.search(
        r'<nav class="tab-nav" aria-label="任务队列">(.*?)</nav>',
        html,
        re.S,
    )
    assert block is not None
    return re.findall(r"<a [^>]*>([^<]+)</a>", block.group(1))


def test_task_center_keeps_quick_queue_small_and_defers_advanced_filters() -> None:
    """开放队列最多四项，归档是另一维度可单列；完整筛选默认收起。

    v1.3.14 把快速队列收敛为四项开放队列，约束的是开放任务队列不要蔓延。
    「已归档」不是开放队列而是归档维度的入口，此前只藏在默认折叠的高级筛选
    里，归档完就找不到任务去了哪。这里把约束写成它真正的意思，而不是放宽数字。
    """
    client, _ = build_client(EmployeeRole.ADMIN)
    login(client)

    response = client.get("/employee/tasks")

    assert response.status_code == 200
    links = _quick_queue_links(response.text)
    open_queue_links = [link for link in links if link != "已归档"]
    assert len(open_queue_links) <= 4
    assert "已失效" not in links
    assert links.count("已归档") <= 1
    disclosure = re.search(r"<details class=\"filter-disclosure\"([^>]*)>", response.text)
    assert disclosure is not None
    assert "open" not in disclosure.group(1)
    assert '<option value="expired"' in response.text
    assert response.text.index("filter-disclosure") < response.text.index("data-filter-form")


def test_task_center_opens_advanced_filters_when_a_filter_is_applied() -> None:
    """已经生效的筛选必须保持展开，避免员工看不到当前生效条件。"""
    client, _ = build_client(EmployeeRole.ADMIN)
    login(client)

    response = client.get("/employee/tasks", params={"task_type": "cleaning"})

    assert response.status_code == 200
    disclosure = re.search(r"<details class=\"filter-disclosure\"([^>]*)>", response.text)
    assert disclosure is not None
    assert "open" in disclosure.group(1)


SESSION_SECRET = "task-test-secret"


def _read_session(client: TestClient) -> dict:
    """解出当前签名会话内容，用于断言 Cookie 不再承载令牌。"""
    raw = client.cookies.get("session")
    assert raw is not None
    return json.loads(b64decode(TimestampSigner(SESSION_SECRET).unsign(raw)))


def _session_from_response(response) -> dict:
    """从响应的 Set-Cookie 解出服务端写回的会话。

    手工写入的会话 Cookie 与服务端回写的会话 Cookie 域不同，会在 jar 中并存；
    直接读响应可以绕开重名冲突。
    """
    raw = response.cookies.get("session")
    assert raw is not None
    return json.loads(b64decode(TimestampSigner(SESSION_SECRET).unsign(raw)))


def _write_session(client: TestClient, data: dict) -> None:
    """回写签名会话，用于构造迁移前遗留的臃肿会话。"""
    signed = TimestampSigner(SESSION_SECRET).sign(
        b64encode(json.dumps(data).encode())
    )
    # 直接 set 会与服务端下发的同名 Cookie 并存，而带 domain 重设又过不了
    # http.cookiejar 对无点域名的匹配；清空后重设是唯一稳定的写法。
    client.cookies.clear()
    client.cookies.set("session", signed.decode())


def test_task_csrf_rejects_cross_entity_replay() -> None:
    """任务详情签发的令牌不得用于提交另一个任务。"""
    client, tasks = build_client(EmployeeRole.ADMIN)
    login(client)
    csrf_token = detail_csrf(client, task_id=1)

    response = client.post(
        "/employee/tasks/2/transition",
        data={"target": "in_progress", "csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert tasks.transition_calls == []


def test_task_detail_survives_repeated_reload() -> None:
    """同一任务反复打开不得因作用域容量在 GET 阶段 429。"""
    client, _ = build_client(EmployeeRole.ADMIN)
    login(client)

    statuses = [
        client.get("/employee/tasks/1").status_code for _ in range(12)
    ]

    assert statuses == [200] * 12
    # 最后一次签发的令牌必须仍然可用，淘汰只应作用于更旧的令牌。
    latest = detail_csrf(client, task_id=1)
    accepted = client.post(
        "/employee/tasks/1/transition",
        data={"target": "in_progress", "csrf_token": latest},
        follow_redirects=False,
    )
    assert accepted.status_code == 303


def test_browsing_many_tasks_does_not_grow_session_cookie() -> None:
    """连续浏览大量任务详情后，签名会话不得随之膨胀。"""
    client, _ = build_client(EmployeeRole.ADMIN)
    login(client)

    for task_id in range(1, 61):
        assert client.get(f"/employee/tasks/{task_id}").status_code == 200

    session = _read_session(client)
    assert "task_csrf" not in session
    # 浏览器丢弃整条 Cookie 的阈值是 4096 字节；令牌不再入会话后应远低于该值。
    assert len(client.cookies.get("session")) < 600


def test_task_detail_clears_legacy_session_csrf_key() -> None:
    """迁移前遗留的会话令牌字典必须在首次访问详情页后消失。"""
    client, _ = build_client(EmployeeRole.ADMIN)
    login(client)
    session = _read_session(client)
    session["task_csrf"] = {str(index): f"legacy-{index}" for index in range(40)}
    _write_session(client, session)

    response = client.get("/employee/tasks/1")

    assert response.status_code == 200
    assert "task_csrf" not in _session_from_response(response)


def test_admin_assignee_sees_field_evidence_controls() -> None:
    """管理员作为任务执行人时必须能提交现场证据。

    服务端 require_evidence_editor 只要求「是该任务的执行员工」，不看角色；
    模板此前按「不是管理员」判断，导致唯一员工是管理员的部署里，任务被分派
    后再也找不到清单与照片入口，只能停在已分派直至失效。
    """
    client, tasks = build_client(EmployeeRole.ADMIN)
    tasks.item.assigned_employee_id = 1  # 管理员会话的员工编号
    tasks.item.status = BusinessTaskStatus.ASSIGNED
    login(client)

    page = client.get("/employee/tasks/1")

    assert page.status_code == 200
    assert "房间检查" in page.text
    assert 'action="/employee/tasks/1/checklist"' in page.text
    assert 'action="/employee/tasks/1/photos"' in page.text


def test_admin_not_assignee_keeps_field_evidence_hidden() -> None:
    """管理员不是执行人时不得出现现场证据入口，与服务端拒绝保持一致。"""
    client, tasks = build_client(EmployeeRole.ADMIN)
    tasks.item.assigned_employee_id = 2  # 分派给了别人
    tasks.item.status = BusinessTaskStatus.ASSIGNED
    login(client)

    page = client.get("/employee/tasks/1")

    assert page.status_code == 200
    assert 'action="/employee/tasks/1/checklist"' not in page.text
    assert 'action="/employee/tasks/1/photos"' not in page.text


def test_admin_assignee_can_confirm_room_ready() -> None:
    """待检查任务的执行人是管理员时，必须给出确认可入住入口。"""
    client, tasks = build_client(EmployeeRole.ADMIN)
    tasks.item.assigned_employee_id = 1
    tasks.item.status = BusinessTaskStatus.PENDING_INSPECTION
    login(client)

    page = client.get("/employee/tasks/1")

    assert page.status_code == 200
    assert 'action="/employee/tasks/1/ready"' in page.text
    assert "确认房间可入住" in page.text


def test_pending_inspection_task_offers_completion_to_assignee() -> None:
    """待检查任务必须给执行人一个完成入口。

    状态机允许 PENDING_INSPECTION → COMPLETED，但模板此前只提供 in_progress、
    pending_inspection 和 cancelled 三个目标，做完的任务只能等待窗口关闭而失效。
    """
    client, tasks = build_client(EmployeeRole.ADMIN)
    tasks.item.assigned_employee_id = 1
    tasks.item.status = BusinessTaskStatus.PENDING_INSPECTION
    login(client)

    page = client.get("/employee/tasks/1")

    assert page.status_code == 200
    assert 'value="completed"' in page.text
    assert "标记任务完成" in page.text


def test_completion_entry_hidden_outside_pending_inspection() -> None:
    """非待检查状态不得出现完成入口，与状态机允许的迁移保持一致。"""
    client, tasks = build_client(EmployeeRole.ADMIN)
    tasks.item.assigned_employee_id = 1
    tasks.item.status = BusinessTaskStatus.ASSIGNED
    login(client)

    page = client.get("/employee/tasks/1")

    assert page.status_code == 200
    assert 'value="completed"' not in page.text


def test_terminal_task_offers_archive_to_admin() -> None:
    """终态任务必须给管理员归档入口。

    失效任务此前只能无限堆积，全仓没有任何删除或归档能力。
    """
    client, tasks = build_client(EmployeeRole.ADMIN)
    tasks.item.status = BusinessTaskStatus.EXPIRED
    tasks.item.archived_at = None
    login(client)

    page = client.get("/employee/tasks/1")

    assert 'action="/employee/tasks/1/archive"' in page.text
    assert "移入归档" in page.text


def test_open_task_has_no_archive_entry() -> None:
    """开放中的任务不得出现归档入口，避免把没做的活藏起来。"""
    client, tasks = build_client(EmployeeRole.ADMIN)
    tasks.item.status = BusinessTaskStatus.ASSIGNED
    login(client)

    page = client.get("/employee/tasks/1")

    assert 'action="/employee/tasks/1/archive"' not in page.text


def test_archived_task_offers_restore() -> None:
    """已归档任务必须可恢复：选软归档的全部意义就在可逆。"""
    client, tasks = build_client(EmployeeRole.ADMIN)
    tasks.item.status = BusinessTaskStatus.EXPIRED
    tasks.item.archived_at = "2026-09-05T00:00:00Z"
    login(client)

    page = client.get("/employee/tasks/1")

    assert 'action="/employee/tasks/1/restore"' in page.text
    assert "从归档恢复" in page.text


def test_admin_archives_single_task() -> None:
    """管理员提交归档后服务收到该任务编号。"""
    client, tasks = build_client(EmployeeRole.ADMIN)
    tasks.item.status = BusinessTaskStatus.EXPIRED
    login(client)

    response = client.post(
        "/employee/tasks/1/archive",
        data={"csrf_token": detail_csrf(client)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert tasks.archive_calls == [1]


def test_staff_cannot_archive_task() -> None:
    """普通员工不得归档任务。"""
    client, tasks = build_client(EmployeeRole.STAFF)
    login(client)

    response = client.post(
        "/employee/tasks/1/archive",
        data={"csrf_token": detail_csrf(client)},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert tasks.archive_calls == []


def test_bulk_archive_uses_current_filters_as_selection() -> None:
    """批量归档以当前筛选条件为选择范围，不引入多选提交。"""
    client, tasks = build_client(EmployeeRole.ADMIN)
    login(client)
    page = client.get("/employee/tasks?status_filter=expired")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)

    response = client.post(
        "/employee/tasks/archive-filtered",
        data={
            "csrf_token": token,
            "status_filter": "expired",
            "task_type": "",
            "property_id": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/employee/tasks?archived=true"
    assert len(tasks.bulk_archive_calls) == 1
    assert tasks.bulk_archive_calls[0].status is BusinessTaskStatus.EXPIRED


def test_open_task_gets_no_checkbox_but_terminal_task_does() -> None:
    """只有终态任务给勾选框。

    开放态任务提交后必然被整批拒绝，让它可勾选等于引导用户走进注定失败的操作；
    从源头不渲染，比事后报错更早解决问题。
    """
    client, tasks = build_client(EmployeeRole.ADMIN)
    login(client)

    tasks.item.status = BusinessTaskStatus.ASSIGNED
    open_page = client.get("/employee/tasks")
    assert 'name="task_ids"' not in open_page.text
    assert "本页 0 条可归档" in open_page.text
    # 无可归档项时不给提交按钮
    assert ">归档勾选的任务</button>" not in open_page.text

    tasks.item.status = BusinessTaskStatus.EXPIRED
    terminal_page = client.get("/employee/tasks")
    assert 'name="task_ids"' in terminal_page.text
    assert "data-select-all" in terminal_page.text
    assert 'action="/employee/tasks/archive-selected"' in terminal_page.text
    assert "本页 1 条可归档" in terminal_page.text
    assert ">归档勾选的任务</button>" in terminal_page.text


def test_staff_list_has_no_selection_controls() -> None:
    """归档是管理员能力，普通员工列表不出现勾选。"""
    client, _ = build_client(EmployeeRole.STAFF)
    login(client)

    page = client.get("/employee/tasks")

    assert 'name="task_ids"' not in page.text
    assert 'action="/employee/tasks/archive-selected"' not in page.text


def test_admin_archives_selected_tasks() -> None:
    """勾选提交后服务收到全部被选编号。"""
    client, tasks = build_client(EmployeeRole.ADMIN)
    login(client)
    page = client.get("/employee/tasks")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)

    response = client.post(
        "/employee/tasks/archive-selected",
        data={"csrf_token": token, "task_ids": ["11", "12", "13"]},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert tasks.selected_archive_calls == [[11, 12, 13]]


def test_staff_cannot_archive_selected_tasks() -> None:
    """普通员工不得提交勾选归档。

    员工列表不签发批量令牌，因此请求先被令牌绑定拦下（409）；即便令牌有效，
    服务层的管理员检查仍是第二道防线。这里断言请求被拒且服务从未被调用。
    """
    client, tasks = build_client(EmployeeRole.STAFF)
    login(client)

    page = client.get("/employee/tasks")
    assert "bulk_csrf_token" not in page.text
    assert 'action="/employee/tasks/archive-selected"' not in page.text

    response = client.post(
        "/employee/tasks/archive-selected",
        data={"csrf_token": detail_csrf(client), "task_ids": ["11"]},
        follow_redirects=False,
    )

    assert response.status_code >= 400
    assert tasks.selected_archive_calls == []
