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


class EvidenceThenTextMessagesStub(MessagesStub):
    """首轮只有搜索证据，第二轮才返回最终正文。"""

    async def create(self, **kwargs):
        """模拟 DeepSeek 偶发完成搜索却漏掉客人可见正文。"""
        if not self.requests:
            self.requests.append(kwargs)
            return SimpleNamespace(
                content=[
                    SimpleNamespace(type="server_tool_use", name="web_search"),
                    SimpleNamespace(
                        type="web_search_tool_result",
                        content=[
                            SimpleNamespace(
                                type="web_search_result",
                                title="武汉市文化和旅游局",
                                url="https://wlj.wuhan.gov.cn/example",
                            )
                        ],
                    ),
                ]
            )
        return await super().create(**kwargs)


class EvidenceThenTextClientStub:
    """暴露首次无正文、第二次成功的搜索消息资源。"""

    def __init__(self) -> None:
        """初始化可记录两次请求的 Messages 资源。"""
        self.messages = EvidenceThenTextMessagesStub()


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


class MutableClock:
    """提供可由测试推进的单调时钟。"""

    def __init__(self) -> None:
        """从零开始记录测试时间。"""
        self.value = 0.0

    def __call__(self) -> float:
        """返回当前测试时间。"""
        return self.value


class FlakyMessagesStub(MessagesStub):
    """首次搜索失败，后续返回有效结果。"""

    async def create(self, **kwargs):
        """第一次抛错，第二次复用正常搜索响应。"""
        if not self.requests:
            self.requests.append(kwargs)
            raise RuntimeError("temporary failure")
        return await super().create(**kwargs)


class FlakyClientStub:
    """暴露首次失败的 Messages 资源。"""

    def __init__(self) -> None:
        """初始化可恢复的搜索资源。"""
        self.messages = FlakyMessagesStub()


class SuccessThenFailureMessagesStub(MessagesStub):
    """首次成功，第二次失败，用于验证健康状态不被缓存掩盖。"""

    async def create(self, **kwargs):
        """首轮返回搜索结果，后续联网请求抛出临时故障。"""
        if not self.requests:
            return await super().create(**kwargs)
        self.requests.append(kwargs)
        raise RuntimeError("temporary failure")


class SuccessThenFailureClientStub:
    """暴露先成功后失败的 Messages 资源。"""

    def __init__(self) -> None:
        """初始化可记录的搜索资源。"""
        self.messages = SuccessThenFailureMessagesStub()


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
    assert "优先选出最值得推荐的3项" in request["system"]
    assert "700至900字" in request["system"]
    assert request["max_tokens"] == 3000
    assert "参考来源：武汉市文化和旅游局" in result
    assert "https://" not in result
    assert statuses == ["ok"]


@pytest.mark.asyncio
async def test_weather_query_pins_wuhan_and_explicit_tomorrow_date() -> None:
    """相对日期天气问题必须把地点和目标日期明确交给联网搜索。"""
    client = AnthropicClientStub()
    searcher = DeepSeekTourismSearcher(
        client=client,
        model="deepseek-v4-flash",
    )

    await searcher.search(
        question="明天天气如何",
        language=Language.ZH,
        queried_on=date(2026, 8, 20),
    )

    request = client.messages.requests[0]
    user_query = request["messages"][0]["content"]
    assert "武汉" in user_query
    assert "2026-08-21" in user_query
    assert "明天天气如何" in user_query
    assert "天气问题必须明确回答目标日期" in request["system"]
    assert "温暖、简洁、可靠的民宿管家口吻" in request["system"]
    assert "我帮您看了一下" in request["system"]
    assert "根据搜索结果给一条实用提醒" in request["system"]


@pytest.mark.asyncio
async def test_live_tourism_query_defaults_to_wuhan_when_location_is_omitted() -> None:
    """未写地点的旅游联网问题必须按武汉搜索。"""
    client = AnthropicClientStub()
    searcher = DeepSeekTourismSearcher(
        client=client,
        model="deepseek-v4-flash",
    )

    await searcher.search(
        question="黄鹤楼门票多少钱？",
        language=Language.ZH,
        queried_on=date(2026, 8, 20),
    )

    request = client.messages.requests[0]
    user_query = request["messages"][0]["content"]
    assert "武汉" in user_query
    assert "原始问题：黄鹤楼门票多少钱？" in user_query
    assert "未指定地点的旅游联网问题默认按武汉市查询" in request["system"]


@pytest.mark.asyncio
async def test_explicit_non_wuhan_location_is_preserved() -> None:
    """客人明确指定其他城市时不能被武汉默认值覆盖。"""
    client = AnthropicClientStub()
    searcher = DeepSeekTourismSearcher(
        client=client,
        model="deepseek-v4-flash",
    )

    await searcher.search(
        question="北京明天天气如何？",
        language=Language.ZH,
        queried_on=date(2026, 8, 20),
    )

    request = client.messages.requests[0]
    user_query = request["messages"][0]["content"]
    assert "北京" in user_query
    assert "武汉" not in user_query
    assert "2026-08-21" in user_query


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
async def test_deepseek_tourism_retries_evidence_without_final_text() -> None:
    """已有搜索证据但无正文时应保留思考并限重试一次。"""
    client = EvidenceThenTextClientStub()
    statuses: list[str] = []
    searcher = DeepSeekTourismSearcher(
        client=client,
        model="deepseek-v4-flash",
        status_setter=statuses.append,
    )

    result = await searcher.search(
        question="武汉近期有啥好玩的",
        language=Language.ZH,
        queried_on=date(2026, 8, 13),
    )

    assert len(client.messages.requests) == 2
    assert all("extra_body" not in item for item in client.messages.requests)
    assert all(
        "结束前必须输出一段客人可见的最终正文" in item["system"]
        for item in client.messages.requests
    )
    assert "参考来源：武汉市文化和旅游局" in result
    assert statuses == ["ok"]


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


