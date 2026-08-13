import logging
from datetime import UTC, date, datetime

from sqlalchemy import func, select, update
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
    CustomerContextSummary,
    CustomerIdentity,
    CustomerMergeSuggestion,
    CustomerTag,
    CustomerTagLink,
    Employee,
    PropertyProfile,
    StayOrder,
)
from homestay_bot.services.customer_errors import (
    CustomerConflictError,
    CustomerNotFoundError,
    CustomerPermissionError,
)
from homestay_bot.services.latest_stay_note import (
    LatestStayCandidate,
    select_latest_stay_note,
)

logger = logging.getLogger(__name__)


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

    async def create_manual_merge_suggestion(
        self,
        source_customer_id: int,
        target_customer_id: int,
        administrator_id: int,
    ) -> int:
        """锁定管理员和两侧客户，幂等创建待二次确认的手动合并建议。"""
        administrator = await self._require_admin(administrator_id)
        if source_customer_id == target_customer_id:
            raise CustomerConflictError("不能把客户合并到自身")

        # 按主键顺序同时锁定两侧客户，降低并发反向操作形成死锁的风险。
        customers = list(
            (
                await self._session.scalars(
                    select(Customer)
                    .where(
                        Customer.id.in_(
                            [source_customer_id, target_customer_id]
                        )
                    )
                    .order_by(Customer.id)
                    .with_for_update()
                )
            ).all()
        )
        customers_by_id = {customer.id: customer for customer in customers}
        source = customers_by_id.get(source_customer_id)
        target = customers_by_id.get(target_customer_id)
        if (
            source is None
            or target is None
            or source.merged_into_customer_id is not None
            or target.merged_into_customer_id is not None
        ):
            raise CustomerNotFoundError("来源或目标客户不存在或已经合并")

        existing = await self._session.scalar(
            select(CustomerMergeSuggestion)
            .where(
                CustomerMergeSuggestion.source_customer_id
                == source_customer_id,
                CustomerMergeSuggestion.target_customer_id
                == target_customer_id,
                CustomerMergeSuggestion.status
                == CustomerMergeStatus.PENDING,
            )
            .order_by(CustomerMergeSuggestion.id)
            .with_for_update()
        )
        if existing is not None:
            return existing.id

        suggestion = CustomerMergeSuggestion(
            source_customer_id=source.id,
            target_customer_id=target.id,
            reason="administrator_manual",
            status=CustomerMergeStatus.PENDING,
        )
        self._session.add(suggestion)
        await self._session.flush()
        self._session.add(
            AuditLog(
                actor_employee_id=administrator.id,
                action="customer_manual_merge_suggested",
                target_type="customer_merge_suggestion",
                target_id=str(suggestion.id),
                details={
                    "source_customer_id": source.id,
                    "target_customer_id": target.id,
                    "suggestion_id": suggestion.id,
                },
            )
        )
        await self._session.flush()
        return suggestion.id

    async def list_customers(
        self,
        query: str | None,
        *,
        offset: int,
        limit: int,
    ) -> list[Customer]:
        """按姓名或备注稳定分页搜索尚未合并的客户。"""
        statement = select(Customer).where(
            Customer.merged_into_customer_id.is_(None)
        )
        cleaned = (query or "").strip()
        if cleaned:
            # 先转义转义符自身，再把 SQL 通配符按普通字符搜索。
            escaped = (
                cleaned[:100]
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            pattern = f"%{escaped}%"
            statement = statement.where(
                Customer.display_name.ilike(pattern, escape="\\")
                | Customer.note.ilike(pattern, escape="\\")
            )
        return list(
            (
                await self._session.scalars(
                    statement.order_by(
                        Customer.updated_at.desc(),
                        Customer.id,
                    )
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
        )

    async def latest_stay_notes(
        self,
        customer_ids: list[int],
        *,
        today: date,
    ) -> dict[int, str | None]:
        """一次查询并计算多个客户的只读最新入住备注。"""

        # 保留调用方编号全集，让没有订单的客户也得到明确的 None。
        unique_customer_ids = list(dict.fromkeys(customer_ids))
        if not unique_customer_ids:
            return {}

        statement = (
            select(
                StayOrder.id.label("order_id"),
                StayOrder.customer_id,
                StayOrder.property_id,
                PropertyProfile.title.label("property_title"),
                StayOrder.check_in_date,
                StayOrder.check_out_date,
                StayOrder.status,
                StayOrder.checkout_observed_on,
            )
            .select_from(StayOrder)
            .outerjoin(
                PropertyProfile,
                PropertyProfile.id == StayOrder.property_id,
            )
            .where(StayOrder.customer_id.in_(unique_customer_ids))
        )
        rows = (await self._session.execute(statement)).mappings().all()
        candidates_by_customer: dict[int, list[LatestStayCandidate]] = {
            customer_id: [] for customer_id in unique_customer_ids
        }
        for row in rows:
            customer_id = int(row["customer_id"])
            candidates_by_customer[customer_id].append(
                LatestStayCandidate(
                    order_id=int(row["order_id"]),
                    customer_id=customer_id,
                    property_id=int(row["property_id"]),
                    property_title=row["property_title"],
                    check_in_date=row["check_in_date"],
                    check_out_date=row["check_out_date"],
                    status=row["status"],
                    checkout_observed_on=row["checkout_observed_on"],
                )
            )

        notes: dict[int, str | None] = {}
        for customer_id, candidates in candidates_by_customer.items():
            result = select_latest_stay_note(candidates, today=today)
            notes[customer_id] = result.note
            if result.invalid_candidate_count:
                # 只记录稳定错误码和计数，不泄露订单、客户或房源信息。
                logger.warning(
                    "latest_stay_note_invalid_candidate",
                    extra={
                        "error_codes": result.error_codes,
                        "invalid_candidate_count": (
                            result.invalid_candidate_count
                        ),
                    },
                )
        return notes

    async def customer_detail(self, customer_id: int) -> dict[str, object]:
        """返回管理员 CRM 需要的标签、摘要和待合并建议。"""
        customer = await self._session.get(Customer, customer_id)
        if customer is None or customer.merged_into_customer_id is not None:
            raise CustomerNotFoundError("客户不存在或已经合并")
        tags = list(
            (
                await self._session.scalars(
                    select(CustomerTag)
                    .where(CustomerTag.is_active.is_(True))
                    .order_by(CustomerTag.name, CustomerTag.id)
                )
            ).all()
        )
        selected_tag_ids = list(
            (
                await self._session.scalars(
                    select(CustomerTagLink.tag_id).where(
                        CustomerTagLink.customer_id == customer_id
                    )
                )
            ).all()
        )
        summary = await self._session.scalar(
            select(CustomerContextSummary).where(
                CustomerContextSummary.customer_id == customer_id
            )
        )
        merge_suggestions = list(
            (
                await self._session.scalars(
                    select(CustomerMergeSuggestion)
                    .where(
                        (
                            CustomerMergeSuggestion.source_customer_id
                            == customer_id
                        )
                        | (
                            CustomerMergeSuggestion.target_customer_id
                            == customer_id
                        ),
                        CustomerMergeSuggestion.status
                        == CustomerMergeStatus.PENDING,
                    )
                    .order_by(CustomerMergeSuggestion.created_at.desc())
                )
            ).all()
        )
        return {
            "customer": customer,
            "tags": tags,
            "selected_tag_ids": selected_tag_ids,
            "summary": summary,
            "merge_suggestions": merge_suggestions,
        }

    async def merge_detail(self, suggestion_id: int) -> dict[str, object]:
        """返回待审核建议和两个客户，供管理员合并前人工对比。"""
        suggestion = await self._session.get(
            CustomerMergeSuggestion,
            suggestion_id,
        )
        if (
            suggestion is None
            or suggestion.status is not CustomerMergeStatus.PENDING
        ):
            raise CustomerNotFoundError("客户合并建议不存在或已经结束")
        source = await self._safe_merge_customer(
            suggestion.source_customer_id
        )
        target = await self._safe_merge_customer(
            suggestion.target_customer_id
        )
        if source is None or target is None:
            raise CustomerNotFoundError("合并建议关联的客户不存在")
        return {
            "suggestion": suggestion,
            "source": source,
            "target": target,
            "source_counts": await self._association_counts(
                suggestion.source_customer_id
            ),
            "target_counts": await self._association_counts(
                suggestion.target_customer_id
            ),
        }

    async def _safe_merge_customer(
        self,
        customer_id: int,
    ) -> dict[str, object] | None:
        """只选择复核页允许展示的客户编号和名称列。"""
        result = await self._session.execute(
            select(Customer.id, Customer.display_name).where(
                Customer.id == customer_id
            )
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "display_name": str(row["display_name"]),
        }

    async def _association_counts(self, customer_id: int) -> dict[str, int]:
        """只用聚合查询统计合并会迁移的关联记录，不加载敏感正文。"""
        statement = select(
            select(func.count(CustomerIdentity.id))
            .where(CustomerIdentity.customer_id == customer_id)
            .scalar_subquery()
            .label("identities"),
            select(func.count(Conversation.id))
            .where(Conversation.customer_id == customer_id)
            .scalar_subquery()
            .label("conversations"),
            select(func.count(StayOrder.id))
            .where(StayOrder.customer_id == customer_id)
            .scalar_subquery()
            .label("orders"),
            select(func.count(BusinessTask.id))
            .where(BusinessTask.customer_id == customer_id)
            .scalar_subquery()
            .label("tasks"),
        )
        row = (await self._session.execute(statement)).one()
        return {
            "identities": int(row.identities),
            "conversations": int(row.conversations),
            "orders": int(row.orders),
            "tasks": int(row.tasks),
        }

    async def replace_tags(
        self,
        customer_id: int,
        tag_ids: list[int],
        administrator_id: int,
    ) -> tuple[list[int], list[int], int]:
        """锁定客户后替换标签，并返回可同步的内部标签差异。"""
        await self._require_admin(administrator_id)
        customer = await self._session.scalar(
            select(Customer)
            .where(Customer.id == customer_id)
            .with_for_update()
        )
        if customer is None or customer.merged_into_customer_id is not None:
            raise CustomerNotFoundError("客户不存在或已经合并")
        requested = set(tag_ids)
        valid = set(
            (
                await self._session.scalars(
                    select(CustomerTag.id).where(
                        CustomerTag.id.in_(requested),
                        CustomerTag.is_active.is_(True),
                    )
                )
            ).all()
        ) if requested else set()
        if valid != requested:
            raise CustomerConflictError("包含不存在或停用的客户标签")
        links = list(
            (
                await self._session.scalars(
                    select(CustomerTagLink)
                    .where(CustomerTagLink.customer_id == customer_id)
                    .with_for_update()
                )
            ).all()
        )
        current = {item.tag_id for item in links}
        added = sorted(requested - current)
        removed = sorted(current - requested)
        for link in links:
            if link.tag_id in removed:
                await self._session.delete(link)
            elif link.tag_id in requested:
                link.sync_pending = True
                link.last_sync_error_code = None
        for tag_id in added:
            self._session.add(
                CustomerTagLink(
                    customer_id=customer_id,
                    tag_id=tag_id,
                    sync_pending=True,
                )
            )
        audit = AuditLog(
            actor_employee_id=administrator_id,
            action="customer_tags_replaced",
            target_type="customer",
            target_id=str(customer_id),
            details={
                "customer_id": customer_id,
                "added_count": len(added),
                "removed_count": len(removed),
            },
        )
        self._session.add(audit)
        await self._session.flush()
        return added, removed, audit.id

    async def update_note(
        self,
        customer_id: int,
        note: str,
        administrator_id: int,
    ) -> None:
        """更新客户备注并写入不含备注正文的审计。"""
        await self._require_admin(administrator_id)
        customer = await self._session.get(Customer, customer_id)
        if customer is None or customer.merged_into_customer_id is not None:
            raise CustomerNotFoundError("客户不存在或已经合并")
        customer.note = note or None
        self._add_customer_audit(
            administrator_id,
            "customer_note_updated",
            customer_id,
        )
        await self._session.flush()

    async def update_summary(
        self,
        *,
        customer_id: int,
        administrator_id: int,
        short_summary: str,
        long_summary: str,
        unresolved_items: list[str],
    ) -> None:
        """更正客户摘要并递增版本，审计不复制摘要正文。"""
        await self._require_admin(administrator_id)
        summary = await self._session.scalar(
            select(CustomerContextSummary)
            .where(CustomerContextSummary.customer_id == customer_id)
            .with_for_update()
        )
        if summary is None:
            if await self._session.get(Customer, customer_id) is None:
                raise CustomerNotFoundError("客户不存在")
            summary = CustomerContextSummary(customer_id=customer_id)
            self._session.add(summary)
        summary.short_summary = short_summary
        summary.long_summary = long_summary
        summary.unresolved_items = unresolved_items
        # 新建 ORM 对象在 flush 前尚未应用数据库默认值。
        summary.version = (summary.version or 0) + 1
        self._add_customer_audit(
            administrator_id,
            "customer_summary_updated",
            customer_id,
            {"version": summary.version},
        )
        await self._session.flush()

    async def delete_summary(
        self,
        customer_id: int,
        administrator_id: int,
    ) -> None:
        """物理删除客户摘要并写最小审计。"""
        await self._require_admin(administrator_id)
        summary = await self._session.scalar(
            select(CustomerContextSummary)
            .where(CustomerContextSummary.customer_id == customer_id)
            .with_for_update()
        )
        if summary is not None:
            await self._session.delete(summary)
        self._add_customer_audit(
            administrator_id,
            "customer_summary_deleted",
            customer_id,
        )
        await self._session.flush()

    async def review_merge(
        self,
        suggestion_id: int,
        administrator_id: int,
        accepted: bool,
    ) -> None:
        """确认时复用完整合并事务，拒绝时只结束建议。"""
        if accepted:
            await self.merge_locked(suggestion_id, administrator_id)
            return
        administrator = await self._require_admin(administrator_id)
        suggestion = await self._session.scalar(
            select(CustomerMergeSuggestion)
            .where(CustomerMergeSuggestion.id == suggestion_id)
            .with_for_update()
        )
        if suggestion is None:
            raise CustomerNotFoundError("客户合并建议不存在")
        if suggestion.status is not CustomerMergeStatus.PENDING:
            raise CustomerConflictError("客户合并建议已经结束")
        suggestion.status = CustomerMergeStatus.REJECTED
        suggestion.reviewed_by = administrator.id
        suggestion.reviewed_at = datetime.now(UTC)
        self._session.add(
            AuditLog(
                actor_employee_id=administrator.id,
                action="customer_merge_rejected",
                target_type="customer_merge_suggestion",
                target_id=str(suggestion.id),
                details={"suggestion_id": suggestion.id},
            )
        )
        await self._session.flush()

    async def has_verified_contact_identity(self, customer_id: int) -> bool:
        """判断客户是否有已验证 WECOM_CONTACT 身份。"""
        identity_id = await self._session.scalar(
            select(CustomerIdentity.id)
            .where(
                CustomerIdentity.customer_id == customer_id,
                CustomerIdentity.provider
                == CustomerIdentityProvider.WECOM_CONTACT,
                CustomerIdentity.is_verified.is_(True),
            )
            .limit(1)
        )
        return identity_id is not None

    async def verified_contact_id(self, customer_id: int) -> str | None:
        """返回已验证 WECOM_CONTACT 外部联系人编号。"""
        external_id = await self._session.scalar(
            select(CustomerIdentity.external_id)
            .where(
                CustomerIdentity.customer_id == customer_id,
                CustomerIdentity.provider
                == CustomerIdentityProvider.WECOM_CONTACT,
                CustomerIdentity.is_verified.is_(True),
            )
            .limit(1)
        )
        return str(external_id) if external_id is not None else None

    async def resolve_wecom_tag_ids(self, tag_ids: list[int]) -> list[str]:
        """把内部标签主键解析为非空企业微信标签编号。"""
        if not tag_ids:
            return []
        mapped_ids = (
            await self._session.scalars(
                select(CustomerTag.wecom_tag_id).where(
                    CustomerTag.id.in_(tag_ids),
                    CustomerTag.wecom_tag_id.is_not(None),
                )
            )
        ).all()
        return [
            str(mapped_id)
            for mapped_id in mapped_ids
            if mapped_id is not None
        ]

    async def mark_sync_completed(self, customer_id: int) -> None:
        """清除客户当前标签的待同步标记。"""
        links = list(
            (
                await self._session.scalars(
                    select(CustomerTagLink).where(
                        CustomerTagLink.customer_id == customer_id
                    )
                )
            ).all()
        )
        for link in links:
            link.sync_pending = False
            link.last_sync_error_code = None
        await self._session.flush()

    async def mark_sync_failed(
        self,
        customer_id: int,
        error_code: str,
    ) -> None:
        """保留客户当前标签待同步并只记录错误类型。"""
        links = list(
            (
                await self._session.scalars(
                    select(CustomerTagLink).where(
                        CustomerTagLink.customer_id == customer_id
                    )
                )
            ).all()
        )
        for link in links:
            link.sync_pending = True
            link.last_sync_error_code = error_code[:64]
        await self._session.flush()

    async def merge_locked(
        self,
        suggestion_id: int,
        administrator_id: int,
    ) -> Customer:
        """按统一层级锁定管理员、客户和建议，并由外层事务处理提交或回滚。"""
        # 无锁权限复核避免未授权调用者通过异常差异探测建议状态。
        await self._require_admin_readonly(administrator_id)
        # 先无锁读取建议快照，避免为了取得客户编号而提前锁住建议行。
        snapshot = await self._session.scalar(
            select(CustomerMergeSuggestion).where(
                CustomerMergeSuggestion.id == suggestion_id
            )
        )
        if snapshot is None:
            raise CustomerNotFoundError("客户合并建议不存在")

        # 已接受建议只做权限复核和只读链解析，不再参与写路径的行锁竞争。
        if snapshot.status is CustomerMergeStatus.ACCEPTED:
            return await self._resolve_final_customer(
                snapshot.target_customer_id
            )
        if snapshot.status is not CustomerMergeStatus.PENDING:
            raise CustomerConflictError("客户合并建议已经结束")
        snapshot_id = snapshot.id
        snapshot_source_id = snapshot.source_customer_id
        snapshot_target_id = snapshot.target_customer_id

        administrator = await self._require_admin(administrator_id)
        locked_customers = list(
            (
                await self._session.scalars(
                    select(Customer)
                    .where(
                        Customer.id.in_(
                            [
                                snapshot_source_id,
                                snapshot_target_id,
                            ]
                        )
                    )
                    .order_by(Customer.id)
                    .with_for_update()
                )
            ).all()
        )
        customers_by_id = {
            customer.id: customer for customer in locked_customers
        }

        # 当前建议和所有涉及来源的待处理建议按主键升序一次锁齐。
        locked_suggestions = list(
            (
                await self._session.scalars(
                    select(CustomerMergeSuggestion)
                    .where(
                        (
                            CustomerMergeSuggestion.id
                            == snapshot_id
                        )
                        | (
                            (
                                (
                                    CustomerMergeSuggestion.source_customer_id
                                    == snapshot_source_id
                                )
                                | (
                                    CustomerMergeSuggestion.target_customer_id
                                    == snapshot_source_id
                                )
                            )
                            & (
                                CustomerMergeSuggestion.status
                                == CustomerMergeStatus.PENDING
                            )
                        )
                    )
                    .order_by(CustomerMergeSuggestion.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        suggestion = next(
            (
                item
                for item in locked_suggestions
                if item.id == snapshot_id
            ),
            None,
        )
        if suggestion is None:
            raise CustomerNotFoundError("客户合并建议不存在")
        if (
            suggestion.source_customer_id != snapshot_source_id
            or suggestion.target_customer_id != snapshot_target_id
        ):
            raise CustomerConflictError("客户合并建议已发生变化")
        if suggestion.status is CustomerMergeStatus.ACCEPTED:
            return await self._resolve_final_customer(
                suggestion.target_customer_id
            )
        if suggestion.status is not CustomerMergeStatus.PENDING:
            raise CustomerConflictError("客户合并建议已经结束")

        source = customers_by_id.get(suggestion.source_customer_id)
        target = customers_by_id.get(suggestion.target_customer_id)
        if target is None or target.merged_into_customer_id is not None:
            raise CustomerNotFoundError("目标客户不存在或已经合并")
        if source is None or source.merged_into_customer_id is not None:
            raise CustomerConflictError("来源客户已失效或已经合并")

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
        await self._merge_customer_summaries(source.id, target.id)

        # 目标客户没有联系方式时才继承来源密文，避免覆盖管理员已确认资料。
        if target.phone_ciphertext is None and source.phone_ciphertext is not None:
            target.phone_ciphertext = source.phone_ciphertext
            target.phone_fingerprint = source.phone_fingerprint
        # 目标显示名始终保留；备注仅追加一次，重复确认会在上方直接返回。
        target.note = self._append_merged_text(
            target.note,
            source.note,
            limit=2000,
        )
        source.merged_into_customer_id = target.id
        suggestion.status = CustomerMergeStatus.ACCEPTED
        suggestion.reviewed_by = administrator.id
        suggestion.reviewed_at = datetime.now(UTC)
        self._close_source_pending_suggestions(
            locked_suggestions,
            suggestion.id,
            administrator.id,
            suggestion.reviewed_at,
        )
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

    async def _merge_customer_summaries(
        self,
        source_customer_id: int,
        target_customer_id: int,
    ) -> None:
        """迁移或合并客户摘要，不触碰摘要所依据的原始消息。"""
        summaries = list(
            (
                await self._session.scalars(
                    select(CustomerContextSummary)
                    .where(
                        CustomerContextSummary.customer_id.in_(
                            [source_customer_id, target_customer_id]
                        )
                    )
                    .with_for_update()
                )
            ).all()
        )
        summaries_by_customer = {
            summary.customer_id: summary for summary in summaries
        }
        source = summaries_by_customer.get(source_customer_id)
        target = summaries_by_customer.get(target_customer_id)
        if source is None:
            return
        if target is None:
            # 即使没有目标摘要，也在迁移前统一执行长度和数量边界。
            source.short_summary = source.short_summary[:4000]
            source.long_summary = source.long_summary[:8000]
            source.unresolved_items = list(
                dict.fromkeys(source.unresolved_items)
            )[:20]
            source.customer_id = target_customer_id
            return

        target.short_summary = (
            self._append_merged_text(
                target.short_summary,
                source.short_summary,
                limit=4000,
            )
            or ""
        )
        target.long_summary = (
            self._append_merged_text(
                target.long_summary,
                source.long_summary,
                limit=8000,
            )
            or ""
        )
        # 目标待确认项优先，随后补充来源且稳定去重，最多保留二十项。
        target.unresolved_items = list(
            dict.fromkeys(
                [
                    *target.unresolved_items,
                    *source.unresolved_items,
                ]
            )
        )[:20]
        target.version = (target.version or 0) + 1
        await self._session.delete(source)

    @staticmethod
    def _close_source_pending_suggestions(
        locked_suggestions: list[CustomerMergeSuggestion],
        accepted_suggestion_id: int,
        administrator_id: int,
        reviewed_at: datetime,
    ) -> None:
        """只修改已经按主键升序锁定的其他未决建议。"""
        for suggestion in locked_suggestions:
            if (
                suggestion.id == accepted_suggestion_id
                or suggestion.status is not CustomerMergeStatus.PENDING
            ):
                continue
            suggestion.status = CustomerMergeStatus.REJECTED
            suggestion.reason = "source_customer_merged"
            suggestion.reviewed_by = administrator_id
            suggestion.reviewed_at = reviewed_at

    @staticmethod
    def _append_merged_text(
        target_text: str | None,
        source_text: str | None,
        *,
        limit: int,
    ) -> str | None:
        """在保留目标内容优先级的前提下追加来源档案文字并截断。"""
        if not source_text:
            return target_text[:limit] if target_text else None
        if not target_text:
            return source_text[:limit]
        merged = f"{target_text}\n\n来自合并档案：\n{source_text}"
        return merged[:limit]

    async def _resolve_final_customer(self, customer_id: int) -> Customer:
        """只读沿合并链解析当前最终客户，检测异常循环或断链。"""
        visited: set[int] = set()
        current_id = customer_id
        while current_id not in visited:
            visited.add(current_id)
            customer = await self._session.scalar(
                select(Customer)
                .where(Customer.id == current_id)
            )
            if customer is None:
                raise CustomerNotFoundError("客户合并链指向不存在的客户")
            if customer.merged_into_customer_id is None:
                return customer
            current_id = customer.merged_into_customer_id
        raise CustomerConflictError("客户合并链存在循环")

    async def _require_admin_readonly(
        self,
        administrator_id: int,
    ) -> Employee:
        """只读复核活动管理员，供已完成建议的幂等重放使用。"""
        administrator = await self._session.scalar(
            select(Employee)
            .where(Employee.id == administrator_id)
            .execution_options(populate_existing=True)
        )
        if (
            administrator is None
            or not administrator.is_active
            or administrator.role is not EmployeeRole.ADMIN
        ):
            raise CustomerPermissionError("只有管理员可以确认客户合并")
        return administrator

    async def _require_admin(self, administrator_id: int) -> Employee:
        """锁定并复核活动管理员，避免只依赖页面层权限。"""
        administrator = await self._session.scalar(
            select(Employee)
            .where(Employee.id == administrator_id)
            .with_for_update()
        )
        if (
            administrator is None
            or not administrator.is_active
            or administrator.role is not EmployeeRole.ADMIN
        ):
            raise CustomerPermissionError("只有管理员可以管理客户")
        return administrator

    def _add_customer_audit(
        self,
        administrator_id: int,
        action: str,
        customer_id: int,
        details: dict[str, object] | None = None,
    ) -> None:
        """登记只含内部编号和计数的客户审计，不复制客户正文。"""
        self._session.add(
            AuditLog(
                actor_employee_id=administrator_id,
                action=action,
                target_type="customer",
                target_id=str(customer_id),
                details={
                    "customer_id": customer_id,
                    **(details or {}),
                },
            )
        )
