from datetime import date
from types import SimpleNamespace

from homestay_bot.integrations.tourism import (
    WebSearchState,
    extract_url_citations,
    format_tourism_reply,
    is_tourism_query,
    latest_user_question,
    web_search_tool,
)


def test_tourism_query_is_gated_without_stealing_booking_queries() -> None:
    """旅游问题应联网，但房态问题必须继续交给百居易工具。"""
    assert is_tourism_query([{"role": "user", "content": "武汉有哪些地方好玩？"}])
    assert is_tourism_query([{"role": "user", "content": "黄鹤楼门票多少钱？"}])
    assert not is_tourism_query([{"role": "user", "content": "8月1日还有房吗？"}])
    assert not is_tourism_query([{"role": "user", "content": "房间价格是多少？"}])


def test_latest_user_question_drops_conversation_history() -> None:
    """联网输入只能保留最后一条客人旅游问题。"""
    messages = [
        {"role": "user", "content": "我叫张三，手机号13800138000"},
        {"role": "assistant", "content": "您好"},
        {"role": "user", "content": "武汉最近有什么展览？"},
    ]

    assert latest_user_question(messages) == {
        "role": "user",
        "content": "武汉最近有什么展览？",
    }


def test_web_search_tool_uses_wuhan_location() -> None:
    """联网搜索应固定武汉、湖北、中国的近似位置。"""
    assert web_search_tool() == {
        "type": "web_search",
        "search_context_size": "low",
        "user_location": {
            "type": "approximate",
            "country": "CN",
            "city": "Wuhan",
            "region": "Hubei",
        },
    }


def test_citations_are_deduplicated_and_appended_with_query_date() -> None:
    """Responses 注解应转换为企业微信可点击的去重来源列表。"""
    annotations = [
        SimpleNamespace(
            type="url_citation",
            url="https://example.gov.cn/a",
            title="官方活动页",
        ),
        SimpleNamespace(
            type="url_citation",
            url="https://example.gov.cn/a",
            title="重复来源",
        ),
    ]
    response = SimpleNamespace(
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(annotations=annotations)],
            )
        ]
    )

    citations = extract_url_citations(response)

    assert citations == [("官方活动页", "https://example.gov.cn/a")]
    formatted = format_tourism_reply(
        "推荐[东湖](https://example.gov.cn/a)，详情见 https://example.gov.cn/b。",
        citations,
        date(2026, 7, 29),
    )
    assert formatted == (
        "推荐东湖，详情见\n\n查询日期：2026-07-29\n"
        "参考来源：官方活动页"
    )
    assert "http://" not in formatted
    assert "https://" not in formatted


def test_sources_fall_back_to_web_search_call_action() -> None:
    """Fenno 未返回正文注解时应读取 web_search_call.action.sources。"""
    source = SimpleNamespace(
        type="url",
        url="https://www.wuhan.gov.cn/zjwh/whly/index.shtml",
    )
    response = SimpleNamespace(
        output=[
            SimpleNamespace(
                type="web_search_call",
                action=SimpleNamespace(sources=[source]),
            ),
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(annotations=[])],
            ),
        ]
    )

    assert extract_url_citations(response) == [
        ("www.wuhan.gov.cn", "https://www.wuhan.gov.cn/zjwh/whly/index.shtml")
    ]


def test_null_web_search_sources_do_not_hide_later_citations() -> None:
    """Fenno 的 sources 为 null 时应跳过，并继续读取后续正文注解。"""
    citation = SimpleNamespace(
        type="url_citation",
        url="https://wlj.wuhan.gov.cn/",
        title="武汉市文化和旅游局",
    )
    response = SimpleNamespace(
        output=[
            SimpleNamespace(
                type="web_search_call",
                action=SimpleNamespace(sources=None),
            ),
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(annotations=[citation])],
            ),
        ]
    )

    assert extract_url_citations(response) == [
        ("武汉市文化和旅游局", "https://wlj.wuhan.gov.cn/")
    ]


def test_web_search_state_starts_unknown_and_can_change() -> None:
    """首次真实调用前必须显示 unknown。"""
    state = WebSearchState()

    assert state.get() == "unknown"
    state.set("ok")
    assert state.get() == "ok"
