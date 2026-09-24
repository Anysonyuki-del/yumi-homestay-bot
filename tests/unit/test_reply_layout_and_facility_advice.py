"""客人回复的段落保留、确定性排版与设施建议清单。

样本取自 2026-09-23 测试会话的真实回复（只读），用来锁定三类问题：清洗时句号后的
分段被压平、【小节】挤在一段、设施回复里条件写反与问候语拼接。
"""

import re

import pytest

from homestay_bot.domain.enums import Language
from homestay_bot.services.guest_reply_policy import (
    layout_guest_reply,
    prepare_facility_advice_reply,
    prepare_guest_reply,
    sanitize_guest_reply,
)

WEATHER_MODEL_REPLY = (
    "我帮您看了一下武汉9月24日（周四）的天气：\n\n"
    "【天气】阴天到多云，局地有短时小雨。全天以阴天、多云为主。\n\n"
    "【气温】最高31℃、最低24℃，白天体感略偏闷热。\n\n"
    "【实用提醒】建议包里放一把折叠伞；湿度偏高，走石板路时留意防滑。\n\n"
    "这是我今天（9月23日）帮您查到的最新预报。天气可能临时变化，出门前可以再看一眼实时情况。"
)
FLATTENED_WEATHER = (
    "我帮您看了一下武汉9月24日（周四）的天气：\n\n"
    "【天气】阴天到多云，局地有短时小雨。全天以阴天、多云为主。【气温】最高31℃、最低24℃，"
    "白天体感略偏闷热。【实用提醒】建议包里放一把折叠伞；湿度偏高，走石板路时留意防滑。"
    "这是我今天（9月23日）帮您查到的最新预报。天气可能临时变化，出门前可以再看一眼实时情况。"
)


def _without_whitespace(text: str) -> str:
    """去掉所有空白后比较，用于断言排版只动换行不动文字。"""
    return re.sub(r"\s", "", text)


def test_sanitizer_keeps_paragraph_breaks_after_sentence_ends() -> None:
    """句号后的空行必须保留，否则【小节】和时效说明会被压成一段。"""
    cleaned = sanitize_guest_reply(
        WEATHER_MODEL_REPLY, language=Language.ZH, requires_human=False
    )

    assert "多云为主。\n\n【气温】" in cleaned
    assert "防滑。\n\n这是我今天" in cleaned


def test_sanitizer_still_removes_commitments_line_by_line() -> None:
    """保留段落不能放松承诺过滤：被删的句子与原来完全相同，段落不因此残留空行。"""
    reply = "早餐在一楼。\n\n我会马上帮您安排好房间。\n\n出门记得带伞。"

    cleaned = sanitize_guest_reply(reply, language=Language.ZH, requires_human=False)

    assert "安排好房间" not in cleaned
    assert cleaned == "早餐在一楼。\n\n出门记得带伞。"


def test_sanitizer_filtering_decisions_are_unchanged() -> None:
    """逐句过滤的取舍与修复前一致：去掉空白后，保留下来的文字完全相同。"""
    reply = "第一句。\n我会立即联系管家。\n• 列表项一\n• 列表项二。第三句！"
    legacy = "".join(
        sentence.strip()
        for sentence in re.findall(r"[^。！？；;.!?]+[。！？；;.!?]*", reply)
        if sentence.strip() and "我会立即联系" not in sentence
    )

    cleaned = sanitize_guest_reply(reply, language=Language.ZH, requires_human=False)

    assert _without_whitespace(cleaned) == _without_whitespace(legacy)


def test_layout_splits_inline_section_headers_and_footer() -> None:
    """【小节】与时效说明各自另起一段；只动换行，不增删文字。"""
    laid_out = layout_guest_reply(FLATTENED_WEATHER, Language.ZH)

    assert "\n\n【气温】" in laid_out
    assert "\n\n【实用提醒】" in laid_out
    assert "\n\n这是我今天（9月23日）" in laid_out
    assert _without_whitespace(laid_out) == _without_whitespace(FLATTENED_WEATHER)


