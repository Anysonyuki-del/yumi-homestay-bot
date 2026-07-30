from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import (
    CustomerIdentityProvider,
    CustomerMergeStatus,
    EmployeeRole,
)
from homestay_bot.domain.models import (
    AuditLog,
    BusinessTask,
    Conversation,
    Customer,
    CustomerIdentity,
    CustomerMergeSuggestion,
    CustomerTagLink,
    Employee,
    StayOrder,
)


class SQLAlchemyCustomerRepository:
    """使用同一数据库事务维护客户身份、合并建议和关联记录。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前消息或管理员请求的数据库会话。"""
        self._session = session

    async def ensure_identity(
        self,
        *,
        provider: CustomerIdentityProvider,
        external_id: str,
        display_name: str,
    ) -> Customer:
        """按可靠渠道身份返回客户，不存在时幂等创建正式主档。"""
        existing = await self._find_by_identity(provider, external_id)
        if existing is not None:
            return existing

        try:
            # 保存点把并发唯一键冲突限制在本次建档，不破坏整条消息事务。
            async with self._session.begin_nested():
                customer = Customer(display_name=display_name)
                identity = CustomerIdentity(
                    customer=customer,
                    provider=provider,
                    external_id=external_id,
                    is_verified=True,
                )
                self._session.add_all([customer, identity])
                await self._session.flush()
            return customer
        except IntegrityError:
            concurrent = await self._find_by_identity(provider, external_id)
            if concurrent is None:
                raise
            return concurrent

    async def suggest_unique_phone_match(
        self,
        source_customer_id: int,
        fingerprint: str,
    ) -> CustomerMergeSuggestion | None:
        """仅在手机号指纹唯一命中另一个有效客户时建立待确认建议。"""
        matches = list(
            (
                await self._session.scalars(
                    select(Customer).where(
                        Customer.id != source_customer_id,
                        Customer.phone_fingerprint == fingerprint,
                        Customer.merged_into_customer_id.is_(None),
                    )
                )
            ).all()
        )
        if len(matches) != 1:
            return None

        target = matches[0]
        existing = await self._session.scalar(
            select(CustomerMergeSuggestion).where(
                CustomerMergeSuggestion.source_customer_id == source_customer_id,
                CustomerMergeSuggestion.target_customer_id == target.id,
                CustomerMergeSuggestion.status == CustomerMergeStatus.PENDING,
            )
        )
        if existing is not None:
            return existing

        suggestion = CustomerMergeSuggestion(
            source_customer_id=source_customer_id,
            target_customer_id=target.id,
            reason="verified_phone",
        )
        self._session.add(suggestion)
        await self._session.flush()
        return suggestion

    async def merge_locked(
        self,
        suggestion_id: int,
        administrator_id: int,
    ) -> Customer:
        """锁定建议与客户后迁移现有关系，任何失败均交给外层事务回滚。"""
        administrator = await self._session.scalar(
            select(Employee).where(Employee.id == administrator_id).with_for_update()
        )
        if (
            administrator is None
            or not administrator.is_active
            or administrator.role is not EmployeeRole.ADMIN
        ):
            raise PermissionError("只有管理员可以确认客户合并")

        suggestion = await self._session.scalar(
            select(CustomerMergeSuggestion)
            .where(CustomerMergeSuggestion.id == suggestion_id)
            .with_for_update()
        )
        if suggestion is None:
            raise LookupError("客户合并建议不存在")

        target = await self._session.scalar(
            select(Customer)
            .where(Customer.id == suggestion.target_customer_id)
            .with_for_update()
        )
        if target is None:
            raise LookupError("目标客户不存在")
        if suggestion.status is CustomerMergeStatus.ACCEPTED:
            return target
        if suggestion.status is not CustomerMergeStatus.PENDING:
            raise ValueError("客户合并建议已经结束")

        source = await self._session.scalar(
            select(Customer)
            .where(Customer.id == suggestion.source_customer_id)
            .with_for_update()
        )
        if source is None or source.merged_into_customer_id is not None:
            raise ValueError("来源客户已失效或已经合并")

        await self._session.execute(
            update(CustomerIdentity)
            .where(CustomerIdentity.customer_id == source.id)
            .values(customer_id=target.id)
        )
        await self._session.execute(
            update(Conversation)
            .where(Conversation.customer_id == source.id)
            .values(customer_id=target.id)
        )
        await self._session.execute(
            update(StayOrder)
            .where(StayOrder.customer_id == source.id)
            .values(customer_id=target.id)
        )
        await self._session.execute(
            update(BusinessTask)
            .where(BusinessTask.customer_id == source.id)
            .values(customer_id=target.id)
        )
        await self._merge_tag_links(source.id, target.id)

        # 目标客户没有联系方式时才继承来源密文，避免覆盖管理员已确认资料。
        if target.phone_ciphertext is None and source.phone_ciphertext is not None:
            target.phone_ciphertext = source.phone_ciphertext
            target.phone_fingerprint = source.phone_fingerprint
        source.merged_into_customer_id = target.id
        suggestion.status = CustomerMergeStatus.ACCEPTED
        suggestion.reviewed_by = administrator.id
        suggestion.reviewed_at = datetime.now(UTC)
        self._session.add(
            AuditLog(
                actor_employee_id=administrator.id,
                action="customer_merge",
                target_type="customer",
                target_id=str(target.id),
                details={
                    "source_customer_id": source.id,
                    "target_customer_id": target.id,
                    "suggestion_id": suggestion.id,
                },
            )
        )
        await self._session.flush()
        return target

    async def _find_by_identity(
        self,
        provider: CustomerIdentityProvider,
        external_id: str,
    ) -> Customer | None:
        """按数据库唯一身份读取尚未被合并的正式客户。"""
        result = await self._session.scalars(
            select(Customer)
            .join(CustomerIdentity)
            .where(
                CustomerIdentity.provider == provider,
                CustomerIdentity.external_id == external_id,
                Customer.merged_into_customer_id.is_(None),
            )
        )
        return result.first()

    async def _merge_tag_links(
        self,
        source_customer_id: int,
        target_customer_id: int,
    ) -> None:
        """迁移来源标签，并删除目标客户已存在的重复标签关联。"""
        target_tag_ids = set(
            (
                await self._session.scalars(
                    select(CustomerTagLink.tag_id).where(
                        CustomerTagLink.customer_id == target_customer_id
                    )
                )
            ).all()
        )
        source_links = list(
            (
                await self._session.scalars(
                    select(CustomerTagLink).where(
                        CustomerTagLink.customer_id == source_customer_id
                    )
                )
            ).all()
        )
        for link in source_links:
            if link.tag_id in target_tag_ids:
                await self._session.delete(link)
                continue
            link.customer_id = target_customer_id
            target_tag_ids.add(link.tag_id)
