from datetime import date

import pytest

from homestay_bot.integrations.tourism import (
    WebSearchState,
    classify_tourism_query,
    format_tourism_reply,
    is_tourism_query,
    latest_user_question,
)


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("武汉哪里好玩？", "stable"),
        ("最近玩啥？", "stable"),
        ("推荐几个武汉经典景点", "stable"),
        ("武汉有什么美食？", "stable"),
        ("武汉近期有什么活动？", "live"),
        ("今天去哪玩？", "live"),
        ("黄鹤楼门票多少钱？", "live"),
        ("黄鹤楼门票怎么预订？", "live"),
        ("Can I book tickets for the concert?", "live"),
        ("黄鹤楼几点关门？", "live"),
        ("黄鹤楼今天开门吗？", "live"),
        ("黄鹤楼现在营业吗？", "live"),
        ("8月20日黄鹤楼开放吗？", "live"),
        ("从民宿怎么去东湖？", "live"),
        ("从民宿到东湖要多久？", "live"),
        ("黄鹤楼离民宿远吗？", "live"),
        ("去黄鹤楼堵不堵？", "live"),
        ("武汉明天天气适合玩吗？", "live"),
        ("8月1日还有房吗？", "none"),
        ("房间价格是多少？", "none"),
        ("想预订地铁附近的房间", "none"),
        ("预订可以看演出的房间", "none"),
        ("预订黄鹤楼景点附近的房间", "none"),
    ],
)
def test_tourism_query_is_classified_by_freshness(
    question: str,
    expected: str,
) -> None:
    """稳定旅游走快速模型，时效旅游才使用联网深度搜索。"""
    assert (
        classify_tourism_query([{"role": "user", "content": question}])
        == expected
    )


def test_tourism_query_is_gated_without_stealing_booking_queries() -> None:
    """旅游问题应联网，但房态问题必须继续交给百居易工具。"""
    assert is_tourism_query([{"role": "user", "content": "武汉有哪些地方好玩？"}])
    assert is_tourism_query([{"role": "user", "content": "黄鹤楼门票多少钱？"}])
    assert is_tourism_query(
        [{"role": "user", "content": "黄鹤楼离春和景明多少公里？"}]
    )
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
