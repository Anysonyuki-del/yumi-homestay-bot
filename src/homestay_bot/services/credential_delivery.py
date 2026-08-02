from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from homestay_bot.domain.enums import (
    CredentialDeliveryStatus,
    RoomOperationalStatus,
)
from homestay_bot.domain.models import CredentialDelivery
from homestay_bot.services.private_file_storage import PrivateFileStorage
from homestay_bot.services.sensitive_data import SensitiveDataCipher


@dataclass
class CredentialDeliveryContext:
    """汇总一次发送决策需要的最小订单、房态、凭证和会话事实。"""

    order_id: int
    order_customer_id: int | None
    order_property_id: int
    order_status: str
    check_in_date: date
    check_out_date: date
    room_status: RoomOperationalStatus | None
    credential_id: int | None
    credential_property_id: int | None
    credential_version: int | None
    credential_is_active: bool
    conversation_customer_id: int | None
    open_kfid: str | None
    external_userid: str | None
    wecom_identity_verified: bool
    last_guest_message_at: datetime | None


@dataclass
class CredentialPartSendContext:
    """保存 worker 发送单个凭证部件时需要重新核对的事实。"""

    part_id: int
    part_type: str
    part_status: CredentialDeliveryStatus
    delivery_id: int
    credential_version: int
    context: CredentialDeliveryContext
    password_ciphertext: bytes
    guide_ciphertext: bytes
    qr_file_id: str


class CredentialDeliveryRepository(Protocol):
    """定义凭证安全门和幂等投递所需的持久化操作。"""

    async def load_context_for_update(
        self,
        order_id: int,
    ) -> CredentialDeliveryContext | None:
        """锁定订单并读取最新发送上下文。"""

    async def ensure_delivery_parts(
        self,
        context: CredentialDeliveryContext,
    ) -> tuple[CredentialDelivery, list[Any]]:
        """幂等创建投递及指南、密码、二维码部件。"""

    async def record_exception(
        self,
        *,
        order_id: int | None,
        property_id: int,
        source_task_id: int,
        reason: str,
    ) -> None:
        """幂等创建不含凭证明文的管理员异常任务。"""


class CredentialJobQueue(Protocol):
    """定义凭证发送后台任务入队接口。"""

    async def enqueue(
        self,
        job_type: str,
        payload: dict[str, Any],
        *,
        dedupe_key: str,
    ) -> Any:
        """按稳定去重键登记一个部件发送任务。"""


class CredentialPartRepository(Protocol):
    """定义 worker 读取和记录单部件发送结果的操作。"""

    async def load_part_for_update(
        self,
        part_id: int,
    ) -> CredentialPartSendContext | None:
        """锁定并返回部件及最新安全上下文。"""

    async def mark_part_sent(
        self,
        part_id: int,
        external_message_id: str,
    ) -> None:
        """记录明确成功的企业微信消息编号。"""

    async def mark_part_needs_review(
        self,
        part_id: int,
        error_code: str,
    ) -> None:
        """记录不明确或不安全结果并建立人工任务。"""


class CredentialWeComApi(Protocol):
    """定义凭证发送需要的企业微信写接口。"""

    async def send_text(
        self,
        open_kfid: str,
        external_userid: str,
        content: str,
    ) -> str:
        """发送文本并返回消息编号。"""

    async def upload_temporary_image(
        self,
        content: bytes,
        *,
        content_type: str,
    ) -> str:
        """上传临时图片并返回素材编号。"""

    async def send_image(
        self,
        open_kfid: str,
        external_userid: str,
        media_id: str,
    ) -> str:
        """发送图片并返回消息编号。"""


