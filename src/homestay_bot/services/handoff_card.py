"""转人工按钮卡片与给客人的会话状态提示（纯函数，不依赖数据库和接口）。

会话服务登记卡片、worker 发送卡片、点击按钮后更新卡片共用这里的组装规则。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

# 给客人的三句状态提示（用户 2026-09-29 否掉了「管家暂时离开」的说法）。
ACCEPTED_TEXT = "【管家接管中】管家已接入，接下来由管家直接为您服务。"
ENDED_TEXT = (
    "【AI 助手】本次管家服务已结束，接下来由 YuMi 智能助手为您服务。"
    "还需要管家时，回复「转人工」即可。"
)
TIMEOUT_TEXT = (
    "【AI 助手】管家暂时没能及时回复，您的问题已转给管家。"
    "这段时间由 YuMi 智能助手先为您解答，需要管家请回复「转人工」。"
)

# 卡片按钮回调 key 的前缀；key 里只带内部会话编号。
ACCEPT_KEY = "accept"
RELEASE_KEY = "release"

CardStage = Literal["pending", "accepted", "closed"]


@dataclass(frozen=True)
class HandoffCard:
    """一张转人工卡片需要的全部可见内容；员工只看这张卡就能决定要不要接入。"""

    conversation_id: int
    reason: str
    guest: str
    link: str
    link_label: str = "看对话记录"
    room: str = ""
    handover: Sequence[str] = ()
    handover_title: str = "接手要点"
    preferences: Sequence[str] = ()
    original: str = ""
    replied: str | None = None
    footer: str = ""


def _clip(text: str, limit: int) -> str:
    """按字符截断，卡片字段的平台建议上限都按字数给出。"""
    # 行内多余空白压成一个，保留有意的换行（接手要点逐行）。
    text = "\n".join(" ".join(line.split()) for line in str(text).splitlines()).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_handoff_card(
    card: HandoffCard,
    *,
    stage: CardStage,
    task_id: str | None = None,
    note: str = "",
    replace_text: str = "",
) -> dict[str, Any]:
    """组装按钮交互型模板卡片。

    - pending：按钮「接入人工」+ 对话链接；副标题是客人刚说、机器人已回与时限提示。
    - accepted：按钮换成「交还 AI 助手」，副标题告诉管家去哪里回复、怎么结束。
    - closed：按钮置灰显示 `replace_text`（已交还、已有管家接入、接入失败等）。
    字段长度按平台建议值截断，超出只影响显示不影响送达。
    """
    lines: list[str] = []
    if stage == "pending":
        if card.original:
            lines.append(f"客人刚说：{_clip(card.original, 60)}")
        if card.replied is not None:
            lines.append(f"机器人已回：{_clip(card.replied or '尚未回复客人', 60)}")
        if card.footer:
            lines.append(card.footer)
    elif stage == "accepted":
        lines.append(
            "已接入。请在企业微信「微信客服」里直接回复客人；处理完点「交还 AI 助手」，"
            "或在会话里点「结束聊天」。"
        )
    if note:
        lines.append(note)
    template: dict[str, Any] = {
        "card_type": "button_interaction",
        "main_title": {"title": _clip(card.reason, 36), "desc": _clip(card.guest, 44)},
        "card_action": {"type": 1, "url": card.link},
    }
    if card.handover:
        template["quote_area"] = {
            "type": 0,
            "title": card.handover_title,
            "quote_text": _clip("\n".join(_clip(item, 80) for item in card.handover[:3]), 240),
        }
    if lines:
        template["sub_title_text"] = _clip("\n".join(lines), 160)
    horizontal = []
    if card.room:
        horizontal.append({"keyname": "入住", "value": _clip(card.room, 30)})
    if card.preferences:
        horizontal.append({"keyname": "偏好", "value": _clip("；".join(card.preferences[:3]), 30)})
    if horizontal:
        template["horizontal_content_list"] = horizontal
    action_button: dict[str, Any] = (
        {"text": "接入人工", "style": 1, "key": f"{ACCEPT_KEY}:{card.conversation_id}"}
        if stage == "pending"
        else {"text": "交还 AI 助手", "style": 1, "key": f"{RELEASE_KEY}:{card.conversation_id}"}
    )
    template["button_list"] = [
        action_button,
        {"text": card.link_label, "style": 2, "type": 1, "url": card.link},
    ]
    if stage == "closed":
        template["replace_text"] = replace_text or "已处理"
    if task_id:
        template["task_id"] = task_id
    return template
