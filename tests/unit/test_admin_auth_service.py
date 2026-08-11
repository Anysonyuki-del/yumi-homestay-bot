import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from argon2 import PasswordHasher, Type

from homestay_bot.domain.models import AdminCredential
from homestay_bot.services.admin_auth_service import (
    AdminAuthService,
    Argon2CapacityError,
    AuthenticationError,
)


class MemoryAdminCredentialRepository:
    """以内存对象模拟凭证仓储，单元测试只关注认证规则。"""

    def __init__(self, credential: AdminCredential | None) -> None:
        """保存唯一管理员凭证。"""
        self.credential = credential

    async def get_by_username(self, username: str) -> AdminCredential | None:
        """按用户名返回唯一凭证。"""
        if self.credential is not None and self.credential.username == username:
            return self.credential
        return None

    async def get_by_id(self, admin_id: int) -> AdminCredential | None:
        """按管理员凭证主键返回唯一凭证。"""
        if self.credential is not None and self.credential.id == admin_id:
            return self.credential
        return None

    async def record_failed_attempt(
        self,
        admin_id: int,
        *,
        now: datetime,
        lock_until: datetime,
    ) -> AdminCredential | None:
        """按生产仓储规则原子模拟失败计数。"""
        credential = await self.get_by_id(admin_id)
        if credential is None:
            return None
        if credential.locked_until is not None:
            if credential.locked_until > now:
                return None
            credential.failed_attempts = 1
            credential.locked_until = None
            return credential
        credential.failed_attempts += 1
        if credential.failed_attempts >= 5:
            credential.locked_until = lock_until
        return credential

    async def record_auth_success(
        self,
        admin_id: int,
        *,
        expected_password_hash: str,
        now: datetime,
        replacement_password_hash: str | None,
    ) -> AdminCredential | None:
        """按旧哈希条件模拟成功认证状态转换。"""
        credential = await self.get_by_id(admin_id)
        if credential is None or credential.password_hash != expected_password_hash:
            return None
        credential.failed_attempts = 0
        credential.locked_until = None
        credential.last_authenticated_at = now
        if replacement_password_hash is not None:
            credential.password_hash = replacement_password_hash
        return credential

    async def change_password_atomic(
        self,
        admin_id: int,
        *,
        expected_password_hash: str,
        new_password_hash: str,
        now: datetime,
    ) -> int | None:
        """按旧哈希条件模拟原子改密和版本递增。"""
        credential = await self.get_by_id(admin_id)
        if credential is None or credential.password_hash != expected_password_hash:
            return None
        credential.password_hash = new_password_hash
        credential.must_change_password = False
        credential.failed_attempts = 0
        credential.locked_until = None
        credential.last_authenticated_at = now
        credential.session_version += 1
        return credential.session_version

    async def increment_session_version(self, admin_id: int) -> int | None:
        """模拟数据库原子版本递增。"""
        credential = await self.get_by_id(admin_id)
        if credential is None:
            return None
        credential.session_version += 1
        return credential.session_version

    async def reverify_and_revoke_sessions(
        self,
        admin_id: int,
        *,
        expected_password_hash: str,
        expected_session_version: int,
        now: datetime,
    ) -> int | None:
        """模拟密码哈希与会话版本双条件 CAS。"""
        credential = await self.get_by_id(admin_id)
        if (
            credential is None
            or credential.password_hash != expected_password_hash
            or credential.session_version != expected_session_version
        ):
            return None
        credential.session_version += 1
        credential.failed_attempts = 0
        credential.locked_until = None
        credential.last_authenticated_at = now
        return credential.session_version

    async def reverify_at_version(
        self,
        admin_id: int,
        *,
        expected_password_hash: str,
        expected_session_version: int,
        now: datetime,
    ) -> bool:
        """按密码哈希和页面会话版本模拟不递增版本的认证 CAS。"""
        credential = await self.get_by_id(admin_id)
        if (
            credential is None
            or credential.password_hash != expected_password_hash
            or credential.session_version != expected_session_version
        ):
            return False
        credential.failed_attempts = 0
        credential.locked_until = None
        credential.last_authenticated_at = now
        return True


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

    session = await AdminAuthService(repository).authenticate("admin", "initial-password", now)

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
async def test_expired_lock_starts_a_new_failure_cycle() -> None:
    """锁定到期后的首次错误密码应计为一，不得立刻再次锁定。"""
    now = datetime(2026, 8, 11, 8, tzinfo=UTC)
    credential = _credential()
    credential.failed_attempts = 5
    credential.locked_until = now
    service = AdminAuthService(MemoryAdminCredentialRepository(credential))

    with pytest.raises(AuthenticationError):
        await service.authenticate(
            "admin",
            "wrong-password",
            now + timedelta(seconds=1),
        )

    assert credential.failed_attempts == 1
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


