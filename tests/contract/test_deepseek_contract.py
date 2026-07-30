import os
from datetime import date
from typing import Any

import pytest
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from homestay_bot.config import Settings
from homestay_bot.domain.enums import Language
from homestay_bot.integrations.deepseek_client import DeepSeekGuestAssistant
from homestay_bot.integrations.deepseek_context_summarizer import (
    DeepSeekContextSummarizer,
)
from homestay_bot.integrations.deepseek_faq_drafter import DeepSeekFaqDrafter
from homestay_bot.integrations.deepseek_tourism import DeepSeekTourismSearcher
from homestay_bot.services.knowledge_service import KnowledgeSnippet

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DEEPSEEK_CONTRACT") != "1",
    reason="需要显式启用真实 DeepSeek 契约测试",
)


class EmptyKnowledge:
    """真实契约不依赖本地知识库。"""

    async def build_context(self, language: Language) -> list[KnowledgeSnippet]:
        """返回空审核知识。"""
        return []


class RecordingAvailabilityExecutor:
    """记录模型根据相对日期提出的房态查询。"""

    def __init__(self) -> None:
        """初始化调用记录。"""
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(
        self, name: str, arguments: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """返回固定可用房态。"""
        self.calls.append((name, arguments))
        return [{"property_id": 101, "days": [{"available": True}]}]


async def build_assistant(
    *,
    executor: RecordingAvailabilityExecutor | None = None,
) -> tuple[DeepSeekGuestAssistant, AsyncOpenAI, AsyncAnthropic]:
    """使用本机 DeepSeek 配置创建真实双接口助手。"""
    settings = Settings()  # type: ignore[call-arg]
    chat = AsyncOpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
    )
    anthropic = AsyncAnthropic(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_anthropic_base_url,
    )
    searcher = DeepSeekTourismSearcher(
        client=anthropic,
        model=settings.deepseek_model,
    )
    assistant = DeepSeekGuestAssistant(
        chat_client=chat,
        tourism_searcher=searcher,
        knowledge=EmptyKnowledge(),  # type: ignore[arg-type]
        model=settings.deepseek_model,
        safety_hmac_key=settings.session_secret.encode(),
        tool_executor=executor,
        local_date_provider=lambda: date(2026, 7, 30),
    )
    return assistant, chat, anthropic


async def close_clients(chat: AsyncOpenAI, anthropic: AsyncAnthropic) -> None:
    """关闭真实契约创建的两个异步客户端。"""
    await chat.close()
    await anthropic.close()


@pytest.mark.asyncio
async def test_general_question_returns_structured_decision() -> None:
    """普通问题应得到结构化实用回答。"""
    assistant, chat, anthropic = await build_assistant()
    try:
        decision = await assistant.respond(
            guest_identifier="deepseek-general",
            language=Language.ZH,
            messages=[
                {
                    "role": "user",
                    "content": "和朋友旅行时怎样更高效地协调行程？",
                }
            ],
        )
    finally:
        await close_clients(chat, anthropic)
    assert len(decision.reply_text) >= 20
    assert decision.handoff_reason is None
    assert decision.knowledge_gap is False


@pytest.mark.asyncio
async def test_property_question_marks_knowledge_gap() -> None:
    """停车资料为空时应提供替代建议并标记知识缺口。"""
    assistant, chat, anthropic = await build_assistant()
    try:
        decision = await assistant.respond(
            guest_identifier="deepseek-property",
            language=Language.ZH,
            messages=[{"role": "user", "content": "你们有停车场吗？"}],
        )
    finally:
        await close_clients(chat, anthropic)
    assert decision.knowledge_gap is True
    assert "停车" in decision.reply_text
    assert decision.staff_confirmation_required is False


@pytest.mark.asyncio
async def test_refund_question_requires_staff_confirmation() -> None:
    """退款金额不得猜测，必须要求员工核实。"""
    assistant, chat, anthropic = await build_assistant()
    try:
        decision = await assistant.respond(
            guest_identifier="deepseek-refund",
            language=Language.ZH,
            messages=[{"role": "user", "content": "这个订单能退款多少？"}],
        )
    finally:
        await close_clients(chat, anthropic)
    assert decision.staff_confirmation_required is True
    assert decision.knowledge_gap is False


