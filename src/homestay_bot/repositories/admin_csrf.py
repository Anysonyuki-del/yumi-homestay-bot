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
        max_active_anonymous: int,
        evict_oldest_in_scope: bool = False,
    ) -> bool:
        """同一事务清理、预占数据库配额并写入 nonce。"""
        await self._ensure_quota()
        deleted = await self._delete_expired(now=now, limit=purge_limit)
        await self._decrement_quota(deleted)
        reserved = await self._session.scalar(
            update(AdminCsrfQuota)
            .where(
                AdminCsrfQuota.id == 1,
                AdminCsrfQuota.active_count
                < _effective_max_active(
                    admin_id=admin_id,
                    max_active=max_active,
                    max_active_anonymous=max_active_anonymous,
                ),
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
        if admin_id is None:
            # 未认证访客整体只能占用独立子池，不得耗尽管理员可用的全局容量。
            anonymous_count = await self._session.scalar(
                select(func.count(AdminCsrfNonce.id)).where(
                    AdminCsrfNonce.admin_id.is_(None),
                    AdminCsrfNonce.expires_at > now,
                )
            )
            if int(anonymous_count or 0) >= max_active_anonymous:
                await self._decrement_quota(1)
                return False
        scope_count = int(
            await self._session.scalar(
                select(func.count(AdminCsrfNonce.id)).where(
                    AdminCsrfNonce.purpose == purpose,
                    admin_condition,
                    AdminCsrfNonce.expires_at > now,
                )
            )
            or 0
        )
        if scope_count >= max_active_per_scope:
            if not evict_oldest_in_scope:
                await self._decrement_quota(1)
                return False
            # 后台表单作用域已满时淘汰最旧 nonce：旧实现按实体覆盖令牌，本就只保留
            # 最后一次签发，因此淘汰不比旧行为更宽松，却能让顺序浏览不在 GET 阶段
            # 429。被淘汰的旧表单再提交仍是 409。
            evicted = await self._evict_oldest_in_scope(
                purpose=purpose,
                admin_condition=admin_condition,
                now=now,
                amount=scope_count - max_active_per_scope + 1,
            )
            await self._decrement_quota(evicted)
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

    async def _evict_oldest_in_scope(
        self,
        *,
        purpose: str,
        admin_condition: Any,
        now: datetime,
        amount: int,
    ) -> int:
        """删除作用域内最旧的若干有效 nonce 并返回精确删除数量。"""
        if amount <= 0:
            return 0
        oldest_ids = (
            select(AdminCsrfNonce.id)
            .where(
                AdminCsrfNonce.purpose == purpose,
                admin_condition,
                AdminCsrfNonce.expires_at > now,
            )
            # 同批签发的有效期相同，主键兜底保证淘汰顺序确定。
            .order_by(AdminCsrfNonce.expires_at, AdminCsrfNonce.id)
            .limit(amount)
        )
        result = await self._session.execute(
            delete(AdminCsrfNonce)
            .where(AdminCsrfNonce.id.in_(oldest_ids))
            .returning(AdminCsrfNonce.id)
            .execution_options(synchronize_session=False)
        )
        return len(result.scalars().all())

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


def _effective_max_active(
    *,
    admin_id: int | None,
    max_active: int,
    max_active_anonymous: int,
) -> int:
    """为未认证登录预留额度后的全局活动上限。

    全局配额检查先于匿名子池检查，因此 `max_active_anonymous` 只给匿名设上限、
    并不为它预留额度。管理员表单占满全局配额后，登录令牌会签发失败、登录页返回
    429，把所有人锁在门外。这里给管理员作用域单独压低天花板；预留量不超过总量的
    五分之一，避免小容量配置被压成零。
    """
    if admin_id is None:
        return max_active
    return max_active - min(max_active_anonymous, max_active // 5)