def test_layout_leaves_brackets_inside_a_sentence_alone() -> None:
    """句子中间的【名称】不是小节标题，不能被拆开。"""
    text = "请关注【平安武汉】公众号了解预约信息。"

    assert layout_guest_reply(text, Language.ZH) == text


def test_layout_puts_each_bullet_on_its_own_line() -> None:
    """列表项挤在同一行时逐项换行，并收拢多余空行。"""
    text = "近期可留意：• 烟花秀\n\n\n\n• 游园会。"

    laid_out = layout_guest_reply(text, Language.ZH)

    assert laid_out == "近期可留意：\n• 烟花秀\n\n• 游园会。"


def test_prepare_guest_reply_applies_layout_to_flattened_text() -> None:
    """普通回复出口最后统一排版：挤在一段的真实样本被拆回小节。"""
    prepared = prepare_guest_reply(
        FLATTENED_WEATHER,
        language=Language.ZH,
        requires_human=False,
        question="明天天气咋样",
    )

    assert "\n\n【气温】" in prepared
    assert "\n\n这是我今天" in prepared


def test_real_toilet_reply_advice_is_rebuilt_without_the_inverted_condition() -> None:
    """真实样本：条件写反的应急建议与问候语都不会出现，开头结尾由代码组装。"""
    advice = [
        "您好",
        "先别反复按压或自行拆修，也别往马桶里继续冲水，避免溢水",
        "可以先看一下水箱盖是否盖平",
        "如果马桶还能勉强用，就先到公共区域或附近的卫生间应急",
    ]

    reply = prepare_facility_advice_reply(advice, Language.ZH)

    assert reply == (
        "收到，先别反复按压或自行拆修，也别往马桶里继续冲水，避免溢水。"
        "可以先看一下水箱盖是否盖平。我已提交管家人工处理，请您稍等。"
    )


@pytest.mark.parametrize(
    "bad_item",
    [
        "如果还在漏水，就关闭总闸",
        "若灯还亮，请换个插座试试",
        "要不要我帮您叫人？",
        "请拆开面板检查线路",
        "维修师傅十分钟内到",
        "我已帮您提交维修",
        "请先" + "非常详细地描述一下" * 6,
    ],
    ids=["if", "ruo", "question", "unsafe", "promise", "submitted", "too-long"],
)
def test_each_bad_facility_item_is_dropped(bad_item: str) -> None:
    """条件句、问句、危险操作、承诺、自称已提交与过长的建议逐条去掉。"""
    reply = prepare_facility_advice_reply([bad_item, "先停止使用该设施"], Language.ZH)

    assert reply == "收到，先停止使用该设施。我已提交管家人工处理，请您稍等。"


def test_facility_advice_is_capped_at_two_items() -> None:
    """最多保留两条，按模型给出的安全优先顺序取前两条。"""
    reply = prepare_facility_advice_reply(
        ["先别再冲水", "不要自行拆修", "看一下水箱盖是否盖平"], Language.ZH
    )

    assert reply == "收到，先别再冲水。不要自行拆修。我已提交管家人工处理，请您稍等。"


@pytest.mark.parametrize("advice", [None, [], ["如果还能用就去别处"]])
def test_missing_or_filtered_advice_falls_back(advice: list[str] | None) -> None:
    """没有清单或全部被去掉时，使用固定兜底，不发空回复。"""
    reply = prepare_facility_advice_reply(advice, Language.ZH)

    assert reply == "收到，请先停止使用该设施，不要拆卸或强行操作。我已提交管家人工处理，请您稍等。"


