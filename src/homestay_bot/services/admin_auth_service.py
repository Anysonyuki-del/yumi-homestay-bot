from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from homestay_bot.domain.models import AdminCredential

MAX_FAILED_ATTEMPTS = 5
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

    async def get_by_username(
        self, username: str, *, for_update: bool = False
    ) -> AdminCredential | None:
        """按用户名读取凭证。"""

    async def get_by_id(
        self, admin_id: int, *, for_update: bool = False
    ) -> AdminCredential | None:
        """按凭证主键读取管理员。"""

    async def save(self, credential: AdminCredential) -> None:
        """刷新认证状态。"""


class AdminAuthService:
    """实现唯一管理员的 Argon2id 校验、锁定、改密和会话撤销。"""

    def __init__(
        self,
        repository: AdminCredentialRepository,
        *,
        password_hasher: PasswordHasher | None = None,
    ) -> None:
        """注入凭证仓储，并准备未知用户名使用的固定代价虚拟哈希。"""
        self._repository = repository
        self._password_hasher = password_hasher or PasswordHasher(type=Type.ID)
        self._dummy_hash = self._password_hasher.hash("admin-auth-dummy-password")

    async def authenticate(
        self,
        username: str,
        password: str,
        now: datetime,
    ) -> AdminSession:
        """校验密码；五次失败锁十五分钟，成功后清零认证失败状态。"""
        credential = await self._repository.get_by_username(
            username, for_update=True
        )
        if credential is None:
            self._verify(password, self._dummy_hash)
            raise AuthenticationError(AUTHENTICATION_ERROR_MESSAGE)

        normalized_now = _as_utc(now)
        if credential.locked_until is not None:
            if _as_utc(credential.locked_until) > normalized_now:
                raise AuthenticationError(AUTHENTICATION_ERROR_MESSAGE)
            # 锁定期结束后开启新的五次失败计数周期，避免首次失败立即重锁。
            credential.failed_attempts = 0
            credential.locked_until = None

        if not self._verify(password, credential.password_hash):
            credential.failed_attempts += 1
            if credential.failed_attempts >= MAX_FAILED_ATTEMPTS:
                credential.locked_until = normalized_now + LOCK_DURATION
            await self._repository.save(credential)
            raise AuthenticationError(AUTHENTICATION_ERROR_MESSAGE)

        credential.failed_attempts = 0
        credential.locked_until = None
        credential.last_authenticated_at = normalized_now
        if self._password_hasher.check_needs_rehash(credential.password_hash):
            credential.password_hash = self._password_hasher.hash(password)
        await self._repository.save(credential)
        return _session_from(credential)

    async def change_password(
        self,
        admin_id: int,
        current: str,
        new: str,
    ) -> None:
        """复核当前密码后写入新 Argon2id 哈希并撤销既有会话。"""
        credential = await self._require_admin(admin_id, for_update=True)
        if not self._verify(current, credential.password_hash):
            raise AuthenticationError(AUTHENTICATION_ERROR_MESSAGE)
        credential.password_hash = self._password_hasher.hash(new)
        credential.must_change_password = False
        credential.failed_attempts = 0
        credential.locked_until = None
        credential.session_version += 1
        await self._repository.save(credential)

    async def reverify(self, admin_id: int, password: str) -> None:
        """对高风险操作重新校验当前密码，不返回或记录敏感正文。"""
        credential = await self._require_admin(admin_id)
        if not self._verify(password, credential.password_hash):
            raise AuthenticationError(AUTHENTICATION_ERROR_MESSAGE)

    async def revoke_other_sessions(self, admin_id: int) -> int:
        """递增会话版本并返回新值，使旧版本会话在下次请求失效。"""
        credential = await self._require_admin(admin_id, for_update=True)
        credential.session_version += 1
        await self._repository.save(credential)
        return credential.session_version

    async def _require_admin(
        self, admin_id: int, *, for_update: bool = False
    ) -> AdminCredential:
        """读取管理员凭证，缺失时继续使用统一认证错误。"""
        credential = await self._repository.get_by_id(
            admin_id, for_update=for_update
        )
        if credential is None:
            raise AuthenticationError(AUTHENTICATION_ERROR_MESSAGE)
        return credential

    def _verify(self, password: str, password_hash: str) -> bool:
        """把 Argon2 的不匹配或非法哈希统一折叠为布尔失败。"""
        try:
            return self._password_hasher.verify(password_hash, password)
        except (InvalidHashError, VerificationError, VerifyMismatchError):
            return False


def _as_utc(value: datetime) -> datetime:
    """统一 SQLite 的无时区时间和生产库的带时区时间。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _session_from(credential: AdminCredential) -> AdminSession:
    """从 ORM 凭证提取不含密码哈希的会话身份。"""
    return AdminSession(
        admin_id=credential.id,
        employee_id=credential.employee_id,
        username=credential.username,
        must_change_password=credential.must_change_password,
        session_version=credential.session_version,
    )
