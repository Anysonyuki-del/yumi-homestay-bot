from datetime import datetime
from typing import Any

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.models import AdminCsrfNonce, AdminCsrfQuota


class SQLAlchemyAdminCsrfRepository:
    """持久化并原子消费后台认证表单 nonce。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前短事务数据库会话。"""
        self._session = session

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
        max_active_per_scope: int,
    ) -> bool:
        """同一事务清理、预占数据库配额并写入 nonce。"""
        await self._ensure_quota()
        deleted = await self._delete_expired(now=now, limit=purge_limit)
        await self._decrement_quota(deleted)
        reserved = await self._session.scalar(
            update(AdminCsrfQuota)
            .where(
                AdminCsrfQuota.id == 1,
                AdminCsrfQuota.active_count < max_active,
            )
            .values(active_count=AdminCsrfQuota.active_count + 1)
            .returning(AdminCsrfQuota.active_count)
        )
        if reserved is None:
            return False
        admin_condition = (
            AdminCsrfNonce.admin_id.is_(None)
            if admin_id is None
            else AdminCsrfNonce.admin_id == admin_id
        )
        scope_count = await self._session.scalar(
            select(func.count(AdminCsrfNonce.id)).where(
                AdminCsrfNonce.purpose == purpose,
                admin_condition,
                AdminCsrfNonce.expires_at > now,
            )
        )
        if int(scope_count or 0) >= max_active_per_scope:
            await self._decrement_quota(1)
            return False
        self._session.add(
            AdminCsrfNonce(
                token_hash=token_hash,
                purpose=purpose,
                admin_id=admin_id,
                expires_at=expires_at,
            )
        )
        await self._session.flush()
        return True

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
        consumed = await self._session.scalar(statement) is not None
        if consumed:
            await self._ensure_quota()
            await self._decrement_quota(1)
        return consumed

    async def purge_expired(self, *, now: datetime, limit: int) -> int:
        """按过期索引有界删除最早记录，避免一次请求形成大事务。"""
        await self._ensure_quota()
        deleted = await self._delete_expired(now=now, limit=limit)
        await self._decrement_quota(deleted)
        return deleted

    async def _delete_expired(self, *, now: datetime, limit: int) -> int:
        """删除过期 nonce 并返回精确数量，不单独修改配额。"""
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

    async def _ensure_quota(self) -> None:
        """按数据库方言幂等初始化单例配额行。"""
        dialect_name = self._session.get_bind().dialect.name
        statement: Any
        if dialect_name == "sqlite":
            statement = sqlite_insert(AdminCsrfQuota).values(id=1, active_count=0)
        elif dialect_name == "postgresql":
            statement = postgresql_insert(AdminCsrfQuota).values(id=1, active_count=0)
        else:
            raise RuntimeError(f"不支持的 CSRF 配额数据库方言: {dialect_name}")
        await self._session.execute(
            statement.on_conflict_do_nothing(index_elements=[AdminCsrfQuota.id])
        )

    async def _decrement_quota(self, amount: int) -> None:
        """按实际删除数原子递减配额，并防御历史不一致导致负数。"""
        if amount <= 0:
            return
        await self._session.execute(
            update(AdminCsrfQuota)
            .where(AdminCsrfQuota.id == 1)
            .values(
                active_count=case(
                    (
                        AdminCsrfQuota.active_count >= amount,
                        AdminCsrfQuota.active_count - amount,
                    ),
                    else_=0,
                )
            )
        )
