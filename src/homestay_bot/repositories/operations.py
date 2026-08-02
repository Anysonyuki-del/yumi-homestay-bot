from datetime import UTC, date, datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from homestay_bot.domain.enums import (
    BusinessTaskStatus,
    BusinessTaskType,
    CustomerIdentityProvider,
    RoomOperationalStatus,
)
from homestay_bot.domain.models import (
    AuditLog,
    BusinessTask,
    Customer,
    CustomerIdentity,
    Employee,
    HostexWebhookEvent,
    Job,
    LifecycleReminder,
    PropertyProfile,
    RoomOperationalState,
    StayOrder,
    TaskAttachment,
)
from homestay_bot.integrations.hostex_client import Reservation


class SQLAlchemyOperationsRepository:
    """提供运营模型的最小幂等写入入口。"""

    def __init__(self, session: AsyncSession) -> None:
        """绑定当前运营事务。"""
        self._session = session

    async def _add_business_task_once(
        self,
        task: BusinessTask,
        lookup: Select[tuple[BusinessTask]],
    ) -> tuple[BusinessTask, bool]:
        """在保存点内创建唯一任务；竞争时返回已存在任务和 False。"""
        # 先刷新外层 pending 状态，后续只捕获候选任务自身的唯一键竞争。
        await self._session.flush()
        try:
            async with self._session.begin_nested():
                # 候选任务必须在保存点建立后才加入 session，避免冲突回滚外层写入。
                self._session.add(task)
                await self._session.flush()
        except IntegrityError:
            existing = await self._session.scalar(lookup)
            if existing is None:
                raise
            return existing, False
        return task, True

    async def create_turnover(
        self,
        *,
        property_id: int,
        service_date: date,
        order_id: int | None = None,
    ) -> BusinessTask:
        """按房间和服务日幂等创建周转保洁任务。"""
        dedupe_key = f"turnover:{property_id}:{service_date.isoformat()}"
        lookup = select(BusinessTask).where(BusinessTask.dedupe_key == dedupe_key)
        existing = await self._session.scalar(lookup)
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
        saved, _ = await self._add_business_task_once(task, lookup)
        return saved

    async def create_manual_contact_for_reminder(
        self,
        reminder: LifecycleReminder,
        reason: str,
    ) -> BusinessTask:
        """按提醒编号幂等创建不含客户正文的人工联系任务。"""
        dedupe_key = f"lifecycle-manual:{reminder.id}"
        lookup = select(BusinessTask).where(BusinessTask.dedupe_key == dedupe_key)
        existing = await self._session.scalar(lookup)
        if existing is not None:
            return existing
        order = await self._session.get(StayOrder, reminder.order_id)
        if order is None:
            raise LookupError("提醒关联订单不存在")
        reason_labels = {
            "send_window_expired": "已超过 48 小时发送窗口",
            "send_count_limit": "已达到主动发送条数限制",
            "send_result_uncertain": "平台发送结果不明确",
            "verified_wecom_conversation_missing": "缺少可靠微信会话",
            "order_customer_missing": "订单尚未关联客户",
            "property_missing": "订单关联房间缺失",
            "wecom_fail_4": "已超过 48 小时发送窗口",
            "wecom_fail_5": "客服会话已关闭",
            "wecom_fail_6": "已超过 5 条主动消息限制",
            "wecom_fail_10": "客户拒收",
        }
        reason_label = reason_labels.get(reason, "企业微信发送失败")
        task = BusinessTask(
            dedupe_key=dedupe_key,
            task_type=BusinessTaskType.MANUAL_CONTACT,
            status=BusinessTaskStatus.PENDING_CONFIRMATION,
            customer_id=order.customer_id,
            order_id=order.id,
            property_id=order.property_id,
            service_date=reminder.scheduled_local_date,
            description=(
                f"主动入住提醒未能自动发送（{reason_label}），"
                "请人工联系客户。"
            ),
        )
        task, created = await self._add_business_task_once(task, lookup)
        if not created:
            return task
        self._session.add(
            AuditLog(
                actor_employee_id=None,
                action="lifecycle_manual_contact_created",
                target_type="business_task",
                target_id=str(task.id),
                details={
                    "reminder_id": reminder.id,
                    "reason": reason[:64],
                },
            )
        )
        await self._session.flush()
        return task

    async def list_all_open(
        self, *, offset: int, limit: int
    ) -> list[BusinessTask]:
        """按稳定顺序分页返回未关闭任务。"""
        return list(
            (
                await self._session.scalars(
                    select(BusinessTask)
                    .where(
                        BusinessTask.status.not_in(
                            [
                                BusinessTaskStatus.COMPLETED,
                                BusinessTaskStatus.CANCELLED,
                            ]
                        )
                    )
                    .order_by(
                        BusinessTask.service_date.asc().nullsfirst(),
                        BusinessTask.id,
                    )
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
        )

    async def list_assigned_open(
        self,
        employee_id: int,
        *,
        offset: int,
        limit: int,
    ) -> list[BusinessTask]:
        """分页返回分派给指定员工的未关闭任务。"""
        return list(
            (
                await self._session.scalars(
                    select(BusinessTask)
                    .where(
                        BusinessTask.assigned_employee_id == employee_id,
                        BusinessTask.status.not_in(
                            [
                                BusinessTaskStatus.COMPLETED,
                                BusinessTaskStatus.CANCELLED,
                            ]
                        ),
                    )
                    .order_by(
                        BusinessTask.service_date.asc().nullsfirst(),
                        BusinessTask.id,
                    )
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
        )

    async def get_task(self, task_id: int) -> BusinessTask | None:
        """按主键读取任务，不加载客户或凭证关系。"""
        return await self._session.get(BusinessTask, task_id)

    async def prepare_assignment(
        self,
        *,
        task_id: int,
        assigned_employee_id: int,
        property_id: int,
        service_date: date,
        actor_employee_id: int,
    ) -> BusinessTask:
        """锁定任务，校验执行员工并补齐分派字段。"""
        task = await self.require_for_update(task_id)
        assignee = await self._session.get(Employee, assigned_employee_id)
        if assignee is None or not assignee.is_active:
            raise ValueError("执行员工不存在或已停用")
        if task.status not in {
            BusinessTaskStatus.PENDING_CONFIRMATION,
            BusinessTaskStatus.PENDING_ASSIGNMENT,
        }:
            raise ValueError("当前任务状态不能分派")
        task.property_id = property_id
        task.service_date = service_date
        task.assigned_employee_id = assigned_employee_id
        self._session.add(
            AuditLog(
                actor_employee_id=actor_employee_id,
                action="business_task_assignment_prepared",
                target_type="business_task",
                target_id=str(task.id),
                details={
                    "assigned_employee_id": assigned_employee_id,
                    "property_id": property_id,
                    "service_date": service_date.isoformat(),
                },
            )
        )
        await self._session.flush()
        return task

    async def assignment_options(self) -> dict[str, list[object]]:
        """返回启用员工和启用房间，不包含联系方式或凭证。"""
        employees: list[object] = list(
            (
                await self._session.scalars(
                    select(Employee)
                    .where(Employee.is_active.is_(True))
                    .order_by(Employee.name, Employee.id)
                )
            ).all()
        )
        properties: list[object] = list(
            (
                await self._session.scalars(
                    select(PropertyProfile)
                    .where(PropertyProfile.is_active.is_(True))
                    .order_by(PropertyProfile.title, PropertyProfile.id)
                )
            ).all()
        )
        return {
            "employees": employees,
            "properties": properties,
        }

    async def update_task_checklist(
        self,
        *,
        task_id: int,
        employee_id: int,
        checklist: dict[str, bool],
    ) -> BusinessTask:
        """锁定任务并保存白名单检查项，审计不记录任务正文。"""
        task = await self.require_for_update(task_id)
        if task.assigned_employee_id != employee_id:
            raise PermissionError("只有任务执行员工可以更新检查清单")
        self._require_evidence_status(task)
        allowed_keys = {"clean", "supplies", "damage"}
        if set(checklist) != allowed_keys or not all(
            isinstance(value, bool) for value in checklist.values()
        ):
            raise ValueError("检查清单字段无效")
        task.checklist = dict(checklist)
        self._session.add(
            AuditLog(
                actor_employee_id=employee_id,
                action="business_task_checklist_updated",
                target_type="business_task",
                target_id=str(task.id),
                details={
                    "completed_count": sum(checklist.values()),
                    "required_count": len(allowed_keys),
                },
            )
        )
        await self._session.flush()
        return task

    async def add_task_attachment(
        self,
        *,
        task_id: int,
        file_id: str,
        uploaded_by: int,
        kind: str = "photo",
    ) -> TaskAttachment:
        """锁定任务并登记私有附件引用，不在审计中保存文件编号。"""
        task = await self.require_for_update(task_id)
        if task.assigned_employee_id != uploaded_by:
            raise PermissionError("只有任务执行员工可以上传现场照片")
        self._require_evidence_status(task)
        attachment = TaskAttachment(
            task_id=task.id,
            private_file_id=file_id,
            kind=kind,
            uploaded_by=uploaded_by,
        )
        self._session.add(attachment)
        await self._session.flush()
        self._session.add(
            AuditLog(
                actor_employee_id=uploaded_by,
                action="business_task_attachment_added",
                target_type="business_task",
                target_id=str(task.id),
                details={
                    "attachment_id": attachment.id,
                    "kind": kind,
                },
            )
        )
        await self._session.flush()
        return attachment

    async def list_task_attachments(self, task_id: int) -> list[TaskAttachment]:
        """返回任务附件元数据，不读取私有文件内容。"""
        return list(
            (
                await self._session.scalars(
                    select(TaskAttachment)
                    .where(TaskAttachment.task_id == task_id)
                    .order_by(TaskAttachment.id)
                )
            ).all()
        )

    async def get_attachment_by_file_id(
        self,
        file_id: str,
    ) -> TaskAttachment | None:
        """按随机私有文件编号查找任务附件。"""
        return cast(
            TaskAttachment | None,
            await self._session.scalar(
                select(TaskAttachment).where(
                    TaskAttachment.private_file_id == file_id
                )
            ),
        )

    async def has_photo_attachment(self, task_id: int) -> bool:
        """判断任务是否至少登记一张现场照片。"""
        attachment_id = await self._session.scalar(
            select(TaskAttachment.id)
            .where(
                TaskAttachment.task_id == task_id,
                TaskAttachment.kind == "photo",
            )
            .limit(1)
        )
        return attachment_id is not None

    async def set_room_status(
        self,
        property_id: int,
        status: RoomOperationalStatus,
        actor_employee_id: int,
    ) -> RoomOperationalState:
        """锁定房源与房态后更新状态和版本，并写入安全审计。"""
        property_profile = await self._session.scalar(
            select(PropertyProfile)
            .where(PropertyProfile.id == property_id)
            .with_for_update()
        )
        if property_profile is None:
            raise LookupError("房间不存在")
        state = await self._session.scalar(
            select(RoomOperationalState)
            .where(RoomOperationalState.property_id == property_id)
            .with_for_update()
        )
        previous = (
            state.status
            if state is not None
            else RoomOperationalStatus.NOT_STARTED
        )
        if (
            status is RoomOperationalStatus.READY
            and previous
            in {
                RoomOperationalStatus.OCCUPIED,
                RoomOperationalStatus.MAINTENANCE,
            }
        ):
            label = (
                "已入住"
                if previous is RoomOperationalStatus.OCCUPIED
                else "维修中"
            )
            raise ValueError(f"{label}房间不能直接标记为可入住")
        if state is not None and previous is status:
            return state
        if state is None:
            state = RoomOperationalState(
                property_id=property_id,
                status=status,
                changed_by=actor_employee_id,
                version=1,
            )
            self._session.add(state)
        else:
            state.status = status
            state.changed_by = actor_employee_id
            state.version += 1
        self._session.add(
            AuditLog(
                actor_employee_id=actor_employee_id,
                action="room_operational_status_changed",
                target_type="room_operational_state",
                target_id=str(property_id),
                details={
                    "from_status": previous.value,
                    "to_status": status.value,
                    "version": state.version,
                },
            )
        )
        await self._session.flush()
        return state

    async def require_room_state_for_update(
        self,
        property_id: int,
    ) -> RoomOperationalState:
        """锁定并返回已有房态，供受控状态转换复核。"""
        state = await self._session.scalar(
            select(RoomOperationalState)
            .where(RoomOperationalState.property_id == property_id)
            .with_for_update()
        )
        if state is None:
            raise LookupError("房间运营状态不存在")
        return state

    async def get_room_state(
        self,
        property_id: int,
    ) -> RoomOperationalState | None:
        """读取房间当前运营状态，不加载其他房源资料。"""
        return await self._session.get(RoomOperationalState, property_id)

    @staticmethod
    def _require_evidence_status(task: BusinessTask) -> None:
        """拒绝尚未分派或已经关闭的任务继续写入现场证据。"""
        if task.status not in {
            BusinessTaskStatus.ASSIGNED,
            BusinessTaskStatus.IN_PROGRESS,
            BusinessTaskStatus.PENDING_INSPECTION,
        }:
            raise ValueError("当前任务状态不能提交现场证据")

    async def create_pending_confirmation(
        self,
        *,
        customer_id: int,
        source_message_id: str,
        task_type: BusinessTaskType,
        description: str,
        property_id: int | None = None,
        service_date: date | None = None,
    ) -> BusinessTask:
        """按来源消息幂等保存一条 AI 待确认建议。"""
        lookup = select(BusinessTask).where(
            BusinessTask.source_message_id == source_message_id
        )
        existing = await self._session.scalar(lookup)
        if existing is not None:
            return existing
        task = BusinessTask(
            source_message_id=source_message_id,
            task_type=task_type,
            status=BusinessTaskStatus.PENDING_CONFIRMATION,
            customer_id=customer_id,
            property_id=property_id,
            service_date=service_date,
            description=description,
        )
        task, created = await self._add_business_task_once(task, lookup)
        if not created:
            return task
        self._session.add(
            AuditLog(
                actor_employee_id=None,
                action="ai_task_suggested",
                target_type="business_task",
                target_id=str(task.id),
                details={
                    "customer_id": customer_id,
                    "task_type": task_type.value,
                },
            )
        )
        await self._session.flush()
        return task

    async def require_for_update(self, task_id: int) -> BusinessTask:
        """锁定并返回一条业务任务。"""
        task = await self._session.scalar(
            select(BusinessTask)
            .where(BusinessTask.id == task_id)
            .with_for_update()
        )
        if task is None:
            raise LookupError("业务任务不存在")
        return task

    async def save_status(
        self,
        task: BusinessTask,
        target: BusinessTaskStatus,
        actor_employee_id: int | None,
    ) -> BusinessTask:
        """保存任务状态并写入不含描述正文的安全审计。"""
        previous = task.status
        task.status = target
        self._session.add(
            AuditLog(
                actor_employee_id=actor_employee_id,
                action="business_task_status_changed",
                target_type="business_task",
                target_id=str(task.id),
                details={
                    "from_status": previous.value,
                    "to_status": target.value,
                    "task_type": task.task_type.value,
                },
            )
        )
        await self._session.flush()
        return task

    async def record_handoff(
        self,
        *,
        conversation_id: int,
        customer_id: int | None,
        reason: str,
    ) -> None:
        """记录不含聊天正文和外部身份的人工接管审计。"""
        self._session.add(
            AuditLog(
                actor_employee_id=None,
                action="conversation_handoff",
                target_type="conversation",
                target_id=str(conversation_id),
                details={
                    "customer_id": customer_id,
                    "reason": reason[:64],
                },
            )
        )
        await self._session.flush()

    async def record_hostex_event(
        self,
        *,
        event_key: str,
        event_type: str,
        reservation_code: str | None,
        payload: dict[str, Any],
    ) -> bool:
        """同一事务保存首次事件和唯一后台任务。"""
        existing = await self._session.scalar(
            select(HostexWebhookEvent).where(
                HostexWebhookEvent.event_key == event_key
            )
        )
        if existing is not None:
            return False
        event = HostexWebhookEvent(
            event_key=event_key,
            event_type=event_type,
            reservation_code=reservation_code,
            payload=payload,
        )
        # 事件与 job 尚未加入 session，先让外层 pending 约束错误直接暴露。
        await self._session.flush()
        try:
            async with self._session.begin_nested():
                self._session.add(event)
                self._session.add(
                    Job(
                        job_type="hostex_event",
                        dedupe_key=f"hostex-event:{event_key}",
                        payload={"event_key": event_key},
                        available_at=datetime.now(UTC),
                    )
                )
                await self._session.flush()
        except IntegrityError:
            # 另一 worker 已写入同一事件时，保存点回滚并按幂等语义返回 False。
            return False
        return True

    async def require_pending_event(self, event_key: str) -> HostexWebhookEvent:
        """锁定并返回待处理事件。"""
        event = await self._session.scalar(
            select(HostexWebhookEvent)
            .where(
                HostexWebhookEvent.event_key == event_key,
                HostexWebhookEvent.status == "pending",
            )
            .with_for_update()
        )
        if event is None:
            raise LookupError("百居易事件不存在或已经处理")
        return event

    async def upsert_reservation(self, reservation: Reservation) -> StayOrder:
        """按订单编号 upsert 房间、百居易客户身份和入住订单。"""
        property_profile = await self._session.get(
            PropertyProfile,
            reservation.property_id,
        )
        if property_profile is None:
            property_profile = PropertyProfile(
                id=reservation.property_id,
                title=f"百居易房间 {reservation.property_id}",
            )
            self._session.add(property_profile)

        identity = await self._session.scalar(
            select(CustomerIdentity).where(
                CustomerIdentity.provider == CustomerIdentityProvider.HOSTEX,
                CustomerIdentity.external_id == reservation.reservation_code,
            )
        )
        if identity is None:
            customer = Customer(
                display_name=reservation.guest_name or "百居易客户"
            )
            identity = CustomerIdentity(
                customer=customer,
                provider=CustomerIdentityProvider.HOSTEX,
                external_id=reservation.reservation_code,
                is_verified=True,
            )
            self._session.add_all([customer, identity])
            await self._session.flush()

        order = await self._session.scalar(
            select(StayOrder).where(
                StayOrder.hostex_reservation_code == reservation.reservation_code
            )
        )
        if order is None:
            order = StayOrder(
                hostex_reservation_code=reservation.reservation_code,
                stay_code=reservation.stay_code,
                customer_id=identity.customer_id,
                property_id=reservation.property_id,
                check_in_date=reservation.check_in_date,
                check_out_date=reservation.check_out_date,
                status=reservation.status,
            )
            self._session.add(order)
        else:
            order.stay_code = reservation.stay_code
            order.customer_id = identity.customer_id
            order.property_id = reservation.property_id
            order.check_in_date = reservation.check_in_date
            order.check_out_date = reservation.check_out_date
            order.status = reservation.status
        order.last_hostex_sync_at = datetime.now(UTC)
        await self._session.flush()
        return order

    async def mark_event_completed(self, event: HostexWebhookEvent) -> bool:
        """仅把仍待处理的事件标记完成，拒绝覆盖更新后的状态。"""
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(HostexWebhookEvent)
                .where(
                    HostexWebhookEvent.id == event.id,
                    HostexWebhookEvent.status == "pending",
                )
                .values(status="completed", last_error_code=None)
                .execution_options(synchronize_session=False)
            ),
        )
        return result.rowcount == 1

    async def reconcile_reservations(
        self,
        reservations: list[Reservation],
    ) -> int:
        """逐笔复用订单 upsert，返回对账数量。"""
        for reservation in reservations:
            await self.upsert_reservation(reservation)
        return len(reservations)
