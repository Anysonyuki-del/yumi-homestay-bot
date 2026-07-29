import os

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
