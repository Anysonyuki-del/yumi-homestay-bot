from datetime import UTC, datetime

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
        self, conversation_id: int, limit: int
    ) -> list[Message]:
        """返回当前测试记录。"""
        return self.messages[-limit:]


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
