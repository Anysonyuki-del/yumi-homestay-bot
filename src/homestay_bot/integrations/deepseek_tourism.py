from collections.abc import Callable
from datetime import date
from typing import Any

from homestay_bot.domain.enums import Language
from homestay_bot.integrations.tourism import (
    TourismSearchError,
    WebSearchStatus,
    format_tourism_reply,
)


class DeepSeekTourismSearcher:
    """通过 DeepSeek Anthropic 原生 Web Search 回答武汉旅游问题。"""

    def __init__(
        self,
        *,
        client: Any,
        model: str,
        status_setter: Callable[[WebSearchStatus], None] | None = None,
    ) -> None:
        """注入 Anthropic 兼容客户端、模型和健康状态写入器。"""
        self._client = client
        self._model = model
        self._status_setter = status_setter

    def _set_status(self, status: WebSearchStatus) -> None:
        """更新非敏感搜索能力状态。"""
        if self._status_setter is not None:
            self._status_setter(status)

    @staticmethod
    def _extract_content(
        response: Any,
    ) -> tuple[str, list[tuple[str, str]]]:
        """从 Anthropic 内容块提取正文和搜索来源。"""
        text_parts: list[str] = []
        citations: list[tuple[str, str]] = []
        seen_urls: set[str] = set()
        for block in getattr(response, "content", []):
            if getattr(block, "type", None) == "text":
                text = str(getattr(block, "text", "")).strip()
                if text:
                    text_parts.append(text)
            if getattr(block, "type", None) != "web_search_tool_result":
                continue
            for result in getattr(block, "content", []) or []:
                url = str(getattr(result, "url", "")).strip()
                title = str(getattr(result, "title", "")).strip()
                if url and url not in seen_urls:
                    citations.append((title or url, url))
                    seen_urls.add(url)
        return "\n".join(text_parts), citations

    async def search(
        self,
        *,
        question: str,
        language: Language,
        queried_on: date,
    ) -> str:
        """执行有限武汉搜索，要求正文和搜索证据同时存在。"""
        system = (
            "你是武汉民宿的旅游客服。必须使用 Web Search 的真实结果回答，"
            "优先武汉政府、文旅局、景区、场馆和主办方来源。"
            "简单推荐给出3至5项，规划问题给出半日或一日路线。"
            "不要在正文中输出网址或 Markdown 链接。"
            if language is Language.ZH
            else (
                "You are a Wuhan homestay travel assistant. Use Web Search "
                "evidence, prefer official sources, and do not include URLs."
            )
        )
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=1800,
                system=system,
                messages=[{"role": "user", "content": question}],
                tools=[
                    {
                        "type": "web_search_20250305",
                        "name": "web_search",
                        "max_uses": 2,
                        "user_location": {
                            "type": "approximate",
                            "country": "CN",
                            "city": "Wuhan",
                            "region": "Hubei",
                        },
                    }
                ],
            )
        except Exception as error:
            status_code = getattr(error, "status_code", None)
            status: WebSearchStatus = (
                "unsupported" if status_code in {400, 404, 422} else "degraded"
            )
            self._set_status(status)
            raise TourismSearchError(status) from error

        text, citations = self._extract_content(response)
        if not text or not citations:
            self._set_status("degraded")
            raise TourismSearchError("degraded")
        self._set_status("ok")
        return format_tourism_reply(text, citations, queried_on)
