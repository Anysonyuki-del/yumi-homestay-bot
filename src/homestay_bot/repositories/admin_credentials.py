from datetime import datetime
from typing import Any, cast

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.models import AdminCredential
from homestay_bot.services.admin_passwords import validate_admin_password_hash

MAX_FAILED_ATTEMPTS = 5


class SQLAlchemyAdminCredentialRepository:
    """使用单例表持久化后台管理员认证状态。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前请求的数据库会话。"""
        self._session = session

    async def get_by_username(self, username: str) -> AdminCredential | None:
        """按精确用户名读取凭证快照。"""
        statement = select(AdminCredential).where(AdminCredential.username == username)
        return cast(AdminCredential | None, await self._session.scalar(statement))

    async def get_by_id(self, admin_id: int) -> AdminCredential | None:
        """按固定凭证主键读取管理员快照。"""
        statement = select(AdminCredential).where(AdminCredential.id == admin_id)
        return cast(AdminCredential | None, await self._session.scalar(statement))

    async def record_failed_attempt(
        self,
        admin_id: int,
        *,
        now: datetime,
        lock_until: datetime,
    ) -> AdminCredential | None:
        """用单条 UPDATE 原子累计失败；锁到期后从一开启新周期。"""
        expired_lock = and_(
            AdminCredential.locked_until.is_not(None),
            AdminCredential.locked_until <= now,
        )
        statement = (
            update(AdminCredential)
            .where(
                AdminCredential.id == admin_id,
                or_(
                    AdminCredential.locked_until.is_(None),
                    AdminCredential.locked_until <= now,
                ),
            )
            .values(
                failed_attempts=case(
                    (expired_lock, 1),
                    else_=AdminCredential.failed_attempts + 1,
                ),
                locked_until=case(
                    (expired_lock, None),
                    (
                        AdminCredential.failed_attempts >= MAX_FAILED_ATTEMPTS - 1,
                        lock_until,
                    ),
                    else_=AdminCredential.locked_until,
                ),
                updated_at=func.now(),
            )
            .returning(AdminCredential)
            .execution_options(populate_existing=True)
        )
        return cast(AdminCredential | None, await self._session.scalar(statement))

    async def record_auth_success(
        self,
        admin_id: int,
        *,
        expected_password_hash: str,
        now: datetime,
        replacement_password_hash: str | None,
    ) -> AdminCredential | None:
        """以密码哈希 CAS 原子清零失败状态并记录最近认证时间。"""
        values: dict[str, Any] = {
            "failed_attempts": 0,
            "locked_until": None,
            "last_authenticated_at": now,
            "updated_at": func.now(),
        }
        if replacement_password_hash is not None:
            values["password_hash"] = replacement_password_hash
        statement = (
            update(AdminCredential)
            .where(
                AdminCredential.id == admin_id,
                AdminCredential.password_hash == expected_password_hash,
                or_(
                    AdminCredential.locked_until.is_(None),
                    AdminCredential.locked_until <= now,
                ),
            )
            .values(**values)
            .returning(AdminCredential)
            .execution_options(populate_existing=True)
        )
        return cast(AdminCredential | None, await self._session.scalar(statement))

    async def change_password_atomic(
        self,
        admin_id: int,
        *,
        expected_password_hash: str,
        new_password_hash: str,
        now: datetime,
    ) -> int | None:
        """以旧哈希为 CAS 条件改密，并原子递增返回会话版本。"""
        statement = (
            update(AdminCredential)
            .where(
                AdminCredential.id == admin_id,
                AdminCredential.password_hash == expected_password_hash,
                or_(
                    AdminCredential.locked_until.is_(None),
                    AdminCredential.locked_until <= now,
                ),
            )
            .values(
                password_hash=new_password_hash,
                must_change_password=False,
                failed_attempts=0,
                locked_until=None,
                session_version=AdminCredential.session_version + 1,
                last_authenticated_at=now,
                updated_at=func.now(),
            )
            .returning(AdminCredential.session_version)
        )
        version = await self._session.scalar(statement)
        return int(version) if version is not None else None

    async def increment_session_version(self, admin_id: int) -> int | None:
        """用单条 UPDATE 原子递增并返回会话版本。"""
        statement = (
            update(AdminCredential)
            .where(AdminCredential.id == admin_id)
            .values(
                session_version=AdminCredential.session_version + 1,
                updated_at=func.now(),
            )
            .returning(AdminCredential.session_version)
        )
        version = await self._session.scalar(statement)
        return int(version) if version is not None else None

    async def reverify_and_revoke_sessions(
        self,
        admin_id: int,
        *,
        expected_password_hash: str,
        expected_session_version: int,
        now: datetime,
    ) -> int | None:
        """以密码哈希和当前版本为 CAS 条件原子撤销其他会话。"""
        statement = (
            update(AdminCredential)
            .where(
                AdminCredential.id == admin_id,
                AdminCredential.password_hash == expected_password_hash,
                AdminCredential.session_version == expected_session_version,
                or_(
                    AdminCredential.locked_until.is_(None),
                    AdminCredential.locked_until <= now,
                ),
            )
            .values(
                failed_attempts=0,
                locked_until=None,
                session_version=AdminCredential.session_version + 1,
                last_authenticated_at=now,
                updated_at=func.now(),
            )
            .returning(AdminCredential.session_version)
        )
        version = await self._session.scalar(statement)
        return int(version) if version is not None else None

    async def reverify_at_version(
        self,
        admin_id: int,
        *,
        expected_password_hash: str,
        expected_session_version: int,
        now: datetime,
    ) -> bool:
        """以密码哈希和会话版本为 CAS 条件原子记录二次认证成功。"""
        statement = (
            update(AdminCredential)
            .where(
                AdminCredential.id == admin_id,
                AdminCredential.password_hash == expected_password_hash,
                AdminCredential.session_version == expected_session_version,
                or_(
                    AdminCredential.locked_until.is_(None),
                    AdminCredential.locked_until <= now,
                ),
            )
            .values(
                failed_attempts=0,
                locked_until=None,
                last_authenticated_at=now,
                updated_at=func.now(),
            )
            .returning(AdminCredential.id)
        )
        return await self._session.scalar(statement) is not None

    async def bootstrap(
        self,
        *,
        employee_id: int,
        username: str,
        password_hash: str,
    ) -> AdminCredential:
        """仅在单例不存在时导入预生成 Argon2id 哈希，绝不接收明文。"""
        validate_admin_password_hash(password_hash)

        values = {
            "id": 1,
            "employee_id": employee_id,
            "username": username,
            "password_hash": password_hash,
            "must_change_password": True,
            "failed_attempts": 0,
            "session_version": 1,
        }
        dialect_name = self._session.get_bind().dialect.name
        statement: Any
        if dialect_name == "sqlite":
            statement = sqlite_insert(AdminCredential).values(**values)
        elif dialect_name == "postgresql":
            statement = postgresql_insert(AdminCredential).values(**values)
        else:
            raise RuntimeError(f"不支持的管理员凭证数据库方言: {dialect_name}")
        await self._session.execute(
            statement.on_conflict_do_nothing(index_elements=[AdminCredential.id])
        )
        credential = await self.get_by_id(1)
        if credential is None:
            raise RuntimeError("管理员凭证引导后无法读取")
        return credential
