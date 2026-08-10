from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from homestay_bot.domain.models import AdminCredential
from homestay_bot.services.admin_passwords import (
    ADMIN_PASSWORD_HASHER,
    hash_admin_password,
    validate_new_admin_password,
    verify_admin_password,
)

LOCK_DURATION = timedelta(minutes=15)
AUTHENTICATION_ERROR_MESSAGE = "用户名或密码错误"


class AuthenticationError(PermissionError):
    """表示不披露具体原因的统一管理员认证失败。"""


@dataclass(frozen=True, slots=True)
class AdminSession:
    """返回给路由层的最小管理员会话身份。"""

    admin_id: int
    employee_id: int
    username: str
    must_change_password: bool
    session_version: int


class AdminCredentialRepository(Protocol):
    """定义认证服务所需的最小凭证仓储接口。"""

    async def get_by_username(self, username: str) -> AdminCredential | None:
        """按用户名读取凭证。"""

    async def get_by_id(self, admin_id: int) -> AdminCredential | None:
        """按凭证主键读取管理员。"""

    async def record_failed_attempt(
        self,
        admin_id: int,
        *,
        now: datetime,
        lock_until: datetime,
    ) -> AdminCredential | None:
        """原子记录一次密码失败。"""

    async def record_auth_success(
        self,
        admin_id: int,
        *,
        expected_password_hash: str,
        now: datetime,
        replacement_password_hash: str | None,
    ) -> AdminCredential | None:
        """原子记录成功认证并可选升级哈希。"""

    async def change_password_atomic(
        self,
        admin_id: int,
        *,
        expected_password_hash: str,
        new_password_hash: str,
        now: datetime,
    ) -> int | None:
        """以旧密码哈希为条件原子改密并递增会话版本。"""

    async def increment_session_version(self, admin_id: int) -> int | None:
        """原子递增并返回会话版本。"""


class AdminAuthService:
    """实现唯一管理员的 Argon2id 校验、锁定、改密和会话撤销。"""

    def __init__(
        self,
        repository: AdminCredentialRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """注入凭证仓储和 UTC 时钟，并准备未知用户名的虚拟哈希。"""
        self._repository = repository
        self._clock = clock or _utc_now
        self._dummy_hash = hash_admin_password("admin-auth-dummy-password")

    async def authenticate(
        self,
        username: str,
        password: str,
        now: datetime,
    ) -> AdminSession:
        """校验密码；五次失败锁十五分钟，成功后清零认证失败状态。"""
        credential = await self._repository.get_by_username(username)
        if credential is None:
            verify_admin_password(self._dummy_hash, password)
            raise AuthenticationError(AUTHENTICATION_ERROR_MESSAGE)

        normalized_now = _as_utc(now)
        if _is_locked(credential, normalized_now):
            raise AuthenticationError(AUTHENTICATION_ERROR_MESSAGE)
        if not verify_admin_password(credential.password_hash, password):
            await self._record_failure(credential.id, normalized_now)
            raise AuthenticationError(AUTHENTICATION_ERROR_MESSAGE)

        replacement_hash = None
        if ADMIN_PASSWORD_HASHER.check_needs_rehash(credential.password_hash):
            replacement_hash = hash_admin_password(password)
        authenticated = await self._repository.record_auth_success(
            credential.id,
            expected_password_hash=credential.password_hash,
            now=normalized_now,
            replacement_password_hash=replacement_hash,
        )
        if authenticated is None:
            raise AuthenticationError(AUTHENTICATION_ERROR_MESSAGE)
        return _session_from(authenticated)

    async def change_password(
        self,
        admin_id: int,
        current: str,
        new: str,
    ) -> None:
        """复核当前密码后写入新 Argon2id 哈希并撤销既有会话。"""
        now = _as_utc(self._clock())
        credential = await self._require_admin(admin_id)
        if _is_locked(credential, now):
            raise AuthenticationError(AUTHENTICATION_ERROR_MESSAGE)
        if not verify_admin_password(credential.password_hash, current):
            await self._record_failure(admin_id, now)
            raise AuthenticationError(AUTHENTICATION_ERROR_MESSAGE)
        validate_new_admin_password(new)
        version = await self._repository.change_password_atomic(
            admin_id,
            expected_password_hash=credential.password_hash,
            new_password_hash=hash_admin_password(new),
            now=now,
        )
        if version is None:
            raise AuthenticationError(AUTHENTICATION_ERROR_MESSAGE)

    async def reverify(self, admin_id: int, password: str) -> None:
        """对高风险操作重新校验当前密码，不返回或记录敏感正文。"""
        now = _as_utc(self._clock())
        credential = await self._require_admin(admin_id)
        if _is_locked(credential, now):
            raise AuthenticationError(AUTHENTICATION_ERROR_MESSAGE)
        if not verify_admin_password(credential.password_hash, password):
            await self._record_failure(admin_id, now)
            raise AuthenticationError(AUTHENTICATION_ERROR_MESSAGE)
        authenticated = await self._repository.record_auth_success(
            admin_id,
            expected_password_hash=credential.password_hash,
            now=now,
            replacement_password_hash=None,
        )
        if authenticated is None:
            raise AuthenticationError(AUTHENTICATION_ERROR_MESSAGE)

    async def revoke_other_sessions(self, admin_id: int) -> int:
        """递增会话版本并返回新值，使旧版本会话在下次请求失效。"""
        version = await self._repository.increment_session_version(admin_id)
        if version is None:
            raise AuthenticationError(AUTHENTICATION_ERROR_MESSAGE)
        return version

    async def _require_admin(self, admin_id: int) -> AdminCredential:
        """读取管理员凭证，缺失时继续使用统一认证错误。"""
        credential = await self._repository.get_by_id(admin_id)
        if credential is None:
            raise AuthenticationError(AUTHENTICATION_ERROR_MESSAGE)
        return credential

    async def _record_failure(self, admin_id: int, now: datetime) -> None:
        """通过仓储原子状态转换记录一次密码失败。"""
        await self._repository.record_failed_attempt(
            admin_id,
            now=now,
            lock_until=now + LOCK_DURATION,
        )


def _as_utc(value: datetime) -> datetime:
    """统一 SQLite 的无时区时间和生产库的带时区时间。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _utc_now() -> datetime:
    """返回可持久化的当前 UTC 时间。"""
    return datetime.now(UTC)


def _is_locked(credential: AdminCredential, now: datetime) -> bool:
    """判断凭证是否仍处于锁定期，过期状态交由原子 UPDATE 重置。"""
    return (
        credential.locked_until is not None
        and _as_utc(credential.locked_until) > now
    )


def _session_from(credential: AdminCredential) -> AdminSession:
    """从 ORM 凭证提取不含密码哈希的会话身份。"""
    return AdminSession(
        admin_id=credential.id,
        employee_id=credential.employee_id,
        username=credential.username,
        must_change_password=credential.must_change_password,
        session_version=credential.session_version,
    )
