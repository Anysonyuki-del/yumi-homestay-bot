from datetime import date

from homestay_bot.integrations.tourism import (
    WebSearchState,
    format_tourism_reply,
    is_tourism_query,
    latest_user_question,
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


def test_citations_are_deduplicated_and_appended_with_query_date() -> None:
    """DeepSeek 搜索来源应转换为无链接的去重名称列表。"""
    citations = [("官方活动页", "https://example.gov.cn/a")]
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
def test_web_search_state_starts_unknown_and_can_change() -> None:
    """首次真实调用前必须显示 unknown。"""
    state = WebSearchState()

    assert state.get() == "unknown"
    state.set("ok")
    assert state.get() == "ok"
