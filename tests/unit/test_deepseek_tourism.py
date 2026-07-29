from datetime import date
from types import SimpleNamespace

import pytest

from homestay_bot.domain.enums import Language
from homestay_bot.integrations.deepseek_tourism import DeepSeekTourismSearcher
from homestay_bot.integrations.tourism import TourismSearchError


class MessagesStub:
    """记录 Anthropic Messages 请求并返回固定搜索结果。"""

    def __init__(self, *, include_sources: bool = True) -> None:
        """配置是否返回有效搜索证据。"""
        self.include_sources = include_sources
        self.requests: list[dict[str, object]] = []

    async def create(self, **kwargs):
        """返回搜索工具结果和旅游正文。"""
        self.requests.append(kwargs)
        blocks = [SimpleNamespace(type="server_tool_use", name="web_search")]
        if self.include_sources:
            blocks.append(
                SimpleNamespace(
                    type="web_search_tool_result",
                    content=[
                        SimpleNamespace(
                            type="web_search_result",
                            title="武汉市文化和旅游局",
                            url="https://wlj.wuhan.gov.cn/example",
                        )
                    ],
                )
            )
        blocks.append(
            SimpleNamespace(
                type="text",
                text=(
                    "推荐[黄鹤楼](https://example.com)、东湖和湖北省博物馆。"
                ),
            )
        )
        return SimpleNamespace(content=blocks)


class AnthropicClientStub:
    """模拟 Anthropic SDK 客户端。"""

    def __init__(self, *, include_sources: bool = True) -> None:
        """暴露可记录的 Messages 资源。"""
        self.messages = MessagesStub(include_sources=include_sources)


@pytest.mark.asyncio
async def test_deepseek_tourism_uses_native_search_and_removes_links() -> None:
    """旅游回答必须使用武汉搜索证据，并移除客人可见链接。"""
    client = AnthropicClientStub()
    statuses: list[str] = []
    searcher = DeepSeekTourismSearcher(
        client=client,
        model="deepseek-v4-flash",
        status_setter=statuses.append,
    )

    result = await searcher.search(
        question="武汉近期有什么好玩的？",
        language=Language.ZH,
        queried_on=date(2026, 7, 30),
    )

    request = client.messages.requests[0]
    assert request["model"] == "deepseek-v4-flash"
    assert request["messages"] == [
        {"role": "user", "content": "武汉近期有什么好玩的？"}
    ]
    assert request["tools"][0]["type"] == "web_search_20250305"
    assert request["tools"][0]["max_uses"] == 2
    assert "参考来源：武汉市文化和旅游局" in result
    assert "https://" not in result
    assert statuses == ["ok"]


@pytest.mark.asyncio
async def test_deepseek_tourism_rejects_answer_without_search_evidence() -> None:
    """没有搜索证据时不得把模型常识冒充实时结果。"""
    statuses: list[str] = []
    searcher = DeepSeekTourismSearcher(
        client=AnthropicClientStub(include_sources=False),
        model="deepseek-v4-flash",
        status_setter=statuses.append,
    )

    with pytest.raises(TourismSearchError) as error:
        await searcher.search(
            question="武汉近期有什么好玩的？",
            language=Language.ZH,
            queried_on=date(2026, 7, 30),
        )

    assert error.value.status == "degraded"
    assert statuses == ["degraded"]
