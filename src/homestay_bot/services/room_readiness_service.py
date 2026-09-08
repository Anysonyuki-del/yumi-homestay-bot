from collections.abc import Callable
from datetime import date
from typing import Protocol

from homestay_bot.domain.enums import (
    BusinessTaskStatus,
    EmployeeRole,
    RoomOperationalStatus,
)
from homestay_bot.domain.models import (
    BusinessTask,
    Employee,
    RoomOperationalState,
)
from homestay_bot.services.stay_date_range import wuhan_today


class ReadinessRuleError(ValueError):
    """表示房间可入住所需证据或任务状态不完整。"""


class TaskEvidenceRepository(Protocol):
    """定义可入住校验所需的任务证据查询。"""

    async def require_for_update(self, task_id: int) -> BusinessTask:
        """锁定并返回任务。"""

    async def has_photo_attachment(self, task_id: int) -> bool:
        """判断任务是否至少关联一张有效照片。"""


class RoomStateRepository(Protocol):
    """定义房态更新入口。"""

    async def require_room_state_for_update(
        self,
        property_id: int,
    ) -> RoomOperationalState:
        """锁定并返回已有房态。"""

    async def set_room_status(
        self,
        property_id: int,
        status: RoomOperationalStatus,
        actor_employee_id: int,
    ) -> RoomOperationalState:
        """锁定并更新房态，同时记录安全审计。"""

    async def record_manual_override(
        self,
        *,
        property_id: int,
        actor_employee_id: int,
        status: RoomOperationalStatus,
    ) -> None:
        """记录一次未经证据的人工房态覆盖。"""


class CheckInOrderLookup(Protocol):
    """定义按房间与业务当日查找入住订单的最小边界。"""

    async def find_check_in_orders(
        self,
        property_id: int,
        local_date: date,
    ) -> list[int]:
        """返回该房间当日入住且未取消的订单编号。"""


class ManualFollowupPort(Protocol):
    """定义把不确定情形交给人工的最小边界。"""

    async def create_credential_review(
        self,
        *,
        property_id: int,
        local_date: date,
        order_ids: list[int],
    ) -> object:
        """按房间与日期幂等创建一条凭证人工复核任务。"""


class CredentialDeliveryEvaluator(Protocol):
    """定义房间可入住后触发凭证安全评估的接口。"""

    async def evaluate(
        self,
        *,
        order_id: int | None,
        expected_property_id: int,
        source_task_id: int,
    ) -> object:
        """评估安全条件并按需创建后台发送或人工异常。"""


