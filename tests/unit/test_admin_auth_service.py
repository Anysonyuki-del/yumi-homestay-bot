from datetime import UTC, datetime, timedelta

import pytest
from argon2 import PasswordHasher, Type

from homestay_bot.domain.models import AdminCredential
from homestay_bot.services.admin_auth_service import (
    AdminAuthService,
    AuthenticationError,
)


class MemoryAdminCredentialRepository:
    """以内存对象模拟凭证仓储，单元测试只关注认证规则。"""

    def __init__(self, credential: AdminCredential | None) -> None:
        """保存唯一管理员凭证。"""
        self.credential = credential

    async def get_by_username(
        self, username: str, *, for_update: bool = False
    ) -> AdminCredential | None:
        """按用户名返回唯一凭证。"""
        del for_update
        if self.credential is not None and self.credential.username == username:
            return self.credential
        return None

    async def get_by_id(
        self, admin_id: int, *, for_update: bool = False
    ) -> AdminCredential | None:
        """按管理员凭证主键返回唯一凭证。"""
        del for_update
        if self.credential is not None and self.credential.id == admin_id:
            return self.credential
        return None

    async def save(self, credential: AdminCredential) -> None:
        """内存仓储无需额外持久化动作。"""
        self.credential = credential


def _credential(password: str = "initial-password") -> AdminCredential:
    """创建使用 Argon2id 的单例测试管理员。"""
    password_hash = PasswordHasher(type=Type.ID).hash(password)
    return AdminCredential(
        id=1,
        employee_id=7,
        username="admin",
        password_hash=password_hash,
        must_change_password=True,
        failed_attempts=0,
        locked_until=None,
        session_version=1,
    )


@pytest.mark.asyncio
async def test_authenticate_verifies_argon2id_and_clears_failures() -> None:
    """正确 Argon2id 密码应登录成功并清零历史失败状态。"""
    now = datetime(2026, 8, 11, 8, tzinfo=UTC)
    credential = _credential()
    credential.failed_attempts = 3
    repository = MemoryAdminCredentialRepository(credential)

    session = await AdminAuthService(repository).authenticate(
        "admin", "initial-password", now
    )

    assert credential.password_hash.startswith("$argon2id$")
    assert credential.failed_attempts == 0
    assert credential.locked_until is None
    assert session.admin_id == 1
    assert session.employee_id == 7
    assert session.must_change_password is True
    assert session.session_version == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("username", "password"),
    [("missing", "initial-password"), ("admin", "wrong-password")],
)
async def test_authenticate_uses_one_error_for_unknown_user_and_wrong_password(
    username: str,
    password: str,
) -> None:
    """未知账号与错误密码必须使用相同错误，避免枚举管理员用户名。"""
    service = AdminAuthService(MemoryAdminCredentialRepository(_credential()))

    with pytest.raises(AuthenticationError, match="用户名或密码错误"):
        await service.authenticate(
            username,
            password,
            datetime(2026, 8, 11, 8, tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_five_failures_lock_account_for_fifteen_minutes() -> None:
    """连续五次失败后应锁定十五分钟，锁定期内正确密码也不能登录。"""
    now = datetime(2026, 8, 11, 8, tzinfo=UTC)
    credential = _credential()
    service = AdminAuthService(MemoryAdminCredentialRepository(credential))

    for attempt in range(5):
        with pytest.raises(AuthenticationError):
            await service.authenticate(
                "admin",
                "wrong-password",
                now + timedelta(seconds=attempt),
            )

    assert credential.failed_attempts == 5
    assert credential.locked_until == now + timedelta(seconds=4, minutes=15)

    with pytest.raises(AuthenticationError, match="用户名或密码错误"):
        await service.authenticate(
            "admin",
            "initial-password",
            now + timedelta(minutes=14),
        )

    session = await service.authenticate(
        "admin",
        "initial-password",
        now + timedelta(minutes=16),
    )
    assert session.admin_id == 1
    assert credential.failed_attempts == 0
    assert credential.locked_until is None


@pytest.mark.asyncio
async def test_change_password_clears_first_login_and_revokes_old_sessions() -> None:
    """首次改密应写入新哈希并递增版本，让旧会话立即失效。"""
    credential = _credential()
    service = AdminAuthService(MemoryAdminCredentialRepository(credential))

    await service.change_password(
        credential.id,
        "initial-password",
        "new-secure-password",
    )

    assert credential.password_hash.startswith("$argon2id$")
    assert credential.must_change_password is False
    assert credential.session_version == 2
    with pytest.raises(AuthenticationError):
        await service.reverify(credential.id, "initial-password")
    await service.reverify(credential.id, "new-secure-password")


@pytest.mark.asyncio
async def test_revoke_other_sessions_increments_and_returns_session_version() -> None:
    """主动撤销其他会话应原子递增并返回新的会话版本。"""
    credential = _credential()
    service = AdminAuthService(MemoryAdminCredentialRepository(credential))

    version = await service.revoke_other_sessions(credential.id)

    assert version == 2
    assert credential.session_version == 2
