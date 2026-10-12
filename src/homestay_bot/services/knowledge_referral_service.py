"""资料核实草稿任务（修复 Spec 2026-10-08 fix2 §2.6(3)(4)）。

管理员点「整理草稿」后入队一个 `knowledge_referral_draft` 作业，载荷只有转交编号与代次。
任务读取本代次冻结的管家消息，净化、按预算截断后交给草稿模型；结果只按「整理中 + 当前代次」
条件写回。重试只由 Worker 承担（最多 3 次），耗尽后由失败回调或 `recover_stale` 写「草稿失败」。
"""

import logging
from typing import Any

from homestay_bot.domain.enums import KnowledgeReferralStatus
from homestay_bot.domain.models import Message
from homestay_bot.integrations.deepseek_faq_drafter import (
    DeepSeekFaqDrafter,
    FaqDraftUnavailableError,
)
from homestay_bot.repositories.knowledge_referrals import SQLAlchemyKnowledgeReferralRepository
from homestay_bot.services.sensitive_text import sanitize_referral_text
from homestay_bot.worker import RetrySafeJobError

logger = logging.getLogger(__name__)

KNOWLEDGE_REFERRAL_DRAFT_JOB_TYPE = "knowledge_referral_draft"
# 交给草稿模型的管家消息总字数上限，与合并客人消息的上限一致；超出时截掉较早的消息。
STAFF_MESSAGE_BUDGET = 2000


def message_cleared(message: Message) -> bool:
    """消息是否已被测试数据清空（正文为空且 metadata 含 cleared_by）。"""
    metadata = message.message_metadata
    return message.content is None or (isinstance(metadata, dict) and "cleared_by" in metadata)


def budget_messages(texts: list[tuple[int, str]]) -> tuple[list[tuple[int, str]], bool]:
    """从最新一条往前取，总长不超过预算；返回（按时间升序的保留消息，是否有截断）。

    被截掉的消息不交给模型，因而也不能作为依据片段的来源。最新一条单独超长时只保留其
    末尾预算长度。
    """
    kept: list[tuple[int, str]] = []
    used = 0
    truncated = False
    for message_id, text in reversed(texts):
        if used + len(text) <= STAFF_MESSAGE_BUDGET:
            kept.append((message_id, text))
            used += len(text)
            continue
        truncated = True
        if not kept:
            kept.append((message_id, text[-STAFF_MESSAGE_BUDGET:]))
        break
    kept.reverse()
    return kept, truncated or len(kept) < len(texts)


class ReferralDraftJobService:
    """执行一次资料核实草稿整理。"""

    def __init__(
        self,
        *,
        referrals: SQLAlchemyKnowledgeReferralRepository,
        drafter: DeepSeekFaqDrafter,
    ) -> None:
        """注入转交仓储与草稿生成器。"""
        self._referrals = referrals
        self._drafter = drafter

    async def handle(self, payload: dict[str, Any]) -> None:
        """处理一项草稿任务；记录不在「整理中 + 当前代次」时直接结束，不调用模型。"""
        referral_id = int(payload["referral_id"])
        generation = int(payload["generation"])
        referral = await self._referrals.get(referral_id)
        if (
            referral is None
            or referral.status is not KnowledgeReferralStatus.DRAFTING
            or referral.generation != generation
        ):
            # 结果已提交、换代、入库、关闭或清空后的重放：幂等结束（V6-R4）。
            return
        frozen = await self._referrals.messages_by_ids(referral.source_message_ids)
        texts = [
            (message.id, sanitize_referral_text(message.content or ""))
            for message in frozen
            if not message_cleared(message) and (message.content or "").strip()
        ]
        if not texts:
            await self._referrals.finish_draft(
                referral_id, generation=generation, status=KnowledgeReferralStatus.NO_ANSWER
            )
            return
        kept, truncated = budget_messages(texts)
        staff_messages = [text for _message_id, text in kept]
        try:
            draft = await self._drafter.generate_from_staff(
                guest_question=referral.question or "",
                canonical_question=referral.canonical_question or "",
                staff_messages=staff_messages,
            )
        except FaqDraftUnavailableError as error:
            # 由 Worker 重试；第三次失败由失败回调写「草稿失败」，这里不另计次数。
            raise RetrySafeJobError("资料核实草稿暂不可用") from error
        if not draft.found_answer:
            await self._referrals.finish_draft(
                referral_id, generation=generation, status=KnowledgeReferralStatus.NO_ANSWER
            )
            return
        quotes = []
        for quote in (item.strip() for item in draft.source_quotes):
            source_id = next(
                (message_id for message_id, text in kept if quote in text), None
            )
            quotes.append({"text": quote, "message_id": source_id})
        written = await self._referrals.finish_draft(
            referral_id,
            generation=generation,
            status=KnowledgeReferralStatus.DRAFTED,
            draft={
                "guest_only": draft.guest_only,
                "category": draft.category,
                "question_zh": draft.question_zh,
                "answer_zh": draft.answer_zh,
                "question_en": draft.question_en,
                "answer_en": draft.answer_en,
                "keywords": draft.keywords,
                "source_quotes": quotes,
                "used_message_ids": [message_id for message_id, _text in kept],
                "truncated": truncated,
                # 页面如实标注：模型已整理，不等于管理员已确认。
                "model_drafted": True,
            },
        )
        if not written:
            logger.info("资料核实草稿写回被拒（已换代或已结束）：referral_id=%s", referral_id)
