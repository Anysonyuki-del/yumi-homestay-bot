from types import SimpleNamespace

import pytest
from starlette.requests import Request

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.routes.employee_auth import require_employee_session


class AccessVerifierStub:
    """返回数据库迁移后的最新员工角色。"""

    async def get_active(self, employee_id: int):
        """忽略旧会话角色并返回普通员工。"""
        assert employee_id == 2
        return SimpleNamespace(
            id=2,
            role=EmployeeRole.STAFF,
            is_active=True,
        )


@pytest.mark.asyncio
async def test_database_role_refreshes_legacy_session_role() -> None:
    """角色迁移后旧签名会话应从数据库刷新，不强制员工重新登录。"""
    app = SimpleNamespace(
        state=SimpleNamespace(
            employee_access_verifier=AccessVerifierStub(),
        )
    )
    session = {
        "employee_id": 2,
        "employee_role": "customer_service",
    }
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/employee/tasks",
            "headers": [],
            "app": app,
            "session": session,
        }
    )

    employee_id, role = await require_employee_session(request)

    assert employee_id == 2
    assert role is EmployeeRole.STAFF
    assert session["employee_role"] == "staff"
