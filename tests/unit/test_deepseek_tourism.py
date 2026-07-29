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


class EventMessagesStub:
    """返回可配置的演出正文与搜索来源。"""

    def __init__(
        self,
        *,
        text: str,
        sources: list[tuple[str, str]],
    ) -> None:
        """保存演出正文和来源标题、网址。"""
        self.text = text
        self.sources = sources
        self.requests: list[dict[str, object]] = []

    async def create(self, **kwargs):
        """生成带真实搜索结果形状的演出响应。"""
        self.requests.append(kwargs)
        results = [
            SimpleNamespace(
                type="web_search_result",
                title=title,
                url=url,
            )
            for title, url in self.sources
        ]
        return SimpleNamespace(
            content=[
                SimpleNamespace(type="server_tool_use", name="web_search"),
                SimpleNamespace(
                    type="web_search_tool_result",
                    content=results,
                ),
                SimpleNamespace(type="text", text=self.text),
            ]
        )


class EventClientStub:
    """暴露可配置的演出搜索消息资源。"""

    def __init__(
        self,
        *,
        text: str,
        sources: list[tuple[str, str]],
    ) -> None:
        """初始化演出响应。"""
        self.messages = EventMessagesStub(text=text, sources=sources)


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
    assert "当前日期：2026-07-30" in request["system"]
    assert "优先时间窗口：2026-07-30 至 2026-08-14" in request["system"]
    assert "每项活动日期必须注明完整年份" in request["system"]
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


@pytest.mark.asyncio
async def test_recent_events_remove_explicitly_stale_sources() -> None:
    """近期演出不得保留标题明确属于往年的搜索来源。"""
    searcher = DeepSeekTourismSearcher(
        client=EventClientStub(
            text="2026年8月5日，武汉有一场已确认的音乐会。",
            sources=[
                (
                    "2025武汉七夕节剧场演出活动",
                    "https://example.com/2025",
                ),
                (
                    "武汉市文化和旅游局2026年8月演出清单",
                    "https://wlj.wuhan.gov.cn/2026-events",
                ),
            ],
        ),
        model="deepseek-v4-flash",
    )

    result = await searcher.search(
        question="武汉最近有什么演出？",
        language=Language.ZH,
        queried_on=date(2026, 7, 30),
    )

    assert "2025武汉七夕节剧场演出活动" not in result
    assert "武汉市文化和旅游局2026年8月演出清单" in result


@pytest.mark.asyncio
async def test_recent_events_fill_current_year_for_dates_inside_window() -> None:
    """窗口内省略年份的日期应根据当前日期补全后再发送。"""
    searcher = DeepSeekTourismSearcher(
        client=EventClientStub(
            text="8月5日，武汉有一场已确认的音乐会。",
            sources=[
                (
                    "武汉市文化和旅游局2026年8月演出清单",
                    "https://wlj.wuhan.gov.cn/2026-events",
                )
            ],
        ),
        model="deepseek-v4-flash",
    )

    result = await searcher.search(
        question="武汉最近有什么演出？",
        language=Language.ZH,
        queried_on=date(2026, 7, 30),
    )

    assert "2026年8月5日" in result


@pytest.mark.asyncio
async def test_recent_events_reject_dates_without_current_year() -> None:
    """近期演出日期未注明当前年份时不得发送。"""
    statuses: list[str] = []
    searcher = DeepSeekTourismSearcher(
        client=EventClientStub(
            text="鹿晗演唱会在8月15日举行。",
            sources=[
                (
                    "武汉市文化和旅游局演出信息",
                    "https://wlj.wuhan.gov.cn/events",
                )
            ],
        ),
        model="deepseek-v4-flash",
        status_setter=statuses.append,
    )

    with pytest.raises(TourismSearchError):
        await searcher.search(
            question="武汉最近有什么演出？",
            language=Language.ZH,
            queried_on=date(2026, 7, 30),
        )

    assert statuses == ["degraded"]


@pytest.mark.asyncio
async def test_recent_events_require_priority_window_or_later_label() -> None:
    """半个月外的演出未明确标注时不得冒充近期推荐。"""
    searcher = DeepSeekTourismSearcher(
        client=EventClientStub(
            text="2026年9月1日，武汉有一场音乐会。",
            sources=[
                (
                    "武汉市文化和旅游局2026演出信息",
                    "https://wlj.wuhan.gov.cn/events",
                )
            ],
        ),
        model="deepseek-v4-flash",
    )

    with pytest.raises(TourismSearchError):
        await searcher.search(
            question="武汉最近有什么演出？",
            language=Language.ZH,
            queried_on=date(2026, 7, 30),
        )


@pytest.mark.asyncio
async def test_recent_exhibition_accepts_ongoing_month_range() -> None:
    """展览使用月度展期时，只要覆盖半月窗口就应保留并补全年份。"""
    searcher = DeepSeekTourismSearcher(
        client=EventClientStub(
            text="武汉博物馆专题展，展期为7月下旬至9月底。",
            sources=[
                (
                    "武汉市文化和旅游局2026暑期展览信息",
                    "https://wlj.wuhan.gov.cn/exhibitions",
                )
            ],
        ),
        model="deepseek-v4-flash",
    )

    result = await searcher.search(
        question="武汉近期有哪些展览？",
        language=Language.ZH,
        queried_on=date(2026, 7, 30),
    )

    assert "2026年7月下旬至2026年9月底" in result
