from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WeComModel(BaseModel):
    """允许企业微信新增字段，同时校验当前消费字段。"""

    model_config = ConfigDict(extra="ignore")


class WeComMessage(WeComModel):
    """表示微信客服读取接口返回的一条消息或事件。"""

    msgid: str | None = None
    # 部分事件只在 event 对象内提供客服账号，顶层字段并不固定存在。
    open_kfid: str | None = None
    external_userid: str | None = None
    send_time: int | None = None
    origin: int | None = None
    msgtype: str | None = None
    text: dict[str, Any] | None = None
    image: dict[str, Any] | None = None
    voice: dict[str, Any] | None = None
    video: dict[str, Any] | None = None
    file: dict[str, Any] | None = None
    location: dict[str, Any] | None = None
    event: dict[str, Any] | None = None


class SyncMessagePage(WeComModel):
    """表示一次客服消息同步结果。"""

    next_cursor: str = ""
    has_more: int = 0
    msg_list: list[WeComMessage] = Field(default_factory=list)
