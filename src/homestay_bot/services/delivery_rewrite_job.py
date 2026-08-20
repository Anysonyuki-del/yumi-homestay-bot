"""编排企业微信安全拦截后的单次事实等价改写与二次投递。"""

import re
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from homestay_bot.domain.enums import Language
from homestay_bot.domain.models import Message
from homestay_bot.integrations.deepseek_delivery_rewriter import (
    DeliveryRewriteUnavailableError,
)
from homestay_bot.integrations.tourism import split_tourism_reply
from homestay_bot.repositories.conversations import DeliveryRewriteContext
from homestay_bot.services.guest_reply_policy import (
    contains_sensitive_guest_text,
    redact_sensitive_guest_text,
    remove_ungrounded_property_claims,
    sanitize_guest_reply,
)

_SOURCE_OR_URL_PATTERN = re.compile(
    r"https?://\S+|www\.\S+|查询日期\s*[:：].*|参考来源\s*[:：].*|"
    r"Query date\s*:.*|Sources?\s*:.*",
    re.IGNORECASE,
)
_NUMBER_PATTERN = re.compile(r"\d")
_CATEGORY_FACT_PATTERNS = (
    re.compile(
        r"天气|气温|温度|阵雨|降雨|下雨|雷雨|晴|多云|阴|伞|雨衣|"
        r"weather|temperature|rain|shower|storm|sunny|cloudy|umbrella",
        re.IGNORECASE,
    ),
    re.compile(
        r"门票|票价|开放|闭馆|营业|预约|ticket|admission|open|clos|book",
        re.IGNORECASE,
    ),
    re.compile(
        r"路线|距离|公里|地铁|公交|步行|打车|route|distance|km|"
        r"metro|subway|bus|walk|taxi",
        re.IGNORECASE,
    ),
    re.compile(r"活动|展览|演出|音乐会|event|show|concert|exhibition", re.IGNORECASE),
)


class DeliveryRewriteRepository(Protocol):
    """定义改写任务读取上下文和保存审计状态的仓储边界。"""

    async def get_delivery_rewrite_context(
        self,
        failed_bot_id: int,
    ) -> DeliveryRewriteContext | None:
        """读取失败回复、对应客人问题与会话。"""
        ...

    async def save_delivery_rewrite_metadata(
        self,
        message: Message,
        metadata: dict[str, object],
    ) -> None:
        """保存原失败回复的改写审计字段。"""
        ...


class DeliveryRewriter(Protocol):
    """定义一次无工具模型改写边界。"""

    async def rewrite(self, **kwargs: Any) -> str:
        """返回通过本地事实校验的客人正文。"""
        ...


class DeliveryRewriteOutbox(Protocol):
    """定义改写后的客人投递与员工通知出站边界。"""

    async def send_text(
        self,
        open_kfid: str,
        external_userid: str,
        content: str,
        *,
        message_type: str = "text",
        delivery_retry_count: int = 0,
        retry_of_message_id: str | None = None,
    ) -> str | None:
        """登记一次客人文本发送。"""
        ...

    async def send_internal_text(
        self,
        *,
        agent_id: int,
        employee_userids: list[str],
        content: str,
    ) -> None:
        """登记一次脱敏员工通知。"""
        ...


def _deterministic_fact_fallback(
    question: str,
    blocked_reply: str,
    language: Language,
) -> str:
    """从原回复中确定性抽取分类事实，模型失败时避免只回“正在核实”。"""
    body, _evidence_footer = split_tourism_reply(blocked_reply)
    body = redact_sensitive_guest_text(body)
    body = _SOURCE_OR_URL_PATTERN.sub("", body)
    body = remove_ungrounded_property_claims(body)
    sentences = [
        sentence.strip()
        for sentence in re.findall(r"[^。！？!?]+[。！？!?]*", body)
        if sentence.strip() and "[敏感信息已隐藏]" not in sentence
    ]
    category_index = next(
        (
            index
            for index, pattern in enumerate(_CATEGORY_FACT_PATTERNS)
            if pattern.search(question) or pattern.search(body)
        ),
        None,
    )
    category_pattern = (
        _CATEGORY_FACT_PATTERNS[category_index]
        if category_index is not None
        else None
    )
    selected = [
        sentence
        for sentence in sentences
        if _NUMBER_PATTERN.search(sentence)
        or (category_pattern is not None and category_pattern.search(sentence))
    ]
    fallback = "".join(selected[:3]).strip()
    if fallback:
        safe_fallback = sanitize_guest_reply(
            fallback[:500].rstrip(),
            language=language,
            requires_human=False,
        )
        if (
            safe_fallback
            and "[敏感信息已隐藏]" not in safe_fallback
            and not contains_sensitive_guest_text(safe_fallback)
        ):
            normalized_body = re.sub(r"\s+", "", body)
            normalized_fallback = re.sub(r"\s+", "", safe_fallback)
            if normalized_body == normalized_fallback:
                zh_labels = ("天气简报：", "票务简报：", "路线简报：", "活动简报：")
                en_labels = (
                    "Weather summary: ",
                    "Ticket summary: ",
                    "Route summary: ",
                    "Event summary: ",
                )
                label_index = category_index if category_index is not None else 0
                label = (
                    en_labels[label_index]
                    if language is Language.EN
                    else zh_labels[label_index]
                )
                safe_fallback = f"{label}{safe_fallback}"
            return safe_fallback
    if language is Language.EN:
        return "I received your question and am checking the details for you."
    return "我已收到您的问题，正在为您核实相关信息，请稍等片刻。"