class RoomReadinessService:
    """校验证据后允许执行员工标记可入住、管理员撤回。"""

    _required_checklist = frozenset({"clean", "supplies", "damage"})

    def __init__(
        self,
        tasks: TaskEvidenceRepository,
        rooms: RoomStateRepository,
        credential_delivery: CredentialDeliveryEvaluator | None = None,
        check_in_orders: CheckInOrderLookup | None = None,
        manual_followup: ManualFollowupPort | None = None,
        today: Callable[[], date] | None = None,
    ) -> None:
        """注入任务证据、房态仓储、凭证评估器与当日入住订单查找。"""
        self._tasks = tasks
        self._rooms = rooms
        self._credential_delivery = credential_delivery
        self._check_in_orders = check_in_orders
        self._manual_followup = manual_followup
        # 业务当日按武汉时区判定，与凭证安全门保持同一口径。
        self._today = today or wuhan_today

    async def mark_ready(
        self,
        task_id: int,
        actor: Employee,
    ) -> RoomOperationalState:
        """验证执行人、待检查状态、清单和照片后标记可入住。"""
        if not actor.is_active:
            raise PermissionError("员工已停用")
        task = await self._tasks.require_for_update(task_id)
        if task.assigned_employee_id != actor.id:
            raise PermissionError("只有该任务的执行员工可以标记可入住")
        if task.status is not BusinessTaskStatus.PENDING_INSPECTION:
            raise ReadinessRuleError("任务必须处于待检查状态")
        if task.property_id is None:
            raise ReadinessRuleError("任务尚未关联房间")
        if not self._required_checklist.issubset(task.checklist) or not all(
            task.checklist.get(key) is True
            for key in self._required_checklist
        ):
            raise ReadinessRuleError("保洁检查清单尚未全部完成")
        if not await self._tasks.has_photo_attachment(task.id):
            raise ReadinessRuleError("至少需要一张有效现场照片")
        state = await self._rooms.set_room_status(
            task.property_id,
            RoomOperationalStatus.READY,
            actor.id,
        )
        await self._hand_credentials_to_arriving_guest(task)
        return state

    async def _hand_credentials_to_arriving_guest(
        self,
        task: BusinessTask,
    ) -> None:
        """按「同房间 + 业务当日入住」重新选订单，再走全部现有安全门。

        周转保洁任务绑定的是**退房**订单，把它直接交给凭证评估，等于拿离店客人
        的订单去发当天入住客人的凭证：日期不符会被安全门拒绝，而当天真有新客人
        时也没人替他重新选订单。房间可入住与凭证发给谁是两件事。

        没有当日入住订单是正常情况，安静结束不报错；出现多个候选则不猜，交人工。
        """
        if self._credential_delivery is None or task.property_id is None:
            return
        local_date = self._today()
        order_ids = (
            await self._check_in_orders.find_check_in_orders(
                task.property_id,
                local_date,
            )
            if self._check_in_orders is not None
            else []
        )
        if not order_ids:
            return
        if len(order_ids) > 1:
            if self._manual_followup is not None:
                await self._manual_followup.create_credential_review(
                    property_id=task.property_id,
                    local_date=local_date,
                    order_ids=sorted(order_ids),
                )
            return
        await self._credential_delivery.evaluate(
            order_id=order_ids[0],
            expected_property_id=task.property_id,
            source_task_id=task.id,
        )

    async def set_status_by_admin(
        self,
        property_id: int,
        administrator: Employee,
        status: RoomOperationalStatus,
    ) -> RoomOperationalState:
        """允许管理员直接设定房态，不要求清单与现场照片证据。

        仓储层本就支持全部六个状态并自带审计，但此前只有 mark_ready 和
        revoke_ready 两个受限入口，清洁中、在住、维修中永远无法到达，
        房间坏了也标不出来。

        这里额外写一条人工覆盖审计：房态 READY 是向客人发放门锁密码的前置
        条件之一，人工直改绕过了证据要求，事后必须能区分「走了证据流程」
        和「管理员直接设定」，否则无从追查房间当时凭什么算可入住。
        仓储层「已入住/维修中不得直接标记为可入住」的领域守卫继续生效。
        """
        if (
            not administrator.is_active
            or administrator.role is not EmployeeRole.ADMIN
        ):
            raise PermissionError("只有管理员可以直接设定房态")
        state = await self._rooms.set_room_status(
            property_id,
            status,
            administrator.id,
        )
        await self._rooms.record_manual_override(
            property_id=property_id,
            actor_employee_id=administrator.id,
            status=status,
        )
        return state

    async def revoke_ready(
        self,
        property_id: int,
        administrator: Employee,
    ) -> RoomOperationalState:
        """只允许启用管理员把房间撤回待检查。"""
        if (
            not administrator.is_active
            or administrator.role is not EmployeeRole.ADMIN
        ):
            raise PermissionError("只有管理员可以撤回可入住状态")
        current = await self._rooms.require_room_state_for_update(property_id)
        if current.status is not RoomOperationalStatus.READY:
            raise ReadinessRuleError("只有可入住状态可以撤回")
        return await self._rooms.set_room_status(
            property_id,
            RoomOperationalStatus.PENDING_INSPECTION,
            administrator.id,
        )
