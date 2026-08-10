import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, TypeVar

from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from homestay_bot.domain.models import AdminCredential
from homestay_bot.services.admin_passwords import (
    ADMIN_PASSWORD_HASHER,
    validate_new_admin_password,
)

LOCK_DURATION = timedelta(minutes=15)
AUTHENTICATION_ERROR_MESSAGE = "用户名或密码错误"
ARGON2_WAIT_TIMEOUT_SECONDS = 0.05
Argon2Result = TypeVar("Argon2Result")


class AuthenticationError(PermissionError):
    """表示不披露具体原因的统一管理员认证失败。"""


class Argon2CapacityError(RuntimeError):
    """表示共享 Argon2 工作容量暂时饱和。"""


class PasswordHasherPort(Protocol):
    """定义可注入且在线程中执行的 Argon2 操作。"""

    def verify(self, password_hash: str, password: str) -> bool:
        """同步校验密码。"""

    def check_needs_rehash(self, password_hash: str) -> bool:
        """同步判断哈希参数是否需要升级。"""

    def hash(self, password: str) -> str:
        """同步生成密码哈希。"""


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

    async def reverify_and_revoke_sessions(
        self,
        admin_id: int,
        *,
        expected_password_hash: str,
        expected_session_version: int,
        now: datetime,
    ) -> int | None:
        """按密码哈希和当前版本 CAS 原子撤销其他会话。"""


class AdminAuthService:
    """实现唯一管理员的 Argon2id 校验、锁定、改密和会话撤销。"""

    def __init__(
        self,
        repository: AdminCredentialRepository,
        *,
        clock: Callable[[], datetime] | None = None,
        password_hasher: PasswordHasherPort = ADMIN_PASSWORD_HASHER,
        dummy_hash: str | None = None,
        argon2_semaphore: asyncio.Semaphore | None = None,
        argon2_wait_timeout: float = ARGON2_WAIT_TIMEOUT_SECONDS,
    ) -> None:
        """注入仓储、UTC 时钟、共享 hasher 与共享虚拟哈希。"""
        self._repository = repository
        self._clock = clock or _utc_now
        self._password_hasher = password_hasher
        self._dummy_hash = dummy_hash or password_hasher.hash("admin-auth-dummy-password")
        self._argon2_semaphore = argon2_semaphore or asyncio.Semaphore(2)
        self._argon2_wait_timeout = argon2_wait_timeout

    async def authenticate(
        self,
        username: str,
        password: str,
        now: datetime,
    ) -> AdminSession:
        """校验密码；五次失败锁十五分钟，成功后清零认证失败状态。"""
        credential = await self._repository.get_by_username(username)
        if credential is None:
            await self._verify(self._dummy_hash, password)
            raise AuthenticationError(AUTHENTICATION_ERROR_MESSAGE)

        normalized_now = _as_utc(now)
        if _is_locked(credential, normalized_now):
            raise AuthenticationError(AUTHENTICATION_ERROR_MESSAGE)
        if not await self._verify(credential.password_hash, password):
            await self._record_failure(credential.id, normalized_now)
            raise AuthenticationError(AUTHENTICATION_ERROR_MESSAGE)

        replacement_hash = None
        if await self._run_argon2(
            self._password_hasher.check_needs_rehash,
            credential.password_hash,
        ):
            replacement_hash = await self._run_argon2(
                self._password_hasher.hash,
                password,
            )
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
        if not await self._verify(credential.password_hash, current):
            await self._record_failure(admin_id, now)
            raise AuthenticationError(AUTHENTICATION_ERROR_MESSAGE)
        validate_new_admin_password(new)
        version = await self._repository.change_password_atomic(
            admin_id,
            expected_password_hash=credential.password_hash,
            new_password_hash=await self._run_argon2(
                self._password_hasher.hash,
                new,
            ),
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
        if not await self._verify(credential.password_hash, password):
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

    async def reverify_and_revoke_sessions(
        self,
        admin_id: int,
        password: str,
        expected_session_version: int,
    ) -> int:
        """复核密码后以同一事务 CAS 递增版本，拒绝并发改密竞态。"""
        now = _as_utc(self._clock())
        credential = await self._require_admin(admin_id)
        if _is_locked(credential, now):
            raise AuthenticationError(AUTHENTICATION_ERROR_MESSAGE)
        if not await self._verify(credential.password_hash, password):
            await self._record_failure(admin_id, now)
            raise AuthenticationError(AUTHENTICATION_ERROR_MESSAGE)
        version = await self._repository.reverify_and_revoke_sessions(
            admin_id,
            expected_password_hash=credential.password_hash,
            expected_session_version=expected_session_version,
            now=now,
        )
        if version is None:
            raise AuthenticationError(AUTHENTICATION_ERROR_MESSAGE)
        return version

    async def _verify(self, password_hash: str, password: str) -> bool:
        """在线程中执行 Argon2，并把非法哈希或不匹配统一折叠为失败。"""
        try:
            return await self._run_argon2(
                self._password_hasher.verify,
                password_hash,
                password,
            )
        except (InvalidHashError, VerificationError, VerifyMismatchError):
            return False

    async def _run_argon2(
        self,
        operation: Callable[..., Argon2Result],
        *args: str,
    ) -> Argon2Result:
        """短等待获取共享容量，并在线程中执行一个昂贵 Argon2 操作。"""
        try:
            await asyncio.wait_for(
                self._argon2_semaphore.acquire(),
                timeout=self._argon2_wait_timeout,
            )
        except TimeoutError as error:
            raise Argon2CapacityError("管理员认证容量暂时饱和") from error
        try:
            return await asyncio.to_thread(operation, *args)
        finally:
            self._argon2_semaphore.release()

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
    return credential.locked_until is not None and _as_utc(credential.locked_until) > now


def _session_from(credential: AdminCredential) -> AdminSession:
    """从 ORM 凭证提取不含密码哈希的会话身份。"""
    return AdminSession(
        admin_id=credential.id,
        employee_id=credential.employee_id,
        username=credential.username,
        must_change_password=credential.must_change_password,
        session_version=credential.session_version,
    )