class CredentialSafetyRules:
    """集中执行首次入队和实际发送前完全相同的安全条件。"""

    _invalid_order_statuses = frozenset(
        {"cancelled", "canceled", "declined", "expired", "deleted"}
    )

    def __init__(
        self,
        *,
        today: Callable[[], date] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        """保存可测试的武汉业务日期和 UTC 当前时间。"""
        self._today = today or date.today
        self._now = now or (lambda: datetime.now(UTC))

    def invalid_reason(
        self,
        context: CredentialDeliveryContext,
        expected_property_id: int,
    ) -> str | None:
        """按固定顺序返回首个不满足的安全条件代码。"""
        if context.order_customer_id is None:
            return "missing_customer"
        if context.order_status.lower() in self._invalid_order_statuses:
            return "invalid_order_status"
        today = self._today()
        if context.check_in_date != today or context.check_out_date <= today:
            return "not_checkin_day"
        if context.order_property_id != expected_property_id:
            return "task_room_mismatch"
        if context.room_status is not RoomOperationalStatus.READY:
            return "room_not_ready"
        if (
            context.credential_id is None
            or context.credential_version is None
            or not context.credential_is_active
        ):
            return "missing_credential"
        if context.credential_property_id != context.order_property_id:
            return "credential_room_mismatch"
        if (
            context.conversation_customer_id != context.order_customer_id
            or not context.open_kfid
            or not context.external_userid
        ):
            return "customer_mismatch"
        if not context.wecom_identity_verified:
            return "wecom_identity_mismatch"
        last_guest_message_at = context.last_guest_message_at
        if last_guest_message_at is None:
            return "missing_guest_window"
        if last_guest_message_at.tzinfo is None:
            last_guest_message_at = last_guest_message_at.replace(tzinfo=UTC)
        if self._now() - last_guest_message_at > timedelta(hours=48):
            return "wecom_window_expired"
        return None


class CredentialDeliveryService:
    """在所有安全条件满足后创建逐部件幂等发送任务。"""

    _part_order = {"guide": 0, "password": 1, "qr": 2}

    def __init__(
        self,
        repository: CredentialDeliveryRepository,
        jobs: CredentialJobQueue,
        *,
        today: Callable[[], date] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        """注入持久化、任务队列及可测试时钟。"""
        self._repository = repository
        self._jobs = jobs
        self._rules = CredentialSafetyRules(today=today, now=now)

    async def evaluate(
        self,
        *,
        order_id: int | None,
        expected_property_id: int,
        source_task_id: int,
    ) -> CredentialDelivery | None:
        """复核安全门；不满足时只建人工异常，不发送任何凭证。"""
        if order_id is None:
            await self._record_exception(
                order_id=None,
                property_id=expected_property_id,
                source_task_id=source_task_id,
                reason="missing_order",
            )
            return None
        context = await self._repository.load_context_for_update(order_id)
        if context is None:
            await self._record_exception(
                order_id=order_id,
                property_id=expected_property_id,
                source_task_id=source_task_id,
                reason="order_not_found",
            )
            return None
        reason = self._rules.invalid_reason(context, expected_property_id)
        if reason is not None:
            await self._record_exception(
                order_id=order_id,
                property_id=expected_property_id,
                source_task_id=source_task_id,
                reason=reason,
            )
            return None
        delivery, parts = await self._repository.ensure_delivery_parts(context)
        version = context.credential_version
        if version is None:
            raise RuntimeError("凭证版本在安全校验后缺失")
        for part in sorted(
            parts,
            key=lambda item: self._part_order.get(item.part_type, 99),
        ):
            if part.status is not CredentialDeliveryStatus.PENDING:
                continue
            await self._jobs.enqueue(
                "credential_send_part",
                {"part_id": part.id},
                dedupe_key=(
                    f"credential:{context.order_id}:"
                    f"{part.part_type}:v{version}"
                ),
            )
        return delivery

    async def _record_exception(
        self,
        *,
        order_id: int | None,
        property_id: int,
        source_task_id: int,
        reason: str,
    ) -> None:
        """把缺失条件交给幂等管理员异常任务，不抛错回滚房态。"""
        await self._repository.record_exception(
            order_id=order_id,
            property_id=property_id,
            source_task_id=source_task_id,
            reason=reason,
        )


class CredentialPartSender:
    """发送单个凭证部件，任何不明确结果都转人工且绝不自动重放。"""

    def __init__(
        self,
        repository: CredentialPartRepository,
        wecom: CredentialWeComApi,
        cipher: SensitiveDataCipher,
        storage: PrivateFileStorage,
        *,
        today: Callable[[], date] | None = None,
        now: Callable[[], datetime] | None = None,
        before_external: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """注入发送状态仓储、企业微信、事务边界、加密服务和时钟。"""
        self._repository = repository
        self._wecom = wecom
        self._cipher = cipher
        self._storage = storage
        self._rules = CredentialSafetyRules(today=today, now=now)
        self._before_external = before_external

    async def handle(self, payload: dict[str, Any]) -> None:
        """重新验证安全条件并发送一个仍处于待发送状态的部件。"""
        part_id = int(payload["part_id"])
        item = await self._repository.load_part_for_update(part_id)
        if (
            item is None
            or item.part_status is not CredentialDeliveryStatus.PENDING
        ):
            return
        reason = self._rules.invalid_reason(
            item.context,
            item.context.order_property_id,
        )
        if reason is not None:
            await self._repository.mark_part_needs_review(part_id, reason)
            return
        if self._before_external is not None:
            # 凭证部件已完成安全快照，网络发送前提交以释放数据库行锁。
            await self._before_external()
        try:
            external_message_id = await self._send(item)
        except Exception as error:
            await self._repository.mark_part_needs_review(
                part_id,
                type(error).__name__,
            )
            return
        await self._repository.mark_part_sent(
            part_id,
            external_message_id,
        )

    async def _send(self, item: CredentialPartSendContext) -> str:
        """按部件类型解密最少正文或读取二维码并调用企业微信。"""
        open_kfid = item.context.open_kfid
        external_userid = item.context.external_userid
        if not open_kfid or not external_userid:
            raise RuntimeError("凭证发送目标缺失")
        if item.part_type == "guide":
            content = self._cipher.decrypt(
                item.guide_ciphertext,
                purpose="checkin_guide",
            )
            return await self._wecom.send_text(
                open_kfid,
                external_userid,
                content,
            )
        if item.part_type == "password":
            password = self._cipher.decrypt(
                item.password_ciphertext,
                purpose="room_password",
            )
            return await self._wecom.send_text(
                open_kfid,
                external_userid,
                f"门锁密码：{password}",
            )
        if item.part_type == "qr":
            stored = self._storage.open_for_read(item.qr_file_id)
            media_id = await self._wecom.upload_temporary_image(
                stored.path.read_bytes(),
                content_type=stored.content_type,
            )
            return await self._wecom.send_image(
                open_kfid,
                external_userid,
                media_id,
            )
        raise ValueError("未知凭证部件类型")
