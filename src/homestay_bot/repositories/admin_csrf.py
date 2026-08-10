from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.models import AdminCsrfNonce


class SQLAlchemyAdminCsrfRepository:
    """持久化并原子消费后台认证表单 nonce。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前短事务数据库会话。"""
        self._session = session

    async def create(
        self,
        *,
        token_hash: str,
        purpose: str,
        admin_id: int | None,
        expires_at: datetime,
    ) -> None:
        """只写入不可逆摘要、用途、主体和过期时间。"""
        self._session.add(
            AdminCsrfNonce(
                token_hash=token_hash,
                purpose=purpose,
                admin_id=admin_id,
                expires_at=expires_at,
            )
        )
        await self._session.flush()

    async def consume(
        self,
        *,
        token_hash: str,
        purpose: str,
        admin_id: int | None,
        now: datetime,
    ) -> bool:
        """用单条 DELETE RETURNING 原子匹配并消费有效 nonce。"""
        admin_condition = (
            AdminCsrfNonce.admin_id.is_(None)
            if admin_id is None
            else AdminCsrfNonce.admin_id == admin_id
        )
        statement = (
            delete(AdminCsrfNonce)
            .where(
                AdminCsrfNonce.token_hash == token_hash,
                AdminCsrfNonce.purpose == purpose,
                admin_condition,
                AdminCsrfNonce.expires_at > now,
            )
            .returning(AdminCsrfNonce.id)
            .execution_options(synchronize_session=False)
        )
        return await self._session.scalar(statement) is not None

    async def purge_expired(self, *, now: datetime, limit: int) -> int:
        """按过期索引有界删除最早记录，避免一次请求形成大事务。"""
        expired_ids = (
            select(AdminCsrfNonce.id)
            .where(AdminCsrfNonce.expires_at <= now)
            .order_by(AdminCsrfNonce.expires_at, AdminCsrfNonce.id)
            .limit(limit)
        )
        result = await self._session.execute(
            delete(AdminCsrfNonce)
            .where(AdminCsrfNonce.id.in_(expired_ids))
            .returning(AdminCsrfNonce.id)
            .execution_options(synchronize_session=False)
        )
        return len(result.scalars().all())

    async def count_active(self, *, now: datetime) -> int:
        """统计尚未过期的活动 nonce，供签发硬上限保护。"""
        count = await self._session.scalar(
            select(func.count(AdminCsrfNonce.id)).where(
                AdminCsrfNonce.expires_at > now
            )
        )
        return int(count or 0)
