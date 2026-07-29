from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.models import Employee


class SQLAlchemyEmployeeRepository:
    """使用 SQLAlchemy 查询本地员工授权。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前数据库会话。"""
        self._session = session

    async def get_active_by_wecom_userid(self, userid: str) -> Employee | None:
        """只返回与企业微信 userid 匹配且已启用的员工。"""
        statement = select(Employee).where(
            Employee.wecom_userid == userid,
            Employee.is_active.is_(True),
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()
