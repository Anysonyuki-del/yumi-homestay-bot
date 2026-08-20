import pytest

from homestay_bot.domain.enums import Language
from homestay_bot.services.guest_reply_policy import (
    human_contact_reply,
    prepare_guest_reply,
    sanitize_guest_reply,
)


def test_human_reply_keeps_safe_washer_advice_and_removes_promises() -> None:
    """洗衣机建议可保留，但师傅上门和解决结果承诺必须删除。"""
    reply = sanitize_guest_reply(
        (
            "别急哈，我会尽快安排师傅上门帮您查看处理。"
            "您可以先长按童锁键三秒试试看；"
            "要是还不行，师傅到了会帮您彻底解决好。"
        ),
        language=Language.ZH,
        requires_human=True,
    )

    assert "长按童锁键三秒试试看" in reply
    assert reply.endswith("我会立即联系管家来处理，请您稍等。")
    assert reply.count("我会立即联系管家来处理，请您稍等。") == 1
    for forbidden in ("安排师傅", "师傅到了", "上门", "彻底解决", "保证", "一定"):
        assert forbidden not in reply


@pytest.mark.parametrize(
    "unsafe_reply",
    [
        "我已经安排好了。",
        "我们马上安排工作人员给您送到。",
        "保证今天彻底解决。",
        "师傅稍后会上门处理。",
        "管家一定会帮您解决问题。",
        "维修人员已经在路上。",
        "工作人员很快就到。",
        "今天给您修好。",
    ],
)
def test_all_promise_only_replies_fall_back_to_human_contact(
    unsafe_reply: str,
) -> None:
    """只含承诺的回复不得留下任何虚构结果，必须回退为管家联系话术。"""
    reply = sanitize_guest_reply(
        unsafe_reply,
        language=Language.ZH,
        requires_human=True,
    )

    assert reply == f"我已收到您的诉求。{human_contact_reply(Language.ZH)}"


def test_non_human_information_drops_accidental_service_promise() -> None:
    """普通信息回复若夹带服务承诺也必须删除，但不追加人工接管。"""
    reply = sanitize_guest_reply(
        "武汉地铁通常晚上十点后逐步收班。我们保证安排司机接您。",
        language=Language.ZH,
        requires_human=False,
    )

    assert reply == "武汉地铁通常晚上十点后逐步收班。"
    assert "联系管家" not in reply


@pytest.mark.parametrize(
    "unsafe_reply",
    [
        "我们正在安排师傅，请稍候。",
        "维修人员正在赶来。",
        "管家随后到您房间。",
        "A technician is on the way.",
        "We're sending someone now.",
        "Our host is coming to your room.",
    ],
)
def test_non_human_reply_blocks_dispatch_promise_variants(
    unsafe_reply: str,
) -> None:
    """普通信息出口也必须删除中英文人员调度承诺变体。"""
    reply = sanitize_guest_reply(
        unsafe_reply,
        language=Language.EN if unsafe_reply.isascii() else Language.ZH,
        requires_human=False,
    )

    assert unsafe_reply not in reply


def test_human_reply_keeps_only_neutral_ack_and_safe_actions() -> None:
    """人工场景不能因模型使用未知承诺变体而发送执行结果。"""
    reply = sanitize_guest_reply(
        "我已收到您的洗衣机问题。维修人员正往这边赶。请先拔掉电源。",
        language=Language.ZH,
        requires_human=True,
    )

    assert "已收到您的洗衣机问题" in reply
    assert "请先拔掉电源" in reply
    assert "正往这边赶" not in reply
    assert reply.endswith(human_contact_reply(Language.ZH))


@pytest.mark.parametrize(
    "safe_advice",
    [
        "请保持在安全区域等待。",
        "闻到燃气味时请远离明火并开窗通风。",
    ],
)
def test_human_reply_preserves_emergency_safety_advice(safe_advice: str) -> None:
    """人工接管不能删除燃气、火灾等必要避险指令。"""
    reply = sanitize_guest_reply(
        safe_advice,
        language=Language.ZH,
        requires_human=True,
    )

    assert safe_advice in reply
    assert reply.endswith(human_contact_reply(Language.ZH))


