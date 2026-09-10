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


@pytest.mark.parametrize("language", ["zh", "en"])
def test_formatter_and_splitter_still_round_trip(language: str) -> None:
    """formatter 生成的页脚必须能被 splitter 完整拆回来。

    改写器、精炼和兜底都靠 split_tourism_reply 把收尾摘出去；拆不出来就会把
    收尾当正文送进模型。页脚文案一改就要连着验这条往返。
    """
    body_text = "武汉明天多云。" if language == "zh" else "Wuhan will be cloudy tomorrow."
    formatted = format_tourism_reply(
        body_text,
        [("任意来源", "https://example.org/weather")],
        date(2026, 8, 21),
        language=language,
        category="weather",
    )

    body, footer = split_tourism_reply(formatted)

    assert body == body_text
    assert footer
    assert "任意来源" not in footer


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
        "这是我今天（8月21日）帮您查到的最新预报。"
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
    assert "mainly using public information" not in formatted
    assert "Query date:" not in formatted
    assert "Sources:" not in formatted
    assert "tomorrow.;" not in formatted


def test_a_reply_without_any_search_result_may_not_claim_a_lookup() -> None:
    """没有任何搜索来源时不得声称「我今天帮您查到的」。

    页脚不再点名来源，但「查过」这个断言必须仍然为真——这条守的是诚实性本身，
    而不是来源名的可读性。
    """
    with pytest.raises(ValueError, match="搜索来源"):
        format_tourism_reply(
            "武汉明天有阵雨。",
            [],
            date(2026, 8, 21),
            language="zh",
            category="weather",
        )


def test_tourism_source_title_never_reaches_the_guest() -> None:
    """搜索结果标题连同其中夹带的域名一律不进客人可见正文。"""
    formatted = format_tourism_reply(
        "武汉明天有阵雨。",
        [("Wuhan Forecast - unknown.example", "https://unknown.example/a")],
        date(2026, 8, 21),
        language="en",
        category="weather",
    )

    assert "Wuhan Forecast" not in formatted
    assert "unknown.example" not in formatted
    assert "for you today (August 21)" in formatted


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


def test_the_footer_never_names_any_source() -> None:
    """页脚不得出现任何来源名，无论搜索结果标题长什么样。

    生产 2026-09-10 的天气回复拼出过「主要参考了武汉天气预报15天天气、最低气温
    17℃，今早出门加件外套等公开信息」——都是原始网页标题。六条真实样本显示：带
    这句的三条全被企业微信以安全限制拦下，不带的三条全部送达；而兜底重发会把
    「天气可能临时变化」这句提醒一起丢掉，坚持列举来源反倒让客人连时效提示都收不到。
    """
    formatted = format_tourism_reply(
        "武汉今天多云，最高25℃。",
        [
            ("武汉天气预报15天天气", "https://unknown.example/a"),
            ("最低气温17℃，今早出门加件外套", "https://unknown.example/b"),
            ("武汉市文化和旅游局2026年8月演出清单", "https://wlj.wuhan.gov.cn/x"),
        ],
        date(2026, 9, 10),
        language="zh",
        category="weather",
    )

    assert "主要参考了" not in formatted
    assert "公开信息" not in formatted
    for title_fragment in (
        "武汉天气预报15天天气",
        "今早出门加件外套",
        "武汉市文化和旅游局",
        "2026年8月演出清单",
    ):
        assert title_fragment not in formatted, f"来源标题泄漏进正文：{title_fragment}"
    # 时效声明仍在：这是去掉来源列举后仍要保住的东西。
    assert "这是我今天（9月10日）帮您查到的最新预报。" in formatted
    assert "天气可能临时变化，出门前可以再看一眼实时情况。" in formatted


def test_the_english_footer_also_names_no_source() -> None:
    """英文页脚同样只保留查询日期与时效提醒。"""
    formatted = format_tourism_reply(
        "Showers are likely tomorrow.",
        [("Wuhan Meteorological Service", "https://unknown.example/a")],
        date(2026, 8, 21),
        language="en",
        category="weather",
    )

    assert "mainly using public information" not in formatted
    assert "Wuhan Meteorological Service" not in formatted
    assert "I checked this latest forecast for you today (August 21)." in formatted


def test_a_model_written_source_note_is_removed_from_the_body() -> None:
    """模型自己写在正文里的来源说明必须删掉，来源声明由页脚统一承担。

    生产消息 125 的正文末尾出现「（以上天气信息来自武汉市气象台及中央气象台
    2026年9月10日发布内容，仅供出行参考。）」，紧接着又是系统页脚的「这是我今天
    帮您查到的最新预报」——同一件事说两遍。

    更要紧的是它绕过了措辞控制：v1.28.0 把来源列举从页脚拿掉正是为了避开企业微信
    的安全限制，而模型在正文里自由发挥的来源声明不受这个控制。这次措辞恰好没触发，
    下次不一定，而且真触发了改页脚也救不回来。

    正文里已有清除「参考来源：X」这类标签式写法的机制，漏的是自然语句形态。
    """
    formatted = format_tourism_reply(
        "武汉今天多云到晴，最高25℃。"
        "（以上天气信息来自武汉市气象台及中央气象台2026年9月10日发布内容，"
        "仅供出行参考。）",
        [("任意来源", "https://example.org/a")],
        date(2026, 9, 10),
        language="zh",
        category="weather",
    )

    assert "以上天气信息来自" not in formatted
    assert "武汉市气象台" not in formatted
    assert "仅供出行参考" not in formatted
    # 天气事实与系统页脚都必须完好。
    assert "武汉今天多云到晴，最高25℃。" in formatted
    assert "这是我今天（9月10日）帮您查到的最新预报。" in formatted


def test_ordinary_sentences_containing_a_source_word_are_kept() -> None:
    """判据必须有区分力：正文里普通的「来自」不能被当成来源说明删掉。"""
    body = (
        "东湖的水来自长江水系，沿岸绿道适合骑行。"
        "黄鹤楼的现存建筑来自1985年重建。"
    )
    formatted = format_tourism_reply(
        body,
        [("任意来源", "https://example.org/a")],
        date(2026, 9, 10),
        language="zh",
        category="tourism",
    )

    assert "东湖的水来自长江水系" in formatted
    assert "黄鹤楼的现存建筑来自1985年重建" in formatted
