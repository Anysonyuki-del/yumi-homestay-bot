import os
from datetime import date
from typing import Any

import pytest
from openai import AsyncOpenAI

from homestay_bot.config import Settings
from homestay_bot.domain.enums import Language
from homestay_bot.integrations.openai_client import GuestAssistant
from homestay_bot.integrations.tourism import WebSearchStatus
from homestay_bot.services.knowledge_service import KnowledgeSnippet

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_FENNO_WEB_SEARCH_CONTRACT") != "1",
    reason="需要显式启用真实 Fenno 联网契约测试",
)


class EmptyKnowledge:
    """契约测试不依赖本地知识库。"""

    async def build_context(self, language: Language) -> list[KnowledgeSnippet]:
        """返回空知识，强制答案来自联网搜索。"""
        return []


class RecordingAvailabilityExecutor:
    """记录真实模型根据相对日期提出的只读房态查询。"""

    def __init__(self) -> None:
        """初始化工具调用记录。"""
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """返回固定可用房态，不访问真实百居易。"""
        self.calls.append((name, arguments))
        return [
            {
                "property_id": 101,
                "days": [
                    {"date": "2026-07-29", "available": True},
                    {"date": "2026-07-30", "available": True},
                ],
            }
        ]


@pytest.mark.asyncio
async def test_fenno_model_returns_tourism_answer_with_citations() -> None:
    """验证生产模型、结构化输出、web_search 和引用注解可同时工作。"""
    settings = Settings()  # type: ignore[call-arg]
    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
    )
    statuses: list[WebSearchStatus] = []
    assistant = GuestAssistant(
        client=client,
        knowledge=EmptyKnowledge(),  # type: ignore[arg-type]
        model=settings.openai_model,
        safety_hmac_key=settings.session_secret.encode(),
        web_search_status_setter=statuses.append,
    )
    try:
        decision = await assistant.respond(
            guest_identifier="fenno-contract-guest",
            language=Language.ZH,
            messages=[{"role": "user", "content": "武汉有哪些地方好玩？"}],
        )
    finally:
        await client.close()

    assert decision.handoff_reason is None
    assert "查询日期：" in decision.reply_text
    assert "参考来源：" in decision.reply_text
    assert "http://" not in decision.reply_text
    assert "https://" not in decision.reply_text
    assert statuses == ["ok"]


@pytest.mark.asyncio
async def test_fenno_resolves_relative_booking_dates_before_querying() -> None:
    """今天入住明天退房应直接换算日期并调用房态工具。"""
    settings = Settings()  # type: ignore[call-arg]
    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
    )
    executor = RecordingAvailabilityExecutor()
    assistant = GuestAssistant(
        client=client,
        knowledge=EmptyKnowledge(),  # type: ignore[arg-type]
        model=settings.openai_model,
        safety_hmac_key=settings.session_secret.encode(),
        tool_executor=executor,
        local_date_provider=lambda: date(2026, 7, 29),
    )
    try:
        decision = await assistant.respond(
            guest_identifier="fenno-relative-date-contract",
            language=Language.ZH,
            messages=[
                {
                    "role": "user",
                    "content": "今天入住明天退房，请问还有房吗？",
                }
            ],
        )
    finally:
        await client.close()

    assert executor.calls == [
        (
            "search_availability",
            {
                "check_in_date": "2026-07-29",
                "check_out_date": "2026-07-30",
            },
        )
    ]
    assert decision.handoff_reason is None
    assert "提供具体" not in decision.reply_text
