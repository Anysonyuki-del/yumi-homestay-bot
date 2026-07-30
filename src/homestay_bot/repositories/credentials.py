from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from homestay_bot.domain.enums import (
    BusinessTaskStatus,
    BusinessTaskType,
    CredentialDeliveryStatus,
    CustomerIdentityProvider,
    MessageOrigin,
)
from homestay_bot.domain.models import (
    AuditLog,
    BusinessTask,
    Conversation,
    CredentialDelivery,
    CredentialDeliveryPart,
    CustomerIdentity,
    Message,
    RoomCredential,
    RoomOperationalState,
    StayOrder,
)
from homestay_bot.services.credential_delivery import (
    CredentialDeliveryContext,
    CredentialPartSendContext,
)


class SQLAlchemyCredentialDeliveryRepository:
    """持久化凭证安全上下文、逐部件状态和人工异常任务。"""

    _part_types = ("guide", "password", "qr")

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前凭证发送事务。"""
        self._session = session

    async def load_context_for_update(
        self,
        order_id: int,
    ) -> CredentialDeliveryContext | None:
        """锁定订单并读取最新房态、凭证和客人消息窗口。"""
        order = await self._session.scalar(
            select(StayOrder)
            .where(StayOrder.id == order_id)
            .with_for_update()
        )
        if order is None:
            return None
        return await self._context_for_order(order)

    async def ensure_delivery_parts(
        self,
        context: CredentialDeliveryContext,
    ) -> tuple[CredentialDelivery, list[CredentialDeliveryPart]]:
        """按订单和凭证版本幂等创建投递及三个唯一部件。"""
        if context.credential_id is None:
            raise ValueError("安全校验后凭证编号缺失")
        delivery = await self._session.scalar(
            select(CredentialDelivery)
            .where(
                CredentialDelivery.order_id == context.order_id,
                CredentialDelivery.credential_id == context.credential_id,
            )
            .with_for_update()
        )
        if delivery is None:
            delivery = CredentialDelivery(
                order_id=context.order_id,
                credential_id=context.credential_id,
                status=CredentialDeliveryStatus.PENDING,
            )
            self._session.add(delivery)
            await self._session.flush()
        existing_parts = list(
            (
                await self._session.scalars(
                    select(CredentialDeliveryPart)
                    .where(
                        CredentialDeliveryPart.delivery_id == delivery.id
                    )
                    .with_for_update()
                )
            ).all()
        )
        by_type = {item.part_type: item for item in existing_parts}
        for part_type in self._part_types:
            if part_type not in by_type:
                part = CredentialDeliveryPart(
                    delivery_id=delivery.id,
                    part_type=part_type,
                    status=CredentialDeliveryStatus.PENDING,
                )
                self._session.add(part)
                by_type[part_type] = part
        await self._session.flush()
        return delivery, [by_type[item] for item in self._part_types]

    async def record_exception(
        self,
        *,
        order_id: int | None,
        property_id: int,
        source_task_id: int,
        reason: str,
    ) -> None:
        """按任务和原因幂等建立不含凭证明文的管理员异常任务。"""
        dedupe_key = (
            f"credential-exception:{source_task_id}:{reason[:48]}"
        )
        existing = await self._session.scalar(
            select(BusinessTask).where(
                BusinessTask.dedupe_key == dedupe_key
            )
        )
        if existing is not None:
            return
        customer_id = None
        service_date = None
        if order_id is not None:
            order = await self._session.get(StayOrder, order_id)
            if order is not None:
                customer_id = order.customer_id
                service_date = order.check_in_date
        task = BusinessTask(
            dedupe_key=dedupe_key,
            task_type=BusinessTaskType.MANUAL_CONTACT,
            status=BusinessTaskStatus.PENDING_CONFIRMATION,
            customer_id=customer_id,
            order_id=order_id,
            property_id=property_id,
            service_date=service_date,
            description=f"入住凭证自动发送需人工处理：{reason[:48]}",
        )
        self._session.add(task)
        await self._session.flush()
        self._add_audit(
            action="credential_delivery_blocked",
            target_type="business_task",
            target_id=task.id,
            details={
                "order_id": order_id,
                "property_id": property_id,
                "reason": reason[:48],
            },
        )
        await self._session.flush()

    async def load_part_for_update(
        self,
        part_id: int,
    ) -> CredentialPartSendContext | None:
        """锁定部件并重新加载订单、房态、凭证和消息窗口。"""
        part = await self._session.scalar(
            select(CredentialDeliveryPart)
            .where(CredentialDeliveryPart.id == part_id)
            .with_for_update()
        )
        if part is None:
            return None
        delivery = await self._session.get(
            CredentialDelivery,
            part.delivery_id,
        )
        if delivery is None:
            return None
        credential = await self._session.get(
            RoomCredential,
            delivery.credential_id,
        )
        order = await self._session.scalar(
            select(StayOrder)
            .where(StayOrder.id == delivery.order_id)
            .with_for_update()
        )
        if credential is None or order is None:
            return None
        context = await self._context_for_order(
            order,
            credential=credential,
        )
        return CredentialPartSendContext(
            part_id=part.id,
            part_type=part.part_type,
            part_status=part.status,
            delivery_id=delivery.id,
            credential_version=credential.version,
            context=context,
            password_ciphertext=credential.password_ciphertext,
            guide_ciphertext=credential.guide_ciphertext,
            qr_file_id=credential.qr_file_id,
        )

    async def mark_part_sent(
        self,
        part_id: int,
        external_message_id: str,
    ) -> None:
        """记录明确成功结果，并在三个部件均成功后完成整体投递。"""
        part = await self._require_part_for_update(part_id)
        if part.status is not CredentialDeliveryStatus.PENDING:
            return
        part.status = CredentialDeliveryStatus.SENT
        part.external_message_id = external_message_id[:128]
        part.error_code = None
        delivery = await self._session.get(
            CredentialDelivery,
            part.delivery_id,
        )
        if delivery is None:
            raise LookupError("凭证投递不存在")
        parts = list(
            (
                await self._session.scalars(
                    select(CredentialDeliveryPart).where(
                        CredentialDeliveryPart.delivery_id == delivery.id
                    )
                )
            ).all()
        )
        if all(
            item.status is CredentialDeliveryStatus.SENT
            for item in parts
        ):
            delivery.status = CredentialDeliveryStatus.SENT
        self._add_audit(
            action="credential_part_sent",
            target_type="credential_delivery_part",
            target_id=part.id,
            details={
                "delivery_id": delivery.id,
                "part_type": part.part_type,
            },
        )
        await self._session.flush()

    async def mark_part_needs_review(
        self,
        part_id: int,
        error_code: str,
    ) -> None:
        """冻结不明确部件、标记整体待复核并创建人工联系任务。"""
        part = await self._require_part_for_update(part_id)
        if part.status is not CredentialDeliveryStatus.PENDING:
            return
        part.status = CredentialDeliveryStatus.NEEDS_REVIEW
        part.error_code = error_code[:64]
        delivery = await self._session.get(
            CredentialDelivery,
            part.delivery_id,
        )
        if delivery is None:
            raise LookupError("凭证投递不存在")
        delivery.status = CredentialDeliveryStatus.NEEDS_REVIEW
        order = await self._session.get(StayOrder, delivery.order_id)
        if order is None:
            raise LookupError("入住订单不存在")
        await self.record_exception(
            order_id=order.id,
            property_id=order.property_id,
            source_task_id=part.id,
            reason=f"part_{part.part_type}_{error_code[:32]}",
        )
        self._add_audit(
            action="credential_part_needs_review",
            target_type="credential_delivery_part",
            target_id=part.id,
            details={
                "delivery_id": delivery.id,
                "part_type": part.part_type,
                "error_code": error_code[:64],
            },
        )
        await self._session.flush()

    async def _context_for_order(
        self,
        order: StayOrder,
        *,
        credential: RoomCredential | None = None,
    ) -> CredentialDeliveryContext:
        """读取订单对应的当前房态、凭证和最近客人消息。"""
        room_state = await self._session.get(
            RoomOperationalState,
            order.property_id,
        )
        if credential is None:
            credential = await self._session.scalar(
                select(RoomCredential)
                .where(
                    RoomCredential.property_id == order.property_id,
                    RoomCredential.is_active.is_(True),
                )
                .order_by(RoomCredential.version.desc())
                .limit(1)
            )
        conversation = None
        last_guest_message_at = None
        wecom_identity_verified = False
        if order.customer_id is not None:
            row = (
                await self._session.execute(
                    select(Conversation, Message.sent_at)
                    .join(
                        Message,
                        Message.conversation_id == Conversation.id,
                    )
                    .where(
                        Conversation.customer_id == order.customer_id,
                        Message.origin == MessageOrigin.GUEST,
                    )
                    .order_by(Message.sent_at.desc(), Message.id.desc())
                    .limit(1)
                )
            ).first()
            if row is not None:
                conversation = row[0]
                last_guest_message_at = row[1]
                identity_id = await self._session.scalar(
                    select(CustomerIdentity.id)
                    .where(
                        CustomerIdentity.customer_id == order.customer_id,
                        CustomerIdentity.provider
                        == CustomerIdentityProvider.WECOM_KF,
                        CustomerIdentity.external_id
                        == conversation.external_userid,
                        CustomerIdentity.is_verified.is_(True),
                    )
                    .limit(1)
                )
                wecom_identity_verified = identity_id is not None
        return CredentialDeliveryContext(
            order_id=order.id,
            order_customer_id=order.customer_id,
            order_property_id=order.property_id,
            order_status=order.status,
            check_in_date=order.check_in_date,
            check_out_date=order.check_out_date,
            room_status=room_state.status if room_state is not None else None,
            credential_id=credential.id if credential is not None else None,
            credential_property_id=(
                credential.property_id if credential is not None else None
            ),
            credential_version=(
                credential.version if credential is not None else None
            ),
            credential_is_active=(
                credential.is_active if credential is not None else False
            ),
            conversation_customer_id=(
                conversation.customer_id
                if conversation is not None
                else None
            ),
            open_kfid=(
                conversation.open_kfid
                if conversation is not None
                else None
            ),
            external_userid=(
                conversation.external_userid
                if conversation is not None
                else None
            ),
            wecom_identity_verified=wecom_identity_verified,
            last_guest_message_at=last_guest_message_at,
        )

    async def _require_part_for_update(
        self,
        part_id: int,
    ) -> CredentialDeliveryPart:
        """锁定并返回凭证部件。"""
        part = await self._session.scalar(
            select(CredentialDeliveryPart)
            .where(CredentialDeliveryPart.id == part_id)
            .with_for_update()
        )
        if part is None:
            raise LookupError("凭证投递部件不存在")
        return part

    def _add_audit(
        self,
        *,
        action: str,
        target_type: str,
        target_id: int,
        details: dict[str, object],
    ) -> None:
        """审计只保存内部编号、部件类型、状态和错误类型。"""
        self._session.add(
            AuditLog(
                actor_employee_id=None,
                action=action,
                target_type=target_type,
                target_id=str(target_id),
                details=details,
            )
        )
