from typing import Any, cast

from argon2 import Type, extract_parameters
from argon2.exceptions import InvalidHashError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.models import AdminCredential


class SQLAlchemyAdminCredentialRepository:
    """使用单例表持久化后台管理员认证状态。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前请求的数据库会话。"""
        self._session = session

    async def get_by_username(
        self, username: str, *, for_update: bool = False
    ) -> AdminCredential | None:
        """按精确用户名读取凭证，可选获取行锁保护认证计数。"""
        statement = select(AdminCredential).where(
            AdminCredential.username == username
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(AdminCredential | None, await self._session.scalar(statement))

    async def get_by_id(
        self, admin_id: int, *, for_update: bool = False
    ) -> AdminCredential | None:
        """按固定凭证主键读取管理员，可选锁定供改密或撤销会话。"""
        statement = select(AdminCredential).where(AdminCredential.id == admin_id)
        if for_update:
            statement = statement.with_for_update()
        return cast(AdminCredential | None, await self._session.scalar(statement))

    async def save(self, credential: AdminCredential) -> None:
        """刷新凭证状态，事务提交由调用方统一负责。"""
        self._session.add(credential)
        await self._session.flush()

    async def bootstrap(
        self,
        *,
        employee_id: int,
        username: str,
        password_hash: str,
    ) -> AdminCredential:
        """仅在单例不存在时导入预生成 Argon2id 哈希，绝不接收明文。"""
        try:
            parameters = extract_parameters(password_hash)
        except InvalidHashError as exc:
            raise ValueError("管理员引导密码必须是合法 Argon2id 哈希") from exc
        if parameters.type is not Type.ID:
            raise ValueError("管理员引导密码必须使用 Argon2id")

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
