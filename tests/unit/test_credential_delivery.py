from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from homestay_bot.domain.enums import (
    CredentialDeliveryStatus,
    RoomOperationalStatus,
)
from homestay_bot.services.credential_delivery import (
    CredentialDeliveryContext,
    CredentialDeliveryService,
    CredentialPartSendContext,
    CredentialPartSender,
)
from homestay_bot.services.private_file_storage import PrivateFileStorage
from homestay_bot.services.sensitive_data import SensitiveDataCipher


def valid_context() -> CredentialDeliveryContext:
    """构造满足入住凭证发送安全门的上下文。"""
    return CredentialDeliveryContext(
        order_id=7,
        order_customer_id=12,
        order_property_id=101,
        order_status="confirmed",
        check_in_date=date(2026, 8, 2),
        check_out_date=date(2026, 8, 3),
        room_status=RoomOperationalStatus.READY,
        credential_id=31,
        credential_property_id=101,
        credential_version=3,
        credential_is_active=True,
        conversation_customer_id=12,
        open_kfid="wk-1",
        external_userid="wm-1",
        wecom_identity_verified=True,
        last_guest_message_at=datetime(2026, 8, 2, 8, tzinfo=UTC),
    )


class DeliveryRepositoryStub:
    """返回固定安全上下文并记录异常与投递状态。"""

    def __init__(self, context=None) -> None:
        """初始化上下文和三项待发送部件。"""
        self.context = context if context is not None else valid_context()
        self.exceptions: list[dict[str, object]] = []
        self.delivery = SimpleNamespace(
            id=41,
            order_id=7,
            credential_id=31,
            status=CredentialDeliveryStatus.PENDING,
        )
        self.parts = [
            SimpleNamespace(
                id=51,
                part_type="guide",
                status=CredentialDeliveryStatus.PENDING,
            ),
            SimpleNamespace(
                id=52,
                part_type="password",
                status=CredentialDeliveryStatus.PENDING,
            ),
            SimpleNamespace(
                id=53,
                part_type="qr",
                status=CredentialDeliveryStatus.PENDING,
            ),
        ]
        self.ensure_calls = 0

    async def load_context_for_update(self, order_id):
        """返回指定订单的凭证发送上下文。"""
        assert order_id == 7
        return self.context

    async def ensure_delivery_parts(self, context):
        """返回幂等投递及三个部件。"""
        self.ensure_calls += 1
        return self.delivery, self.parts

    async def record_exception(self, **fields):
        """记录缺失安全条件形成的管理员异常任务。"""
        self.exceptions.append(fields)


class JobQueueStub:
    """记录凭证部件后台任务和去重键。"""

    def __init__(self) -> None:
        """初始化入队记录。"""
        self.items: list[dict[str, object]] = []
        self._dedupe_keys: set[str] = set()

    async def enqueue(self, job_type, payload, *, dedupe_key):
        """记录任务类型、载荷和去重键。"""
        if dedupe_key in self._dedupe_keys:
            return
        self._dedupe_keys.add(dedupe_key)
        self.items.append(
            {
                "job_type": job_type,
                "payload": payload,
                "dedupe_key": dedupe_key,
            }
        )


def build_service(context=None):
    """构造使用固定武汉日期与时间的服务。"""
    repository = DeliveryRepositoryStub(context)
    jobs = JobQueueStub()
    service = CredentialDeliveryService(
        repository,
        jobs,
        today=lambda: date(2026, 8, 2),
        now=lambda: datetime(2026, 8, 2, 9, tzinfo=UTC),
    )
    return service, repository, jobs


@pytest.mark.asyncio
async def test_ready_room_enqueues_each_credential_part_once() -> None:
    """全部安全条件满足时按版本创建三个独立幂等发送任务。"""
    service, repository, jobs = build_service()

    result = await service.evaluate(
        order_id=7,
        expected_property_id=101,
        source_task_id=9,
    )
    repeated = await service.evaluate(
        order_id=7,
        expected_property_id=101,
        source_task_id=9,
    )

    assert result is repository.delivery
    assert repeated is repository.delivery
    assert [item["dedupe_key"] for item in jobs.items] == [
        "credential:7:guide:v3",
        "credential:7:password:v3",
        "credential:7:qr:v3",
    ]
    assert all(
        set(item["payload"]) == {"part_id"}
        for item in jobs.items
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"order_customer_id": None}, "missing_customer"),
        ({"order_status": "cancelled"}, "invalid_order_status"),
        ({"check_in_date": date(2026, 8, 3)}, "not_checkin_day"),
        ({"order_property_id": 102}, "task_room_mismatch"),
        (
            {"room_status": RoomOperationalStatus.MAINTENANCE},
            "room_not_ready",
        ),
        ({"credential_property_id": 102}, "credential_room_mismatch"),
        ({"conversation_customer_id": 99}, "customer_mismatch"),
        ({"wecom_identity_verified": False}, "wecom_identity_mismatch"),
    ],
)
async def test_missing_safety_condition_never_enqueues(
    change,
    reason,
) -> None:
    """任一订单、客户、日期、房间或凭证条件不匹配都不得发送。"""
    context = valid_context()
    for field, value in change.items():
        setattr(context, field, value)
    service, repository, jobs = build_service(context)

    result = await service.evaluate(
        order_id=7,
        expected_property_id=101,
        source_task_id=9,
    )

    assert result is None
    assert jobs.items == []
    assert repository.ensure_calls == 0
    assert repository.exceptions[0]["reason"] == reason


