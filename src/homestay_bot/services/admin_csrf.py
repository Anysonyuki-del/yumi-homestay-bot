import hashlib
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol


class AdminCsrfRepository(Protocol):
    """定义服务端一次性 nonce 所需的持久化接口。"""

    async def reserve_and_create(
        self,
        *,
        token_hash: str,
        purpose: str,
        admin_id: int | None,
        expires_at: datetime,
        now: datetime,
        purge_limit: int,
        max_active: int,
    ) -> bool:
        """原子预占数据库配额并保存 nonce 摘要。"""

    async def consume(
        self,
        *,
        token_hash: str,
        purpose: str,
        admin_id: int | None,
        now: datetime,
    ) -> bool:
        """原子消费匹配的有效 nonce。"""

    async def purge_expired(self, *, now: datetime, limit: int) -> int:
        """有界清理过期 nonce。"""

class AdminCsrfCapacityError(RuntimeError):
    """表示活动 nonce 已达到应用硬上限。"""


class AdminCsrfService:
    """签发随机明文 nonce，并仅以 SHA-256 摘要持久化。"""

    def __init__(
        self,
        repository: AdminCsrfRepository,
        *,
        clock: Callable[[], datetime] | None = None,
        ttl: timedelta = timedelta(minutes=15),
        max_active: int = 1000,
        purge_limit: int = 100,
    ) -> None:
        """注入仓储、UTC 时钟和短有效期。"""
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ttl = ttl
        self._max_active = max_active
        self._purge_limit = purge_limit

    async def issue(self, purpose: str, *, admin_id: int | None) -> str:
        """返回浏览器所需随机明文，同时只保存摘要。"""
        token = secrets.token_urlsafe(32)
        now = self._clock()
        created = await self._repository.reserve_and_create(
            token_hash=_hash_token(token),
            purpose=purpose,
            admin_id=admin_id,
            expires_at=now + self._ttl,
            now=now,
            purge_limit=self._purge_limit,
            max_active=self._max_active,
        )
        if not created:
            raise AdminCsrfCapacityError("认证表单容量已满")
        return token

    async def consume(
        self,
        token: str,
        purpose: str,
        *,
        admin_id: int | None,
    ) -> bool:
        """摘要化来令牌并原子消费，绝不持久化或记录明文。"""
        return await self._repository.consume(
            token_hash=_hash_token(token),
            purpose=purpose,
            admin_id=admin_id,
            now=self._clock(),
        )


def _hash_token(token: str) -> str:
    """生成固定长度 SHA-256 十六进制摘要。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
