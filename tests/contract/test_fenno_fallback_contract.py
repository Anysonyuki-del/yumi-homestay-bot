import os

import pytest
from openai import AsyncOpenAI

from homestay_bot.config import Settings
from homestay_bot.domain.enums import Language
from homestay_bot.integrations.openai_client import GuestAssistant
from homestay_bot.services.knowledge_service import KnowledgeSnippet

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_FENNO_FALLBACK_CONTRACT") != "1",
    reason="需要显式启用真实 Fenno 兜底契约测试",
)


class EmptyKnowledge:
    """返回空审核知识，验证真实模型兜底行为。"""

    async def build_context(self, language: Language) -> list[KnowledgeSnippet]:
        """确保回答不依赖本地知识条目。"""
        return []


async def build_assistant() -> tuple[GuestAssistant, AsyncOpenAI]:
    """使用当前 `.env` 构造真实 Fenno 助手。"""
    settings = Settings()  # type: ignore[call-arg]
    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
    )
    assistant = GuestAssistant(
        client=client,
        knowledge=EmptyKnowledge(),  # type: ignore[arg-type]
        model=settings.openai_model,
        safety_hmac_key=settings.session_secret.encode(),
    )
    return assistant, client


@pytest.mark.asyncio
async def test_general_question_uses_model_fallback() -> None:
    """普通问题应直接获得合理回复，不产生员工提醒状态。"""
    assistant, client = await build_assistant()
    try:
        decision = await assistant.respond(
            guest_identifier="fenno-general-fallback",
            language=Language.ZH,
            messages=[
                {
                    "role": "user",
                    "content": "和朋友旅行时怎样更高效地协调行程？",
                }
            ],
        )
    finally:
        await client.close()

    assert len(decision.reply_text) >= 20
    assert decision.handoff_reason is None
    assert decision.knowledge_gap is False
    assert decision.staff_confirmation_required is False


@pytest.mark.asyncio
async def test_property_question_marks_knowledge_gap() -> None:
    """空知识下的停车问题应给替代建议并标记知识缺口。"""
    assistant, client = await build_assistant()
    try:
        decision = await assistant.respond(
            guest_identifier="fenno-property-gap",
            language=Language.ZH,
            messages=[{"role": "user", "content": "你们有停车场吗？"}],
        )
    finally:
        await client.close()

    assert any(
        wording in decision.reply_text
        for wording in ("未确认", "暂未", "没有确认")
    )
    assert "停车" in decision.reply_text
    assert decision.knowledge_gap is True
    assert decision.knowledge_gap_topic
    assert decision.staff_confirmation_required is False
    assert decision.handoff_reason is None


@pytest.mark.asyncio
async def test_unresolved_refund_requires_staff_confirmation() -> None:
    """空知识下的退款金额不得猜测，必须标记员工核实。"""
    assistant, client = await build_assistant()
    try:
        decision = await assistant.respond(
            guest_identifier="fenno-transaction-confirmation",
            language=Language.ZH,
            messages=[{"role": "user", "content": "这个订单能退款多少？"}],
        )
    finally:
        await client.close()

    assert "核实" in decision.reply_text or "确认" in decision.reply_text
    assert decision.staff_confirmation_required is True
    assert decision.staff_confirmation_reason
    assert decision.knowledge_gap is False
    assert decision.handoff_reason is None