@pytest.mark.asyncio
async def test_reverify_at_version_rejects_stale_session_without_mutating_version() -> None:
    """敏感操作复核必须绑定页面会话版本，且成功不能自行撤销会话。"""
    credential = _credential()
    credential.session_version = 4
    service = AdminAuthService(MemoryAdminCredentialRepository(credential))

    await service.reverify_at_version(1, "initial-password", 4)
    assert credential.session_version == 4

    with pytest.raises(AuthenticationError):
        await service.reverify_at_version(1, "initial-password", 3)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "new_password",
    ["", "   ", "short-pass", "x" * 129],
)
async def test_change_password_rejects_invalid_new_password(
    new_password: str,
) -> None:
    """新密码必须非空白且长度保持在 12 到 128 个字符。"""
    credential = _credential()
    service = AdminAuthService(MemoryAdminCredentialRepository(credential))

    with pytest.raises(ValueError):
        await service.change_password(
            credential.id,
            "initial-password",
            new_password,
        )


@pytest.mark.asyncio
async def test_change_password_accepts_twelve_to_128_characters() -> None:
    """符合长度边界且非空白的新密码应成功写入。"""
    credential = _credential()
    service = AdminAuthService(MemoryAdminCredentialRepository(credential))

    await service.change_password(
        credential.id,
        "initial-password",
        "x" * 12,
    )
    await service.change_password(
        credential.id,
        "x" * 12,
        "y" * 128,
    )

    await service.reverify(credential.id, "y" * 128)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["reverify", "change_password"])
async def test_current_password_failure_uses_persistent_lock_counter(
    operation: str,
) -> None:
    """二次验证和改密的当前密码错误必须进入与登录相同的锁定计数。"""
    now = datetime(2026, 8, 11, 9, tzinfo=UTC)
    credential = _credential()
    service = AdminAuthService(
        MemoryAdminCredentialRepository(credential),
        clock=lambda: now,
    )

    with pytest.raises(AuthenticationError):
        if operation == "reverify":
            await service.reverify(credential.id, "wrong-password")
        else:
            await service.change_password(
                credential.id,
                "wrong-password",
                "new-secure-password",
            )

    assert credential.failed_attempts == 1


@pytest.mark.asyncio
async def test_argon2_verification_runs_outside_event_loop() -> None:
    """耗时 Argon2 校验必须在线程执行，不能阻塞异步请求循环。"""

    class SlowHasher:
        """用阻塞睡眠模拟 Argon2 CPU 工作。"""

        def verify(self, password_hash: str, password: str) -> bool:
            """阻塞后返回密码匹配。"""
            time.sleep(0.08)
            return password == "initial-password"

        def check_needs_rehash(self, password_hash: str) -> bool:
            """测试哈希无需升级。"""
            return False

        def hash(self, password: str) -> str:
            """返回固定测试哈希。"""
            return "slow-hash"

    credential = _credential()
    service = AdminAuthService(
        MemoryAdminCredentialRepository(credential),
        password_hasher=SlowHasher(),
        dummy_hash="dummy-hash",
    )
    heartbeat_elapsed = 0.0

    async def heartbeat() -> None:
        """测量认证执行期间事件循环是否仍能调度。"""
        nonlocal heartbeat_elapsed
        started = time.monotonic()
        await asyncio.sleep(0.01)
        heartbeat_elapsed = time.monotonic() - started

    await asyncio.gather(
        service.authenticate(
            "admin",
            "initial-password",
            datetime(2026, 8, 11, 8, tzinfo=UTC),
        ),
        heartbeat(),
    )

    assert heartbeat_elapsed < 0.05