@pytest.mark.asyncio
async def test_relative_dates_call_read_only_availability_tool() -> None:
    """相对日期应直接触发准确的房态查询。"""
    executor = RecordingAvailabilityExecutor()
    assistant, chat, anthropic = await build_assistant(executor=executor)
    try:
        await assistant.respond(
            guest_identifier="deepseek-relative-date",
            language=Language.ZH,
            messages=[
                {
                    "role": "user",
                    "content": "今天入住明天退房，请问还有几间房？",
                }
            ],
        )
    finally:
        await close_clients(chat, anthropic)
    assert executor.calls == [
        (
            "search_availability",
            {
                "check_in_date": "2026-07-30",
                "check_out_date": "2026-07-31",
            },
        )
    ]


@pytest.mark.asyncio
async def test_wuhan_tourism_uses_web_search_evidence() -> None:
    """旅游问题必须返回实时搜索来源名称。"""
    assistant, chat, anthropic = await build_assistant()
    try:
        decision = await assistant.respond(
            guest_identifier="deepseek-tourism",
            language=Language.ZH,
            messages=[{"role": "user", "content": "武汉近期有什么好玩的？"}],
        )
    finally:
        await close_clients(chat, anthropic)
    assert "查询日期：2026-07-30" in decision.reply_text
    assert "参考来源：" in decision.reply_text


@pytest.mark.asyncio
async def test_tourism_reply_contains_no_links() -> None:
    """旅游回复不得向企业微信客人返回链接。"""
    assistant, chat, anthropic = await build_assistant()
    try:
        decision = await assistant.respond(
            guest_identifier="deepseek-tourism-links",
            language=Language.ZH,
            messages=[{"role": "user", "content": "武汉近期有哪些展览？"}],
        )
    finally:
        await close_clients(chat, anthropic)
    assert "http://" not in decision.reply_text
    assert "https://" not in decision.reply_text


@pytest.mark.asyncio
async def test_recent_wuhan_events_are_anchored_to_current_window() -> None:
    """真实近期演出回答必须使用当前年份并排除往年内容。"""
    assistant, chat, anthropic = await build_assistant()
    try:
        decision = await assistant.respond(
            guest_identifier="deepseek-current-events",
            language=Language.ZH,
            messages=[{"role": "user", "content": "武汉最近有什么演出？"}],
        )
    finally:
        await close_clients(chat, anthropic)
    assert "查询日期：2026-07-30" in decision.reply_text
    assert "2026" in decision.reply_text
    assert "2025" not in decision.reply_text
    assert "参考来源：" in decision.reply_text


@pytest.mark.asyncio
async def test_faq_draft_marks_unknown_property_fact_for_admin_review() -> None:
    """真实 FAQ 草稿不得猜测本店停车事实，必须交给管理员核实。"""
    settings = Settings()  # type: ignore[call-arg]
    chat = AsyncOpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
    )
    try:
        draft = await DeepSeekFaqDrafter(
            client=chat,
            model=settings.deepseek_model,
        ).generate(
            canonical_question="民宿是否提供免费停车位？",
            category="交通",
            examples=["有停车位吗？", "停车收费吗？"],
            approved_knowledge=[],
        )
    finally:
        await chat.close()

    assert "【待管理员确认】" in draft.answer_zh
    assert draft.verification_items
    assert "http://" not in draft.model_dump_json()
    assert "https://" not in draft.model_dump_json()


@pytest.mark.asyncio
async def test_live_context_summary_removes_sensitive_details() -> None:
    """真实摘要契约不得返回手机号、密码或详细门牌。"""
    settings = Settings()  # type: ignore[call-arg]
    chat = AsyncOpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
    )
    try:
        result = await DeepSeekContextSummarizer(
            chat,
            settings.deepseek_model,
        ).summarize(
            tier="long",
            existing_summary="",
            messages=[
                "客户偏好安静，手机号13800138000，"
                "门锁密码839201，地址武汉市武昌区中北路12号。"
            ],
        )
    finally:
        await chat.close()

    serialized = result.summary + str(result.unresolved_items)
    assert "安静" in serialized
    assert "13800138000" not in serialized
    assert "839201" not in serialized
    assert "中北路12号" not in serialized


@pytest.mark.asyncio
async def test_live_guest_request_extracts_reviewable_task() -> None:
    """真实客服契约应在同一次响应中提取待员工确认的补给任务。"""
    assistant, chat, anthropic = await build_assistant()
    try:
        decision = await assistant.respond(
            guest_identifier="deepseek-task-contract",
            language=Language.ZH,
            messages=[
                {
                    "role": "user",
                    "content": "可以帮我补两瓶矿泉水吗？",
                }
            ],
        )
    finally:
        await close_clients(chat, anthropic)

    assert decision.task_suggestion is not None
    assert decision.task_suggestion.task_type.value == "supplies"
    assert decision.task_suggestion.description
