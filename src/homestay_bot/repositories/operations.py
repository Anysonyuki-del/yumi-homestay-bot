from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import BusinessTaskStatus, BusinessTaskType
from homestay_bot.domain.models import BusinessTask


class SQLAlchemyOperationsRepository:
    """提供运营模型的最小幂等写入入口。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前运营事务。"""
        self._session = session

    async def create_turnover(
        self,
        *,
        property_id: int,
        service_date: date,
        order_id: int | None = None,
    ) -> BusinessTask:
        """按房间和服务日幂等创建周转保洁任务。"""
        dedupe_key = f"turnover:{property_id}:{service_date.isoformat()}"
        existing = await self._session.scalar(
            select(BusinessTask).where(BusinessTask.dedupe_key == dedupe_key)
        )
        if existing is not None:
            return existing
        task = BusinessTask(
            dedupe_key=dedupe_key,
            task_type=BusinessTaskType.CLEANING,
            status=BusinessTaskStatus.PENDING_ASSIGNMENT,
            order_id=order_id,
            property_id=property_id,
            service_date=service_date,
            description="退房后周转保洁",
        )
        self._session.add(task)
        await self._session.flush()
        return task