@pytest.mark.asyncio
async def test_argon2_capacity_saturation_fails_fast() -> None:
    """共享 Argon2 容量被占满时后续认证应短等待后稳定快速失败。"""

    class BlockingHasher:
        """用线程事件模拟尚未完成的昂贵 Argon2 校验。"""

        def verify(self, password_hash: str, password: str) -> bool:
            """阻塞足够久，使第二个认证触发容量超时。"""
            time.sleep(0.1)
            return True

        def check_needs_rehash(self, password_hash: str) -> bool:
            """避免测试进入额外哈希阶段。"""
            return False

        def hash(self, password: str) -> str:
            """返回固定测试哈希。"""
            return "blocking-hash"

    credential = _credential()
    semaphore = asyncio.Semaphore(1)
    service = AdminAuthService(
        MemoryAdminCredentialRepository(credential),
        password_hasher=BlockingHasher(),
        dummy_hash="dummy-hash",
        argon2_semaphore=semaphore,
        argon2_wait_timeout=0.01,
    )
    first = asyncio.create_task(
        service.authenticate(
            "admin",
            "initial-password",
            datetime(2026, 8, 11, 8, tzinfo=UTC),
        )
    )
    await asyncio.sleep(0.01)

    with pytest.raises(Argon2CapacityError):
        await service.authenticate(
            "admin",
            "initial-password",
            datetime(2026, 8, 11, 8, tzinfo=UTC),
        )
    await first


@pytest.mark.asyncio
async def test_cancelled_argon_waiter_cannot_exceed_physical_worker_limit() -> None:
    """取消等待线程结果的请求后，新请求也不能令底层 Argon 并发超过线程池上限。"""
    release = threading.Event()
    two_started = threading.Event()
    lock = threading.Lock()
    active = 0
    maximum_active = 0

    class TrackingHasher:
        """记录真实工作线程中的同时执行数量。"""

        def verify(self, password_hash: str, password: str) -> bool:
            """占住工作线程直到测试统一释放。"""
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
                if active == 2:
                    two_started.set()
            release.wait(timeout=1)
            with lock:
                active -= 1
            return True

        def check_needs_rehash(self, password_hash: str) -> bool:
            """避免认证成功后进入额外哈希任务。"""
            return False

        def hash(self, password: str) -> str:
            """返回固定测试哈希。"""
            return "tracking-hash"

    credential = _credential()
    executor = ThreadPoolExecutor(max_workers=2)
    service = AdminAuthService(
        MemoryAdminCredentialRepository(credential),
        password_hasher=TrackingHasher(),
        dummy_hash="dummy-hash",
        argon2_semaphore=asyncio.Semaphore(2),
        argon2_executor=executor,
        argon2_wait_timeout=0.2,
    )
    now = datetime(2026, 8, 11, 8, tzinfo=UTC)
    first = asyncio.create_task(service.authenticate("admin", "initial-password", now))
    second = asyncio.create_task(service.authenticate("admin", "initial-password", now))
    assert await asyncio.to_thread(two_started.wait, 0.5)

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    third = asyncio.create_task(service.authenticate("admin", "initial-password", now))
    await asyncio.sleep(0.05)
    release.set()
    await asyncio.gather(second, third)
    executor.shutdown(wait=True)

    assert maximum_active <= 2
