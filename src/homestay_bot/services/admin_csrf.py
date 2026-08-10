import hashlib
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol


class AdminCsrfRepository(Protocol):
    """定义服务端一次性 nonce 所需的持久化接口。"""

    async def create(
        self,
        *,
        token_hash: str,
        purpose: str,
        admin_id: int | None,
        expires_at: datetime,
    ) -> None:
        """保存 nonce 摘要及匹配边界。"""

    async def consume(
        self,
        *,
        token_hash: str,
        purpose: str,
        admin_id: int | None,
        now: datetime,
    ) -> bool:
        """原子消费匹配的有效 nonce。"""


class AdminCsrfService:
    """签发随机明文 nonce，并仅以 SHA-256 摘要持久化。"""

    def __init__(
        self,
        repository: AdminCsrfRepository,
        *,
        clock: Callable[[], datetime] | None = None,
        ttl: timedelta = timedelta(minutes=15),
    ) -> None:
        """注入仓储、UTC 时钟和短有效期。"""
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ttl = ttl

    async def issue(self, purpose: str, *, admin_id: int | None) -> str:
        """返回浏览器所需随机明文，同时只保存摘要。"""
        token = secrets.token_urlsafe(32)
        now = self._clock()
        await self._repository.create(
            token_hash=_hash_token(token),
            purpose=purpose,
            admin_id=admin_id,
            expires_at=now + self._ttl,
        )
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
