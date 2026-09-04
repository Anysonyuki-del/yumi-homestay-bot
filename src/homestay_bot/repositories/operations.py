from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

from sqlalchemy import String, and_, exists, func, literal, or_, select, update
from sqlalchemy import cast as sa_cast
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from homestay_bot.domain.enums import (
    BusinessTaskOrigin,
    BusinessTaskStatus,
    BusinessTaskType,
    CustomerIdentityProvider,
    RoomOperationalStatus,
    TaskClosureReason,
    TaskClosureSource,
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
from homestay_bot.domain.stay_status import (
    is_checked_out_stay_status,
    is_excluded_stay_status,
)
from homestay_bot.domain.task_lifecycle import (
    TaskLifecycleCandidate,
    local_service_window_expires_at,
    manual_contact_expires_at,
)
from homestay_bot.integrations.hostex_client import Reservation

WUHAN_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _wuhan_today() -> date:
    """返回武汉本地日期，避免 UTC 跨日导致退房观察日偏移。"""
    return datetime.now(WUHAN_TIMEZONE).date()


class SQLAlchemyOperationsRepository:
    """提供运营模型的最小幂等写入入口。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        local_date_provider: Callable[[], date] | None = None,
    ) -> None:
        """绑定当前运营事务。"""
        self._session = session
        self._local_date_provider = local_date_provider or _wuhan_today

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
            origin_kind=BusinessTaskOrigin.TURNOVER,
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
            origin_kind=BusinessTaskOrigin.LIFECYCLE_REMINDER,
            customer_id=order.customer_id,
            order_id=order.id,
            property_id=order.property_id,
            service_date=reminder.scheduled_local_date,
            description=(
                f"主动入住提醒未能自动发送（{reason_label}），"
                "请人工联系客户。"
            ),
            expires_at=manual_contact_expires_at(
                reminder.reminder_type,
                reminder.scheduled_at,
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
        self,
        *,
        offset: int,
        limit: int,
        status: BusinessTaskStatus | None = None,
        task_type: BusinessTaskType | None = None,
        service_date: date | None = None,
        property_id: int | None = None,
        assigned_employee_id: int | None = None,
        overdue_before: date | None = None,
        archived: bool = False,
    ) -> list[BusinessTask]:
        """按稳定顺序分页返回未关闭任务；默认排除已归档。"""
        conditions: list[Any] = [
            BusinessTask.archived_at.is_not(None)
            if archived
            else BusinessTask.archived_at.is_(None)
        ]
        if status is None and not archived:
            # 归档只收终态任务，归档视图再叠加「仅开放态」会恒为空。
            conditions.append(
                BusinessTask.status.not_in(
                    [
                        BusinessTaskStatus.COMPLETED,
                        BusinessTaskStatus.CANCELLED,
                        BusinessTaskStatus.EXPIRED,
                    ]
                )
            )
        for value, column in (
            (task_type, BusinessTask.task_type),
            (service_date, BusinessTask.service_date),
            (property_id, BusinessTask.property_id),
            (assigned_employee_id, BusinessTask.assigned_employee_id),
        ):
            if value is not None:
                conditions.append(column == value)
        if overdue_before is not None:
            conditions.append(BusinessTask.service_date < overdue_before)
        if status is not None:
            # 显式筛选终态时允许管理员查看历史；默认列表仍只展示开放任务。
            conditions.append(BusinessTask.status == status)
        return list(
            (
                await self._session.scalars(
                    select(BusinessTask).where(*conditions)
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
        status: BusinessTaskStatus | None = None,
        task_type: BusinessTaskType | None = None,
        service_date: date | None = None,
        property_id: int | None = None,
        overdue_before: date | None = None,
    ) -> list[BusinessTask]:
        """分页返回分派给指定员工的未关闭任务。"""
        conditions: list[Any] = [BusinessTask.assigned_employee_id == employee_id]
        if status is None:
            conditions.append(
                BusinessTask.status.not_in(
                    [
                        BusinessTaskStatus.COMPLETED,
                        BusinessTaskStatus.CANCELLED,
                        BusinessTaskStatus.EXPIRED,
                    ]
                )
            )
        for value, column in (
            (task_type, BusinessTask.task_type),
            (service_date, BusinessTask.service_date),
            (property_id, BusinessTask.property_id),
        ):
            if value is not None:
                conditions.append(column == value)
        if overdue_before is not None:
            conditions.append(BusinessTask.service_date < overdue_before)
        if status is not None:
            # 普通员工只能看到曾经分派给自己的终态任务。
            conditions.append(BusinessTask.status == status)
        return list(
            (
                await self._session.scalars(
                    select(BusinessTask).where(*conditions)
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

    _ARCHIVABLE_STATUSES = (
        BusinessTaskStatus.COMPLETED,
        BusinessTaskStatus.CANCELLED,
        BusinessTaskStatus.EXPIRED,
    )

    async def archive_task(
        self,
        task_id: int,
        actor_employee_id: int,
    ) -> BusinessTask:
        """把单条终态任务移入归档，开放中的任务拒绝归档。"""
        task = await self._session.scalar(
            select(BusinessTask)
            .where(BusinessTask.id == task_id)
            .with_for_update()
        )
        if task is None:
            raise LookupError("任务不存在")
        if task.status not in self._ARCHIVABLE_STATUSES:
            raise ValueError("只有已完成、已取消或已失效的任务可以归档")
        if task.archived_at is not None:
            return task
        task.archived_at = datetime.now(UTC)
        task.archived_by_employee_id = actor_employee_id
        self._session.add(
            AuditLog(
                actor_employee_id=actor_employee_id,
                action="business_task_archived",
                target_type="business_task",
                target_id=str(task_id),
                details={"status": task.status.value, "count": 1},
            )
        )
        await self._session.flush()
        return task

    async def restore_task(
        self,
        task_id: int,
        actor_employee_id: int,
    ) -> BusinessTask:
        """把任务移出归档，状态本身不变。"""
        task = await self._session.scalar(
            select(BusinessTask)
            .where(BusinessTask.id == task_id)
            .with_for_update()
        )
        if task is None:
            raise LookupError("任务不存在")
        if task.archived_at is None:
            return task
        task.archived_at = None
        task.archived_by_employee_id = None
        self._session.add(
            AuditLog(
                actor_employee_id=actor_employee_id,
                action="business_task_restored",
                target_type="business_task",
                target_id=str(task_id),
                details={"status": task.status.value},
            )
        )
        await self._session.flush()
        return task

    async def archive_selected(
        self,
        task_ids: list[int],
        actor_employee_id: int,
    ) -> int:
        """按显式勾选的编号归档，返回归档数量。

        勾选里混入开放态任务时拒绝整批而不是静默跳过：用户以为都归档了、
        实际漏了几条，比直接报错更难发现。
        """
        if not task_ids:
            raise ValueError("请先勾选要归档的任务")
        unique_ids = sorted(set(task_ids))
        tasks = list(
            await self._session.scalars(
                select(BusinessTask)
                .where(BusinessTask.id.in_(unique_ids))
                .with_for_update()
            )
        )
        found = {task.id for task in tasks}
        if missing := sorted(set(unique_ids) - found):
            raise LookupError(f"任务不存在：{missing}")
        if blocked := sorted(
            task.id
            for task in tasks
            if task.status not in self._ARCHIVABLE_STATUSES
        ):
            raise ValueError(
                f"只有已完成、已取消或已失效的任务可以归档，以下仍在处理中：{blocked}"
            )
        now = datetime.now(UTC)
        archived = 0
        for task in tasks:
            if task.archived_at is not None:
                continue
            task.archived_at = now
            task.archived_by_employee_id = actor_employee_id
            archived += 1
        if archived:
            self._session.add(
                AuditLog(
                    actor_employee_id=actor_employee_id,
                    action="business_task_archived",
                    target_type="business_task",
                    target_id="selection",
                    details={"count": archived, "task_ids": unique_ids},
                )
            )
        await self._session.flush()
        return archived

    async def archive_matching(
        self,
        actor_employee_id: int,
        *,
        status: BusinessTaskStatus | None = None,
        task_type: BusinessTaskType | None = None,
        service_date: date | None = None,
        property_id: int | None = None,
        assigned_employee_id: int | None = None,
    ) -> int:
        """按当前筛选条件批量归档终态任务，返回归档数量。

        筛选条件即选择范围：394 条失效任务逐条点击不现实，而为此新建一套
        多选提交系统又过重，列表页既有的筛选器本身就是最自然的选择方式。
        """
        conditions: list[Any] = [
            BusinessTask.archived_at.is_(None),
            BusinessTask.status.in_(self._ARCHIVABLE_STATUSES),
        ]
        if status is not None:
            conditions.append(BusinessTask.status == status)
        for value, column in (
            (task_type, BusinessTask.task_type),
            (service_date, BusinessTask.service_date),
            (property_id, BusinessTask.property_id),
            (assigned_employee_id, BusinessTask.assigned_employee_id),
        ):
            if value is not None:
                conditions.append(column == value)
        now = datetime.now(UTC)
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(BusinessTask)
                .where(*conditions)
                .values(archived_at=now, archived_by_employee_id=actor_employee_id)
            ),
        )
        archived = int(result.rowcount or 0)
        if archived:
            # 批量只记一条含数量与条件的汇总，不为每条任务各写一条审计。
            self._session.add(
                AuditLog(
                    actor_employee_id=actor_employee_id,
                    action="business_task_archived",
                    target_type="business_task",
                    target_id="bulk",
                    details={
                        "count": archived,
                        "status": status.value if status else None,
                        "task_type": task_type.value if task_type else None,
                        "property_id": property_id,
                    },
                )
            )
        await self._session.flush()
        return archived

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

    async def record_manual_override(
        self,
        *,
        property_id: int,
        actor_employee_id: int,
        status: RoomOperationalStatus,
    ) -> None:
        """记录一次未经清单与照片证据的人工房态覆盖。

        set_room_status 自带的审计只说明房态从什么变成什么，不区分来源。
        向客人发放门锁密码要求房态为 READY，因此必须能分辨这个 READY 是
        走完证据流程得到的，还是管理员直接设定的。
        """
        self._session.add(
            AuditLog(
                actor_employee_id=actor_employee_id,
                action="room_status_manual_override",
                target_type="room_operational_state",
                target_id=str(property_id),
                details={
                    "to_status": status.value,
                    "evidence_required": False,
                },
            )
        )
        await self._session.flush()

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
            origin_kind=BusinessTaskOrigin.AI_SUGGESTION,
            customer_id=customer_id,
            property_id=property_id,
            service_date=service_date,
            description=description,
            expires_at=(
                local_service_window_expires_at(service_date)
                if service_date is not None
                and task_type
                in {
                    BusinessTaskType.EARLY_CHECK_IN,
                    BusinessTaskType.LATE_CHECK_OUT,
                }
                else None
            ),
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
        if target in {
            BusinessTaskStatus.COMPLETED,
            BusinessTaskStatus.CANCELLED,
        }:
            # 人工完成或取消也写入统一关闭元数据，便于审计区分系统失效。
            task.closed_at = datetime.now(UTC)
            task.closure_source = TaskClosureSource.EMPLOYEE
            task.closed_by_employee_id = actor_employee_id
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

    async def list_lifecycle_candidates(
        self,
        *,
        now: datetime,
        limit: int,
        order_id: int | None = None,
    ) -> tuple[TaskLifecycleCandidate, ...]:
        """批量读取任务治理需要的最小投影，不读取任务描述。"""
        reminder_key = literal("lifecycle-manual:") + sa_cast(
            LifecycleReminder.id,
            String(),
        )
        attachment_exists = exists(
            select(TaskAttachment.id).where(TaskAttachment.task_id == BusinessTask.id)
        )
        local_today = now.astimezone(WUHAN_TIMEZONE).date()
        cancelled_order = and_(
            func.lower(func.trim(StayOrder.status)).in_(
                ("cancelled", "canceled", "declined", "expired", "deleted")
            ),
            BusinessTask.task_type.in_(
                (
                    BusinessTaskType.CLEANING,
                    BusinessTaskType.MANUAL_CONTACT,
                    BusinessTaskType.EARLY_CHECK_IN,
                    BusinessTaskType.LATE_CHECK_OUT,
                )
            ),
        )
        expired_window = or_(
            BusinessTask.expires_at <= now,
            and_(
                BusinessTask.task_type == BusinessTaskType.MANUAL_CONTACT,
                LifecycleReminder.scheduled_at <= now,
            ),
            and_(
                BusinessTask.task_type.in_(
                    (
                        BusinessTaskType.EARLY_CHECK_IN,
                        BusinessTaskType.LATE_CHECK_OUT,
                    )
                ),
                BusinessTask.service_date < local_today,
            ),
        )
        statement = (
            select(
                BusinessTask.id,
                BusinessTask.order_id,
                BusinessTask.task_type,
                BusinessTask.status,
                BusinessTask.origin_kind,
                StayOrder.status,
                BusinessTask.service_date,
                BusinessTask.assigned_employee_id,
                BusinessTask.checklist,
                attachment_exists.label("has_attachments"),
                LifecycleReminder.reminder_type,
                LifecycleReminder.scheduled_at,
                BusinessTask.expires_at,
            )
            .outerjoin(StayOrder, StayOrder.id == BusinessTask.order_id)
            .outerjoin(
                LifecycleReminder,
                BusinessTask.dedupe_key == reminder_key,
            )
            .where(
                BusinessTask.status.in_(
                    (
                        BusinessTaskStatus.PENDING_CONFIRMATION,
                        BusinessTaskStatus.PENDING_ASSIGNMENT,
                    )
                ),
                or_(cancelled_order, expired_window),
            )
            .order_by(BusinessTask.updated_at, BusinessTask.id)
            .limit(limit)
        )
        if order_id is not None:
            statement = statement.where(BusinessTask.order_id == order_id)
        rows = await self._session.execute(statement)
        return tuple(
            TaskLifecycleCandidate(
                task_id=task_id,
                order_id=selected_order_id,
                task_type=task_type,
                status=status,
                origin_kind=origin_kind,
                order_status=order_status,
                service_date=service_date,
                assigned_employee_id=assigned_employee_id,
                has_checklist=bool(checklist),
                has_attachments=bool(has_attachments),
                reminder_type=reminder_type,
                reminder_scheduled_at=reminder_scheduled_at,
                expires_at=expires_at,
            )
            for (
                task_id,
                selected_order_id,
                task_type,
                status,
                origin_kind,
                order_status,
                service_date,
                assigned_employee_id,
                checklist,
                has_attachments,
                reminder_type,
                reminder_scheduled_at,
                expires_at,
            ) in rows
        )

    async def expire_if_safe(
        self,
        task_id: int,
        *,
        reason: TaskClosureReason,
        now: datetime,
    ) -> bool:
        """锁定后再次校验执行证据，再把任务写入失效终态。"""
        task = await self._session.scalar(
            select(BusinessTask)
            .where(BusinessTask.id == task_id)
            .with_for_update()
        )
        if task is None or task.status not in {
            BusinessTaskStatus.PENDING_CONFIRMATION,
            BusinessTaskStatus.PENDING_ASSIGNMENT,
        }:
            return False
        if task.assigned_employee_id is not None or bool(task.checklist):
            return False
        if await self._session.scalar(
            select(exists().where(TaskAttachment.task_id == task.id))
        ):
            return False
        if reason is TaskClosureReason.ORDER_CANCELLED:
            order_status = await self._session.scalar(
                select(StayOrder.status).where(StayOrder.id == task.order_id)
            )
            if not is_excluded_stay_status(order_status):
                return False
        if reason is TaskClosureReason.WINDOW_EXPIRED:
            deadline = task.expires_at
            if deadline is None and task.task_type is BusinessTaskType.MANUAL_CONTACT:
                reminder = await self._session.scalar(
                    select(LifecycleReminder).where(
                        literal("lifecycle-manual:")
                        + sa_cast(LifecycleReminder.id, String())
                        == task.dedupe_key
                    )
                )
                if reminder is not None:
                    deadline = manual_contact_expires_at(
                        reminder.reminder_type,
                        reminder.scheduled_at,
                    )
            if (
                deadline is None
                and task.task_type
                in {
                    BusinessTaskType.EARLY_CHECK_IN,
                    BusinessTaskType.LATE_CHECK_OUT,
                }
                and task.service_date is not None
            ):
                deadline = local_service_window_expires_at(task.service_date)
            if deadline is None:
                return False
            aware_deadline = (
                deadline.replace(tzinfo=UTC)
                if deadline.tzinfo is None
                else deadline
            )
            if now < aware_deadline:
                return False
        previous = task.status
        task.status = BusinessTaskStatus.EXPIRED
        task.closed_at = now
        task.closure_reason_code = reason
        task.closure_source = TaskClosureSource.SYSTEM
        task.closed_by_employee_id = None
        self._session.add(
            AuditLog(
                actor_employee_id=None,
                action="business_task_expired",
                target_type="business_task",
                target_id=str(task.id),
                details={
                    "from_status": previous.value,
                    "reason": reason.value,
                    "task_type": task.task_type.value,
                },
            )
        )
        await self._session.flush()
        return True

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
            checkout_observed_on = None
            if is_checked_out_stay_status(reservation.status):
                observed_on = self._local_date_provider()
                if reservation.check_in_date <= observed_on:
                    checkout_observed_on = observed_on
            order = StayOrder(
                hostex_reservation_code=reservation.reservation_code,
                stay_code=reservation.stay_code,
                customer_id=identity.customer_id,
                property_id=reservation.property_id,
                check_in_date=reservation.check_in_date,
                check_out_date=reservation.check_out_date,
                status=reservation.status,
                checkout_observed_on=checkout_observed_on,
            )
            self._session.add(order)
        else:
            order.stay_code = reservation.stay_code
            order.customer_id = identity.customer_id
            order.property_id = reservation.property_id
            order.check_in_date = reservation.check_in_date
            order.check_out_date = reservation.check_out_date
            order.status = reservation.status
            # 仅在首次观察到退房终态时落日；恢复有效后清空，以便再次退房重记。
            if is_checked_out_stay_status(reservation.status):
                observed_on = self._local_date_provider()
                if reservation.check_in_date > observed_on:
                    order.checkout_observed_on = None
                elif order.checkout_observed_on is None:
                    order.checkout_observed_on = observed_on
            elif not is_excluded_stay_status(reservation.status):
                order.checkout_observed_on = None
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
