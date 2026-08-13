from datetime import UTC, datetime, timedelta

import pytest

import homestay_bot.services.message_service as message_service_module
from homestay_bot.domain.enums import MessageOrigin
from homestay_bot.domain.models import Message
from homestay_bot.services.message_service import MessageService


class MessageRepositoryStub:
    """记录消息服务写入的数据。"""

    def __init__(self) -> None:
        """初始化消息记录。"""
        self.messages: list[Message] = []

    async def exists(self, external_message_id: str) -> bool:
        """本测试不模拟重复消息。"""
        return False

    async def add(self, message: Message) -> bool:
        """保存待断言的消息对象。"""
        self.messages.append(message)
        return True

    async def list_recent(
        self,
        conversation_id: int,
        limit: int,
        through_external_message_id: str | None = None,
    ) -> list[Message]:
        """返回当前测试记录。"""
        messages = self.messages
        if through_external_message_id is not None:
            boundary = next(
                index + 1
                for index, message in enumerate(messages)
                if message.external_message_id == through_external_message_id
            )
            messages = messages[:boundary]
        return messages[-limit:]

    async def has_newer_guest_message(
        self,
        conversation_id: int,
        external_message_id: str,
    ) -> bool:
        """本组测试不需要模拟更新消息。"""
        return False


class RecordingDateTime:
    """记录业务代码请求当前时间时使用的时区。"""

    requested_timezones: list[object] = []

    @classmethod
    def now(cls, timezone: object = None) -> datetime:
        """返回固定时间，并记录调用者传入的时区。"""
        cls.requested_timezones.append(timezone)
        return datetime(2026, 7, 29, 5, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_bot_message_uses_utc_timestamp_by_default(monkeypatch) -> None:
    """机器人消息默认时间必须与企业微信客人消息统一使用 UTC。"""
    RecordingDateTime.requested_timezones.clear()
    monkeypatch.setattr(message_service_module, "datetime", RecordingDateTime)
    repository = MessageRepositoryStub()
    service = MessageService(repository)

    await service.record_bot(1, "bot-1", "您好")

    assert RecordingDateTime.requested_timezones == [UTC]
    assert repository.messages[0].origin is MessageOrigin.BOT


def stored_message(
    external_message_id: str,
    content: str,
    *,
    received_at: datetime,
    origin: MessageOrigin = MessageOrigin.GUEST,
) -> Message:
    """构造带系统入库时间的文本消息。"""
    return Message(
        conversation_id=1,
        external_message_id=external_message_id,
        origin=origin,
        message_type="text",
        content=content,
        message_metadata={},
        sent_at=received_at,
        created_at=received_at,
    )


@pytest.mark.asyncio
async def test_guest_batch_merges_only_contiguous_messages_in_order() -> None:
    """合并批次按入库顺序输出，并在超过三秒的间隔处停止。"""
    repository = MessageRepositoryStub()
    now = datetime(2026, 8, 14, 2, 0, tzinfo=UTC)
    repository.messages.extend(
        [
            stored_message("old", "上一轮问题", received_at=now),
            stored_message(
                "msg-1",
                "房间里的灯",
                received_at=now + timedelta(seconds=5),
            ),
            stored_message(
                "msg-2",
                "一直闪",
                received_at=now + timedelta(seconds=7),
            ),
            stored_message(
                "msg-3",
                "麻烦维修",
                received_at=now + timedelta(seconds=10),
            ),
        ]
    )

    batch = await MessageService(repository).build_guest_batch(
        1,
        "msg-3",
        quiet_window_seconds=3,
    )

    assert batch.content == "房间里的灯\n一直闪\n麻烦维修"
    assert batch.message_count == 3
    assert [message.content for message in repository.messages] == [
        "上一轮问题",
        "房间里的灯",
        "一直闪",
        "麻烦维修",
    ]


@pytest.mark.asyncio
async def test_guest_batch_is_bounded_to_ten_messages_and_2000_characters() -> None:
    """恶意高频或超长片段不能让合并任务无限增长。"""
    repository = MessageRepositoryStub()
    now = datetime(2026, 8, 14, 2, 0, tzinfo=UTC)
    repository.messages.extend(
        stored_message(
            f"msg-{index}",
            f"<{index}>" + "客" * 300,
            received_at=now + timedelta(milliseconds=index * 100),
        )
        for index in range(12)
    )

    batch = await MessageService(repository).build_guest_batch(1, "msg-11")

    assert batch.message_count == 10
    assert len(batch.content) == 2000
    assert "<0>" not in batch.content
    assert "<1>" not in batch.content
    assert batch.content.endswith("客")


@pytest.mark.asyncio
async def test_model_context_collapses_current_guest_batch_into_one_question() -> None:
    """本轮连续片段折叠成一条 user 消息，同时保留上一轮问答。"""
    repository = MessageRepositoryStub()
    now = datetime(2026, 8, 14, 2, 0, tzinfo=UTC)
    repository.messages.extend(
        [
            stored_message("old-question", "上一轮问题", received_at=now),
            stored_message(
                "old-answer",
                "上一轮回答",
                received_at=now + timedelta(seconds=1),
                origin=MessageOrigin.BOT,
            ),
            stored_message(
                "msg-1",
                "房间里的灯",
                received_at=now + timedelta(seconds=5),
            ),
            stored_message(
                "msg-2",
                "一直闪",
                received_at=now + timedelta(seconds=6),
            ),
            stored_message(
                "msg-3",
                "麻烦维修",
                received_at=now + timedelta(seconds=7),
            ),
        ]
    )

    context = await MessageService(repository).build_context(
        1,
        limit=3,
        through_external_message_id="msg-3",
        merged_guest_content="房间里的灯\n一直闪\n麻烦维修",
        merged_guest_count=3,
    )

    assert context == [
        {"role": "user", "content": "上一轮问题"},
        {"role": "assistant", "content": "上一轮回答"},
        {"role": "user", "content": "房间里的灯\n一直闪\n麻烦维修"},
    ]
