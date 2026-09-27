"""共享回复事实与动作结果；来源和动作状态只由本地执行结果构造。"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

from homestay_bot.domain.enums import Language


class ReplyEvidence(BaseModel):
    """一条实际读取的来源及其适用房间、日期和不可删除条件。"""

    source_kind: Literal[
        "knowledge", "availability", "reference_price", "property", "public", "task"
    ]
    source_id: str
    property_id: int | None = None
    target_date: date | None = None
    target_end_date: date | None = None
    fetched_at: datetime
    conditions: tuple[str, ...] = ()


class ReplyPart(BaseModel):
    """本轮一个可独立回答的问题；正文保留已验证事实单元。"""

    question: str
    status: Literal["grounded", "missing", "clarification", "query_failed", "out_of_scope"]
    text: str
    evidence: tuple[ReplyEvidence, ...] = ()


class GuestActionResult(BaseModel):
    """任务持久化和通知入队结果，禁止从模型 JSON 接受成功状态。"""

    task_id: int | None = None
    registered: bool = False
    notification_queued: bool = False


def compose_reply_parts(parts: list[ReplyPart] | tuple[ReplyPart, ...]) -> str:
    """只拼接本地校验后的分项，不再改写事实。"""
    return "\n\n".join(dict.fromkeys(part.text.strip() for part in parts if part.text.strip()))


def prepare_planned_reply(
    parts: list[ReplyPart] | tuple[ReplyPart, ...],
    *,
    fallback: str,
    language: Language,
    question: str = "",
    high_risk: bool = False,
) -> str:
    """已取证事实只排版；其他正文仍经承诺过滤，供会话、调试和评估共用。"""
    from homestay_bot.services.guest_reply_policy import layout_guest_reply, prepare_guest_reply

    if not parts:
        return prepare_guest_reply(
            fallback, language=language, requires_human=high_risk,
            high_risk=high_risk, question=question,
        )
    rendered = [
        layout_guest_reply(part.text, language)
        # 搜索证明外部事实，不证明管家已行动；公开生成文本仍过滤执行承诺。
        if part.status == "grounded" and part.evidence
        and all(e.source_kind != "public" for e in part.evidence)
        else prepare_guest_reply(part.text, language=language, requires_human=False)
        for part in parts
    ]
    if high_risk:
        rendered.append(prepare_guest_reply(
            "", language=language, requires_human=True, high_risk=True,
        ))
    return "\n\n".join(dict.fromkeys(rendered))