def test_english_facility_advice_uses_the_same_rules() -> None:
    """英文同样逐条检查并由代码组装开头结尾。"""
    reply = prepare_facility_advice_reply(
        [
            "Hello!",
            "Please stop flushing the toilet",
            "If it still works, use the lobby restroom",
            "Check whether the tank lid is seated properly.",
        ],
        Language.EN,
    )

    assert reply == (
        "Thanks for letting us know. Please stop flushing the toilet. "
        "Check whether the tank lid is seated properly. "
        "I've submitted this to the host for manual handling. Please wait a moment."
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (["先别再冲水", 3, None], ["先别再冲水"]),
        ("先别再冲水", None),
        ([], None),
        (None, None),
    ],
    ids=["mixed-items", "string", "empty", "missing"],
)
def test_decision_tolerates_malformed_facility_advice(raw, expected) -> None:
    """模型给出格式异常的清单时只丢弃该字段或其中非文本项，不让整轮回复失败。"""
    from homestay_bot.integrations.deepseek_client import AssistantDecision

    decision = AssistantDecision(
        reply_text="收到。",
        language=Language.ZH,
        intent="facility_fault",
        confidence=0.9,
        facility_advice=raw,
    )

    assert decision.facility_advice == expected


REAL_WEATHER_WITH_HEADER = (
    "【天气速览】我帮您看了一下，2026年9月25日（周五，中秋节）武汉白天多云，之后转小到中雨。"
    "\n\n【实用提醒】出门随身带伞，晚归注意路面湿滑。"
)


def test_weather_opener_is_not_repeated_when_the_model_wrote_it_after_a_header() -> None:
    """真实样本：模型把开场白写在【标题】之后，系统不能再补一遍。"""
    prepared = prepare_guest_reply(
        REAL_WEATHER_WITH_HEADER,
        language=Language.ZH,
        requires_human=False,
        question="明天天气咋样",
    )

    assert prepared.count("我帮您看了一下") == 1
    assert prepared.startswith("【天气速览】")


def test_weather_opener_sits_on_its_own_line_before_a_header() -> None:
    """正文以【标题】开头且没有开场白时，补的开场白单独成段，不黏在标题前。"""
    prepared = prepare_guest_reply(
        "【天气速览】明天多云，最高31℃。",
        language=Language.ZH,
        requires_human=False,
        question="明天天气咋样",
    )

    assert prepared.startswith("我帮您看了一下：\n\n【天气速览】")


def test_weather_opener_is_still_added_to_plain_text() -> None:
    """普通正文没有开场白时照旧补在句首。"""
    prepared = prepare_guest_reply(
        "明天多云，最高31℃。",
        language=Language.ZH,
        requires_human=False,
        question="明天天气咋样",
    )

    assert prepared.startswith("我帮您看了一下，明天多云")


def test_english_weather_opener_is_not_repeated_after_a_header() -> None:
    """英文同理：开头一段里已有开场白就不再补。"""
    prepared = prepare_guest_reply(
        "【Overview】I checked the forecast for you. Cloudy tomorrow, high of 31°C.",
        language=Language.EN,
        requires_human=False,
        question="What's the weather tomorrow?",
    )

    assert prepared.count("I checked the forecast") == 1


@pytest.mark.parametrize(
    ("language", "phrase", "question", "body"),
    [
        (Language.ZH, "我帮您看了一下", "明天天气", "明天晴，20至25℃。"),
        (Language.EN, "I checked the forecast", "Weather tomorrow?", "Sunny tomorrow."),
    ],
)
def test_weather_opener_after_standalone_heading(language, phrase, question, body) -> None:
    """独立标题之后已有开场白时，不应重复；不扫描后续段落。"""
    prepared = prepare_guest_reply(
        f"【天气】\n\n{phrase}，{body}",
        language=language, requires_human=False, question=question,
    )
    assert prepared.count(phrase) == 1
    later = prepare_guest_reply(
        f"【天气】\n\n{body}\n\n{phrase}",
        language=language, requires_human=False, question=question,
    )
    assert later.count(phrase) == 2
