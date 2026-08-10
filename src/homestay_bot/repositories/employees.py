from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import EmployeeRole
from homestay_bot.domain.models import AdminCredential, Employee


@dataclass(frozen=True, slots=True)
class ActiveAdminSession:
    """请求期复核所需且不包含密码哈希的管理员联合投影。"""

    admin_id: int
    employee_id: int
    role: EmployeeRole
    is_active: bool
    session_version: int
    must_change_password: bool


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

    async def get_active(self, employee_id: int) -> Employee | None:
        """按本地主键返回仍启用的员工。"""
        statement = select(Employee).where(
            Employee.id == employee_id,
            Employee.is_active.is_(True),
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_active_admin(
        self,
        admin_id: int,
        employee_id: int,
    ) -> ActiveAdminSession | None:
        """联合复核唯一凭证及其活动管理员员工身份。"""
        statement = (
            select(
                AdminCredential.id,
                Employee.id,
                Employee.role,
                Employee.is_active,
                AdminCredential.session_version,
                AdminCredential.must_change_password,
            )
            .join(Employee, Employee.id == AdminCredential.employee_id)
            .where(
                AdminCredential.id == admin_id,
                AdminCredential.employee_id == employee_id,
                Employee.id == employee_id,
                Employee.role == EmployeeRole.ADMIN,
                Employee.is_active.is_(True),
            )
        )
        row = (await self._session.execute(statement)).one_or_none()
        if row is None:
            return None
        return ActiveAdminSession(
            admin_id=int(row[0]),
            employee_id=int(row[1]),
            role=row[2],
            is_active=bool(row[3]),
            session_version=int(row[4]),
            must_change_password=bool(row[5]),
        )

    async def list_active_admin_userids(self) -> list[str]:
        """按稳定顺序返回所有启用管理员的企业微信 userid。"""
        result = await self._session.scalars(
            select(Employee.wecom_userid)
            .where(
                Employee.role == EmployeeRole.ADMIN,
                Employee.is_active.is_(True),
            )
            .order_by(Employee.id)
        )
        return list(result.all())
