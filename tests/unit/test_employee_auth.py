from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.routes.employee_auth import require_employee_session


class AccessVerifierStub:
    """返回唯一管理员凭证与外键员工的最新状态。"""

    async def get_active_admin(self, admin_id: int, employee_id: int):
        """复核管理员编号、员工编号和当前会话版本。"""
        assert admin_id == 1
        assert employee_id == 2
        return SimpleNamespace(
            admin_id=1,
            id=2,
            employee_id=2,
            role=EmployeeRole.ADMIN,
            is_active=True,
            session_version=4,
            must_change_password=False,
        )


def _request(
    app: SimpleNamespace,
    session: dict[str, object],
    *,
    path: str = "/employee/tasks",
) -> Request:
    """创建带应用状态和可变签名会话的请求。"""
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "app": app,
            "session": session,
        }
    )


@pytest.mark.asyncio
async def test_active_admin_session_is_reverified_and_activity_is_refreshed() -> None:
    """每个请求都应复核唯一管理员及版本，并刷新最后活动时间。"""
    now = datetime(2026, 8, 11, 9, tzinfo=UTC)
    app = SimpleNamespace(
        state=SimpleNamespace(
            employee_access_verifier=AccessVerifierStub(),
            admin_auth_clock=lambda: now,
        )
    )
    session = {
        "employee_id": 2,
        "employee_role": "admin",
        "admin_id": 1,
        "admin_session_version": 4,
        "last_activity_at": (now - timedelta(minutes=5)).isoformat(),
    }

    employee_id, role = await require_employee_session(_request(app, session))

    assert employee_id == 2
    assert role is EmployeeRole.ADMIN
    assert session["employee_role"] == "admin"
    assert session["last_activity_at"] == now.isoformat()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("session_patch", "detail"),
    [
        ({"admin_session_version": 3}, "管理员会话已失效"),
        (
            {
                "last_activity_at": datetime(
                    2026, 8, 11, 0, 59, tzinfo=UTC
                ).isoformat()
            },
            "管理员会话已超时",
        ),
    ],
)
async def test_invalid_admin_session_is_cleared(
    session_patch: dict[str, object],
    detail: str,
) -> None:
    """版本不匹配或闲置超过八小时都必须清空整个签名会话。"""
    now = datetime(2026, 8, 11, 9, tzinfo=UTC)
    app = SimpleNamespace(
        state=SimpleNamespace(
            employee_access_verifier=AccessVerifierStub(),
            admin_auth_clock=lambda: now,
        )
    )
    session: dict[str, object] = {
        "employee_id": 2,
        "employee_role": "admin",
        "admin_id": 1,
        "admin_session_version": 4,
        "last_activity_at": (now - timedelta(hours=1)).isoformat(),
        "unrelated": "must-also-be-cleared",
    }
    session.update(session_patch)

    with pytest.raises(HTTPException, match=detail) as raised:
        await require_employee_session(_request(app, session))

    assert raised.value.status_code == 401
    assert session == {}


@pytest.mark.asyncio
async def test_first_login_session_can_only_open_account_or_logout() -> None:
    """强制首次改密时，其他后台页面应跳到账号安全页。"""
    now = datetime(2026, 8, 11, 9, tzinfo=UTC)

    class MustChangeVerifier(AccessVerifierStub):
        """模拟仍处于首次改密状态的管理员。"""

        async def get_active_admin(self, admin_id: int, employee_id: int):
            """返回必须改密的当前版本。"""
            state = await super().get_active_admin(admin_id, employee_id)
            state.must_change_password = True
            return state

    app = SimpleNamespace(
        state=SimpleNamespace(
            employee_access_verifier=MustChangeVerifier(),
            admin_auth_clock=lambda: now,
        )
    )
    session = {
        "employee_id": 2,
        "employee_role": "admin",
        "admin_id": 1,
        "admin_session_version": 4,
        "last_activity_at": now.isoformat(),
    }

    with pytest.raises(HTTPException) as raised:
        await require_employee_session(_request(app, session))

    assert raised.value.status_code == 303
    assert raised.value.headers["Location"] == "/employee/account"
