from datetime import date

import pytest

from homestay_bot.integrations.tourism import (
    WebSearchState,
    classify_tourism_query,
    format_tourism_reply,
    is_tourism_query,
    latest_user_question,
    split_tourism_reply,
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
        ("What is the admission fee for Yellow Crane Tower?", "live"),
        ("Can I book admission to Yellow Crane Tower?", "live"),
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


def test_split_tourism_reply_finds_footer_after_whitespace_is_flattened() -> None:
    """统一回复策略压平换行后仍应识别本地生成的自然来源收尾。"""
    body, footer = split_tourism_reply(
        "武汉明天多云。这是我今天（8月21日）帮您查到的最新预报，"
        "主要参考了武汉市气象台等公开信息。"
        "天气可能临时变化，出门前可以再看一眼实时情况。"
    )

    assert body == "武汉明天多云。"
    assert footer.startswith("这是我今天（8月21日）")


@pytest.mark.parametrize(
    "reply_text",
    [
        "这是我今天（8月21日）整理的东湖路线：乘地铁8号线。",
        "武汉明天多云。这是我今天（8月21日）拍到的活动海报，演出19:30开始。",
        "I checked this latest route twice before recommending it.",
        (
            "这是我今天（8月21日）帮您查到的最新天气信息，主要参考了"
            "游客反馈等公开信息。随后我们去东湖。"
        ),
        (
            "I checked this latest forecast for you today (August 21), mainly using "
            "public information from visitor comments. Then we went to East Lake."
        ),
    ],
)
def test_split_tourism_reply_does_not_strip_ordinary_similar_text(
    reply_text: str,
) -> None:
    """只有完整本地证据收尾可拆分，相似的普通正文必须原样保留。"""
    body, footer = split_tourism_reply(reply_text)

    assert body == reply_text
    assert footer == ""


@pytest.mark.parametrize("source_name", ["Wuhan Gov. Portal", "U.S. Embassy Wuhan"])
def test_split_tourism_reply_accepts_periods_inside_english_source_name(
    source_name: str,
) -> None:
    """英文可读来源名含句点时，formatter 与 splitter 仍须完整往返。"""
    formatted = format_tourism_reply(
        "Wuhan will be cloudy tomorrow.",
        [(source_name, "https://example.org/weather")],
        date(2026, 8, 21),
        language="en",
        category="weather",
    )

    body, footer = split_tourism_reply(formatted)

    assert body == "Wuhan will be cloudy tomorrow."
    assert source_name in footer


def test_weather_reply_uses_natural_evidence_footer_without_markdown() -> None:
    """真实天气缺陷正文应转换成自然纯文本，并限制客人侧来源数量。"""
    citations = [
        ("武汉市气象台", "https://weather.example/a"),
        ("武汉市文化和旅游局", "https://wlj.wuhan.gov.cn/b"),
        ("湖北省气象局", "https://weather.example/c"),
    ]
    formatted = format_tourism_reply(
        "***天气：***武汉2026年8月22日局地有阵雨，建议您随身带把晴雨伞。"
        "（查询日期：2026-08-21）参考来源：https://weather.example/a",
        citations,
        date(2026, 8, 21),
        language="zh",
        category="weather",
    )
    assert formatted == (
        "天气：武汉2026年8月22日局地有阵雨，建议您随身带把晴雨伞。\n\n"
        "这是我今天（8月21日）帮您查到的最新预报，主要参考了"
        "武汉市气象台、武汉市文化和旅游局等公开信息。"
        "天气可能临时变化，出门前可以再看一眼实时情况。"
    )
    assert "**" not in formatted
    assert "***" not in formatted
    assert "查询日期：" not in formatted
    assert "参考来源：" not in formatted
    assert "湖北省气象局" not in formatted
    assert "http://" not in formatted
    assert "https://" not in formatted
    assert (
        format_tourism_reply(
            formatted,
            citations,
            date(2026, 8, 21),
            language="zh",
            category="weather",
        )
        == formatted
    )

@pytest.mark.parametrize(
    ("category", "expected_phrase", "expected_caution"),
    [
        ("event", "最新活动信息", "活动安排可能临时调整"),
        ("ticket", "最新票务与开放信息", "票价和开放安排可能临时调整"),
        ("tourism", "最新出行信息", "出行信息可能临时变化"),
    ],
)
def test_tourism_categories_use_natural_chinese_footers(
    category: str,
    expected_phrase: str,
    expected_caution: str,
) -> None:
    """活动、票务和普通时效旅游信息应使用各自的管家式收尾。"""
    formatted = format_tourism_reply(
        "- 第一项\n* 第二项",
        [("武汉市文化和旅游局", "https://wlj.wuhan.gov.cn/a")],
        date(2026, 8, 21),
        language="zh",
        category=category,
    )

    assert formatted.startswith("• 第一项\n• 第二项")
    assert expected_phrase in formatted
    assert expected_caution in formatted
    assert "查询日期：" not in formatted
    assert "参考来源：" not in formatted


def test_english_tourism_footer_is_natural_and_link_free() -> None:
    """英文时效回复也应保留自然时效依据，不暴露内部字段标签。"""
    formatted = format_tourism_reply(
        "**Weather:** Showers are likely tomorrow. "
        "(Query date: August 21; Sources: weather.example)",
        [("Wuhan Meteorological Service", "https://weather.example/a")],
        date(2026, 8, 21),
        language="en",
        category="weather",
    )

    assert formatted.startswith("Weather: Showers are likely tomorrow.")
    assert "I checked this latest forecast for you today (August 21)" in formatted
    assert "Wuhan Meteorological Service" in formatted
    assert "Query date:" not in formatted
    assert "Sources:" not in formatted
    assert "tomorrow.;" not in formatted


def test_tourism_reply_rejects_sources_without_readable_names() -> None:
    """没有官方映射或可读标题时不得把域名直接展示给客人。"""
    with pytest.raises(ValueError, match="可读来源"):
        format_tourism_reply(
            "武汉明天有阵雨。",
            [("unknown.example", "https://unknown.example/a")],
            date(2026, 8, 21),
            language="zh",
            category="weather",
        )


def test_tourism_source_title_strips_embedded_domain() -> None:
    """来源标题中夹带的域名应删除，只保留可读机构名称。"""
    formatted = format_tourism_reply(
        "武汉明天有阵雨。",
        [("Wuhan Forecast - unknown.example", "https://unknown.example/a")],
        date(2026, 8, 21),
        language="en",
        category="weather",
    )

    assert "Wuhan Forecast" in formatted
    assert "unknown.example" not in formatted


@pytest.mark.parametrize(
    ("body", "expected_fact"),
    [
        ("参考来源：武汉市文旅局。门票建议提前一天预约。", "门票建议提前一天预约。"),
        (
            "Sources: Wuhan Tourism Bureau. Tickets should be booked a day ahead.",
            "Tickets should be booked a day ahead.",
        ),
        ("参考来源：https://weather.example/a。明天有阵雨。", "明天有阵雨。"),
        (
            "Sources: https://weather.example/a.Tickets should be booked ahead.",
            "Tickets should be booked ahead.",
        ),
        (
            "Sources: https://weather.example/a.I recommend booking ahead.",
            "I recommend booking ahead.",
        ),
        (
            "Sources: https://weather.example/a.FAQ details follow.",
            "FAQ details follow.",
        ),
    ],
)
def test_legacy_source_label_cleanup_preserves_following_fact(
    body: str,
    expected_fact: str,
) -> None:
    """删除旧来源字段时必须保留同一行后续的客人可用事实。"""
    formatted = format_tourism_reply(
        body,
        [("武汉市文化和旅游局", "https://wlj.wuhan.gov.cn/a")],
        date(2026, 8, 21),
        language="en" if body.startswith("Sources") else "zh",
        category="ticket",
    )

    assert expected_fact in formatted
    assert "参考来源：" not in formatted
    assert "Sources:" not in formatted


def test_web_search_state_starts_unknown_and_can_change() -> None:
    """首次真实调用前必须显示 unknown。"""
    state = WebSearchState()

    assert state.get() == "unknown"
    state.set("ok")
    assert state.get() == "ok"