@pytest.mark.asyncio
async def test_expired_customer_service_window_creates_manual_exception() -> None:
    """最近客人消息超过 48 小时后停止发送并建立人工联系异常。"""
    context = valid_context()
    context.last_guest_message_at = datetime(2026, 7, 31, 8, tzinfo=UTC)
    service, repository, jobs = build_service(context)

    result = await service.evaluate(
        order_id=7,
        expected_property_id=101,
        source_task_id=9,
    )

    assert result is None
    assert jobs.items == []
    assert repository.exceptions[0]["reason"] == "wecom_window_expired"


@pytest.mark.asyncio
async def test_sent_or_uncertain_parts_are_never_enqueued_again() -> None:
    """成功或结果不明确的部件不能因再次评估而盲目重放。"""
    service, repository, jobs = build_service()
    repository.parts[0].status = CredentialDeliveryStatus.SENT
    repository.parts[2].status = CredentialDeliveryStatus.NEEDS_REVIEW

    await service.evaluate(
        order_id=7,
        expected_property_id=101,
        source_task_id=9,
    )

    assert [item["dedupe_key"] for item in jobs.items] == [
        "credential:7:password:v3",
    ]


class PartRepositoryStub:
    """返回固定部件并记录明确成功或待复核结果。"""

    def __init__(self, item) -> None:
        """保存待发送部件。"""
        self.item = item
        self.sent: list[tuple[int, str]] = []
        self.review: list[tuple[int, str]] = []

    async def load_part_for_update(self, part_id):
        """返回仍待发送的固定部件。"""
        assert part_id == self.item.part_id
        return self.item

    async def mark_part_sent(self, part_id, external_message_id):
        """记录明确成功并同步测试状态。"""
        self.sent.append((part_id, external_message_id))
        self.item.part_status = CredentialDeliveryStatus.SENT

    async def mark_part_needs_review(self, part_id, error_code):
        """记录结果不明确并同步测试状态。"""
        self.review.append((part_id, error_code))
        self.item.part_status = CredentialDeliveryStatus.NEEDS_REVIEW


class WeComSendStub:
    """记录凭证文本、素材和图片发送。"""

    def __init__(self, *, image_error: Exception | None = None) -> None:
        """配置可选图片发送异常。"""
        self.image_error = image_error
        self.texts: list[str] = []
        self.uploads = 0
        self.images = 0

    async def send_text(self, open_kfid, external_userid, content):
        """记录文本并返回明确消息编号。"""
        self.texts.append(content)
        return "TEXT-MSG-1"

    async def upload_temporary_image(self, content, *, content_type):
        """记录二维码上传并返回素材编号。"""
        self.uploads += 1
        return "MEDIA-1"

    async def send_image(self, open_kfid, external_userid, media_id):
        """发送图片或模拟结果不明确。"""
        self.images += 1
        if self.image_error is not None:
            raise self.image_error
        return "IMAGE-MSG-1"


def part_context(cipher, part_type: str) -> CredentialPartSendContext:
    """构造含用途隔离密文的待发送部件。"""
    return CredentialPartSendContext(
        part_id=51,
        part_type=part_type,
        part_status=CredentialDeliveryStatus.PENDING,
        delivery_id=41,
        credential_version=3,
        context=valid_context(),
        password_ciphertext=cipher.encrypt(
            "839201",
            purpose="room_password",
        ),
        guide_ciphertext=cipher.encrypt(
            "入住指南正文",
            purpose="checkin_guide",
        ),
        qr_file_id="a" * 32 + ".png",
    )


@pytest.mark.asyncio
async def test_text_part_marks_sent_only_after_explicit_message_id(
    tmp_path,
) -> None:
    """文本只有获得企业微信消息编号后才标记为成功。"""
    cipher = SensitiveDataCipher(Fernet.generate_key().decode("ascii"))
    item = part_context(cipher, "guide")
    repository = PartRepositoryStub(item)
    wecom = WeComSendStub()
    sender = CredentialPartSender(
        repository,
        wecom,
        cipher,
        PrivateFileStorage(tmp_path),
        today=lambda: date(2026, 8, 2),
        now=lambda: datetime(2026, 8, 2, 9, tzinfo=UTC),
    )

    await sender.handle({"part_id": 51})

    assert wecom.texts == ["入住指南正文"]
    assert repository.sent == [(51, "TEXT-MSG-1")]
    assert repository.review == []


@pytest.mark.asyncio
async def test_uncertain_image_result_is_not_replayed(tmp_path) -> None:
    """图片上传后发送结果不明确时转人工，重复处理也不得重放。"""
    cipher = SensitiveDataCipher(Fernet.generate_key().decode("ascii"))
    item = part_context(cipher, "qr")
    qr_path = tmp_path / item.qr_file_id
    qr_path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00"
        b"\x90wS\xde"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    repository = PartRepositoryStub(item)
    wecom = WeComSendStub(image_error=TimeoutError("unknown result"))
    sender = CredentialPartSender(
        repository,
        wecom,
        cipher,
        PrivateFileStorage(tmp_path),
        today=lambda: date(2026, 8, 2),
        now=lambda: datetime(2026, 8, 2, 9, tzinfo=UTC),
    )

    await sender.handle({"part_id": 51})
    await sender.handle({"part_id": 51})

    assert wecom.uploads == 1
    assert wecom.images == 1
    assert repository.sent == []
    assert repository.review == [(51, "TimeoutError")]