def test_english_handoff_is_promise_free() -> None:
    """英文人工接管同样不得承诺技术人员上门或解决结果。"""
    reply = sanitize_guest_reply(
        "A technician will come shortly and fix it. Please unplug the washer first.",
        language=Language.EN,
        requires_human=True,
    )

    assert "Please unplug the washer first." in reply
    assert reply.endswith(human_contact_reply(Language.EN))
    assert "technician will" not in reply.lower()
    assert "fix it" not in reply.lower()


@pytest.mark.parametrize("language", [Language.ZH, Language.EN])
def test_human_reply_sanitization_is_idempotent(language: Language) -> None:
    """已经过客人策略处理的安抚再次进入出口时，正文不得发生变化。"""
    raw = (
        "抱歉给您添麻烦了"
        if language is Language.ZH
        else "Sorry for the inconvenience."
    )
    first = sanitize_guest_reply(raw, language=language, requires_human=True)
    second = sanitize_guest_reply(first, language=language, requires_human=True)

    assert second == first


def test_weather_reply_uses_warm_host_tone_without_changing_facts() -> None:
    """天气回复应更亲和，但日期、温度、天气和来源必须原样保留。"""
    raw = (
        "武汉 2026-08-21：26～33℃，局部阵雨。"
        "\n查询日期：2026-08-20\n参考来源：武汉市气象服务。"
    )

    reply = prepare_guest_reply(
        raw,
        language=Language.ZH,
        question="明天天气如何？",
        requires_human=False,
    )

    assert reply.startswith("我帮您看了一下，")
    for fact in (
        "武汉",
        "2026-08-21",
        "26～33℃",
        "局部阵雨",
        "查询日期：2026-08-20",
        "参考来源：武汉市气象服务",
    ):
        assert fact in reply
    assert "出门记得带伞" in reply
    assert prepare_guest_reply(
        reply,
        language=Language.ZH,
        question="明天天气如何？",
        requires_human=False,
    ) == reply


@pytest.mark.parametrize(
    "unsafe_reply",
    [
        "真的很抱歉给您添麻烦了，我们一定负责到底。",
        "对不起，师傅已经出发，十分钟内一定到。",
        "这是我们的责任，今晚保证给您处理好。",
    ],
)
def test_high_risk_handoff_is_neutral_and_promise_free(unsafe_reply: str) -> None:
    """高危转人工只客观记录并联系值班管家，不道歉、不定责、不承诺。"""
    reply = prepare_guest_reply(
        unsafe_reply,
        language=Language.ZH,
        requires_human=True,
        high_risk=True,
    )

    assert reply == (
        "您的情况我已记录。"
        "我会立即联系值班管家跟进处理，请保持联系方式畅通。"
    )
    for forbidden in (
        "抱歉",
        "对不起",
        "责任",
        "一定",
        "保证",
        "已经出发",
        "十分钟",
        "处理好",
    ):
        assert forbidden not in reply


def test_high_risk_emergency_keeps_safety_steps_before_neutral_handoff() -> None:
    """火灾燃气等现实风险必须先保留撤离和报警，再执行中立转人工。"""
    reply = prepare_guest_reply(
        "很抱歉，请立即离开危险区域，并根据现场情况拨打 119。师傅马上到。",
        language=Language.ZH,
        requires_human=True,
        high_risk=True,
    )

    assert reply == (
        "请立即离开危险区域，并根据现场情况拨打 119。"
        "您的情况我已记录。"
        "我会立即联系值班管家跟进处理，请保持联系方式畅通。"
    )
    assert "抱歉" not in reply


def test_high_risk_reply_policy_is_idempotent() -> None:
    """高危固定话术重复经过所有客人出口时不得再次变化。"""
    first = prepare_guest_reply(
        "情况已经记录，我们正在安排人员。",
        language=Language.ZH,
        requires_human=True,
        high_risk=True,
    )
    second = prepare_guest_reply(
        first,
        language=Language.ZH,
        requires_human=True,
        high_risk=True,
    )

    assert second == first