class GuestDeliveryRewriteJobService:
    """确保一次安全拦截只调用一次改写并登记一次二次发送。"""

    def __init__(
        self,
        *,
        repository: DeliveryRewriteRepository,
        rewriter: DeliveryRewriter,
        outbox_factory: Callable[[int, str], DeliveryRewriteOutbox],
        before_model: Callable[[], Awaitable[None]],
        on_unavailable: Callable[[int], Awaitable[None]],
        agent_id: int,
        duty_employee_userids: list[str],
    ) -> None:
        """注入仓储、模型、稳定出站工厂和员工通知目标。"""
        self._repository = repository
        self._rewriter = rewriter
        self._outbox_factory = outbox_factory
        self._before_model = before_model
        self._on_unavailable = on_unavailable
        self._agent_id = agent_id
        self._duty_employee_userids = duty_employee_userids

    async def handle(self, payload: dict[str, Any]) -> None:
        """从数据库读取原问答，改写失败时降级为事实型短回复。"""
        message_id = int(payload.get("message_id", 0) or 0)
        if message_id <= 0:
            raise ValueError("安全改写任务缺少有效消息编号")
        context = await self._repository.get_delivery_rewrite_context(message_id)
        if context is None:
            await self._on_unavailable(message_id)
            return
        metadata = dict(context.failed_bot.message_metadata or {})
        try:
            retry_count = int(metadata.get("delivery_retry_count", 0) or 0)
        except (TypeError, ValueError):
            retry_count = 0
        if retry_count >= 1:
            return

        fallback_used = bool(metadata.get("delivery_rewrite_started"))
        if not fallback_used:
            # 在调用模型前持久化“一次机会已使用”；进程中断后的重放只走本地兜底。
            metadata["delivery_rewrite_started"] = True
            await self._repository.save_delivery_rewrite_metadata(
                context.failed_bot,
                metadata,
            )
            await self._before_model()
            try:
                reply = await self._rewriter.rewrite(
                    guest_question=context.source_guest.content or "",
                    blocked_reply=context.failed_bot.content or "",
                    language=context.conversation.language,
                )
            except DeliveryRewriteUnavailableError:
                fallback_used = True
        if fallback_used:
            reply = _deterministic_fact_fallback(
                context.source_guest.content or "",
                context.failed_bot.content or "",
                context.conversation.language,
            )

        outbox = self._outbox_factory(
            context.failed_bot.id,
            context.source_guest.external_message_id,
        )
        outbox_id = await outbox.send_text(
            context.conversation.open_kfid,
            context.conversation.external_userid,
            reply,
            delivery_retry_count=1,
            retry_of_message_id=str(context.failed_bot.id),
        )
        metadata.update(
            {
                "delivery_retry_count": 1,
                "delivery_retry_pending": True,
                "delivery_rewrite_pending": False,
                "delivery_rewrite_fallback_used": fallback_used,
            }
        )
        if outbox_id is not None:
            metadata["delivery_rewrite_outbox_id"] = outbox_id
        await self._repository.save_delivery_rewrite_metadata(
            context.failed_bot,
            metadata,
        )
        if fallback_used and self._duty_employee_userids:
            await outbox.send_internal_text(
                agent_id=self._agent_id,
                employee_userids=self._duty_employee_userids,
                content="有一条客人回复经安全改写后使用了简短兜底，请管家关注后续。",
            )
