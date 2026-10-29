from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import CustomerIdentityProvider
from homestay_bot.domain.models import Customer, CustomerIdentity, MiniProgramSession


def _as_utc(value: datetime) -> datetime:
    """SQLite 读回的时间不带时区；本表只写入 UTC，读回时补上 UTC。"""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class SQLAlchemyMiniProgramSessionRepository:
    """在调用方事务内读写小程序会话；只接触 token 摘要，不接触明文 token。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前数据库会话。"""
        self._session = session

    async def identity_id_for(self, external_id: str) -> int | None:
        """读取小程序身份行编号；身份由客户仓储 ensure_identity 负责创建。"""
        identity_id: int | None = await self._session.scalar(
            select(CustomerIdentity.id).where(
                CustomerIdentity.provider == CustomerIdentityProvider.WECHAT_MINIPROGRAM,
                CustomerIdentity.external_id == external_id,
            )
        )
        return identity_id

    async def create(
        self,
        *,
        identity_id: int,
        token_hash: str,
        now: datetime,
        expires_at: datetime,
        max_per_identity: int,
        expired_cleanup_limit: int,
    ) -> None:
        """按固定顺序清理并插入新会话，全部步骤与建档处于同一事务。

        每身份上限是软上限：两次并发首登可能各自看到 4 条而共存 6 条，
        下一次登录即收敛；它只用于防止单个身份无限堆积会话。
        """
        await self._session.execute(
            delete(MiniProgramSession).where(
                MiniProgramSession.identity_id == identity_id,
                MiniProgramSession.expires_at <= now,
            )
        )
        active_ids = list(
            (
                await self._session.scalars(
                    select(MiniProgramSession.id)
                    .where(MiniProgramSession.identity_id == identity_id)
                    .order_by(MiniProgramSession.id)
                )
            ).all()
        )
        overflow = len(active_ids) - max_per_identity + 1
        if overflow > 0:
            # 编号单调递增，最小编号即最早创建的会话。
            await self._session.execute(
                delete(MiniProgramSession).where(
                    MiniProgramSession.id.in_(active_ids[:overflow])
                )
            )
        self._session.add(
            MiniProgramSession(
                token_hash=token_hash,
                identity_id=identity_id,
                expires_at=expires_at,
            )
        )
        await self._session.flush()
        # 全局过期清理有批量上限，避免一次登录事务删除大量行、长时间持锁。
        expired_ids = (
            select(MiniProgramSession.id)
            .where(MiniProgramSession.expires_at <= now)
            .order_by(MiniProgramSession.expires_at, MiniProgramSession.id)
            .limit(expired_cleanup_limit)
            .scalar_subquery()
        )
        await self._session.execute(
            delete(MiniProgramSession).where(MiniProgramSession.id.in_(expired_ids))
        )

    async def resolve(
        self,
        token_hash: str,
        now: datetime,
    ) -> tuple[Customer, datetime] | None:
        """按摘要解析未过期会话当前所属客户与到期时间；失效时返回 None。"""
        row = (
            await self._session.execute(
                select(Customer, MiniProgramSession.expires_at)
                .join(CustomerIdentity, CustomerIdentity.customer_id == Customer.id)
                .join(MiniProgramSession, MiniProgramSession.identity_id == CustomerIdentity.id)
                .where(
                    MiniProgramSession.token_hash == token_hash,
                    MiniProgramSession.expires_at > now,
                    # 防御性限制：小程序凭证只能对应小程序身份，即使身份行被误改也不越权。
                    CustomerIdentity.provider == CustomerIdentityProvider.WECHAT_MINIPROGRAM,
                    Customer.merged_into_customer_id.is_(None),
                )
            )
        ).first()
        if row is None:
            return None
        customer, expires_at = row
        return customer, _as_utc(expires_at)

    async def revoke(self, token_hash: str) -> None:
        """删除当前会话；会话不存在时不报错，使重复退出幂等。"""
        await self._session.execute(
            delete(MiniProgramSession).where(MiniProgramSession.token_hash == token_hash)
        )