@pytest.mark.asyncio
async def test_successful_tourism_reply_is_cached_for_same_request() -> None:
    """同一天同语言的相同问题应直接复用已校验答案。"""
    client = AnthropicClientStub()
    searcher = DeepSeekTourismSearcher(
        client=client,
        model="deepseek-v4-flash",
    )

    first = await searcher.search(
        question="  武汉近期有什么好玩的？ ",
        language=Language.ZH,
        queried_on=date(2026, 7, 30),
    )
    second = await searcher.search(
        question="武汉近期有什么好玩的？",
        language=Language.ZH,
        queried_on=date(2026, 7, 30),
    )

    assert second == first
    assert len(client.messages.requests) == 1


@pytest.mark.asyncio
async def test_tourism_cache_expires_after_ten_minutes() -> None:
    """缓存满十分钟后必须重新联网，避免长期复用旧活动。"""
    clock = MutableClock()
    client = AnthropicClientStub()
    searcher = DeepSeekTourismSearcher(
        client=client,
        model="deepseek-v4-flash",
        clock=clock,
    )

    await searcher.search(
        question="武汉有什么好玩的？",
        language=Language.ZH,
        queried_on=date(2026, 7, 30),
    )
    clock.value = 601.0
    await searcher.search(
        question="武汉有什么好玩的？",
        language=Language.ZH,
        queried_on=date(2026, 7, 30),
    )

    assert len(client.messages.requests) == 2


@pytest.mark.asyncio
async def test_failed_tourism_search_is_not_cached() -> None:
    """联网失败不得进入缓存，下一次相同问题仍应重新搜索。"""
    client = FlakyClientStub()
    searcher = DeepSeekTourismSearcher(
        client=client,
        model="deepseek-v4-flash",
    )

    with pytest.raises(TourismSearchError):
        await searcher.search(
            question="武汉有什么好玩的？",
            language=Language.ZH,
            queried_on=date(2026, 7, 30),
        )
    result = await searcher.search(
        question="武汉有什么好玩的？",
        language=Language.ZH,
        queried_on=date(2026, 7, 30),
    )

    assert "参考来源：" in result
    assert len(client.messages.requests) == 2


@pytest.mark.asyncio
async def test_tourism_cache_separates_date_and_language() -> None:
    """不同查询日期或语言不得共享旅游答案。"""
    client = AnthropicClientStub()
    searcher = DeepSeekTourismSearcher(
        client=client,
        model="deepseek-v4-flash",
    )

    for language, queried_on in (
        (Language.ZH, date(2026, 7, 30)),
        (Language.EN, date(2026, 7, 30)),
        (Language.ZH, date(2026, 7, 31)),
    ):
        await searcher.search(
            question="武汉有什么好玩的？",
            language=language,
            queried_on=queried_on,
        )

    assert len(client.messages.requests) == 3


@pytest.mark.asyncio
async def test_cache_hit_does_not_hide_latest_search_failure() -> None:
    """缓存命中不得把最近一次真实联网故障重新标记为正常。"""
    statuses: list[str] = []
    client = SuccessThenFailureClientStub()
    searcher = DeepSeekTourismSearcher(
        client=client,
        model="deepseek-v4-flash",
        status_setter=statuses.append,
    )
    await searcher.search(
        question="武汉有什么好玩的？",
        language=Language.ZH,
        queried_on=date(2026, 7, 30),
    )
    with pytest.raises(TourismSearchError):
        await searcher.search(
            question="武汉有什么好吃的？",
            language=Language.ZH,
            queried_on=date(2026, 7, 30),
        )

    await searcher.search(
        question="武汉有什么好玩的？",
        language=Language.ZH,
        queried_on=date(2026, 7, 30),
    )

    assert statuses == ["ok", "degraded"]
    assert len(client.messages.requests) == 2


@pytest.mark.asyncio
async def test_tourism_cache_evicts_oldest_entry_at_capacity() -> None:
    """超过容量时淘汰最早条目，确保进程内缓存有界。"""
    client = AnthropicClientStub()
    searcher = DeepSeekTourismSearcher(
        client=client,
        model="deepseek-v4-flash",
        cache_max_entries=2,
    )
    for question in ("武汉景点一", "武汉景点二", "武汉景点三", "武汉景点一"):
        await searcher.search(
            question=question,
            language=Language.ZH,
            queried_on=date(2026, 7, 30),
        )

    assert len(client.messages.requests) == 4


def test_tourism_cache_rejects_non_positive_capacity() -> None:
    """非正缓存容量应在初始化时明确拒绝，而不是运行时崩溃。"""
    with pytest.raises(ValueError, match="cache_max_entries"):
        DeepSeekTourismSearcher(
            client=AnthropicClientStub(),
            model="deepseek-v4-flash",
            cache_max_entries=0,
        )


@pytest.mark.asyncio
async def test_english_tourism_prompt_stays_below_hard_reply_limit() -> None:
    """英文旅游回答使用短词数目标，避免发送层截断证据尾注。"""
    client = AnthropicClientStub()
    searcher = DeepSeekTourismSearcher(
        client=client,
        model="deepseek-v4-flash",
    )

    result = await searcher.search(
        question="What should I visit in Wuhan?",
        language=Language.EN,
        queried_on=date(2026, 7, 30),
    )

    system = client.messages.requests[0]["system"]
    assert "120-180 words" in system
    assert "700-900 words" not in system
    assert "warm, concise, and reliable homestay host" in system
    assert "Do not change dates, temperatures, prices, availability, or sources" in system
    assert "查询日期：" in result
    assert "参考来源：" in result
