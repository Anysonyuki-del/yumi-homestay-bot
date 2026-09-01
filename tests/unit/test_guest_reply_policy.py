import pytest

from homestay_bot.domain.enums import Language
from homestay_bot.services.guest_reply_policy import (
    human_contact_reply,
    prepare_facility_issue_reply,
    prepare_guest_reply,
    sanitize_guest_reply,
)


@pytest.mark.parametrize(
    ("model_reply", "advice"),
    [
        ("收到，**请先停止使用洗衣机，不要自行拆卸。**", "停止使用洗衣机"),
        ("请先确认房间开关是否已开启。", "确认房间开关是否已开启"),
        ("请先停止冲水，以免马桶继续溢水。", "停止冲水"),
        ("请先轻推或拉住门，再尝试开锁一次。", "轻推或拉住门"),
    ],
)
def test_facility_issue_reply_keeps_model_advice_and_manual_submission(
    model_reply: str,
    advice: str,
) -> None:
    """普通设施故障应保留模型的针对性安全建议并声明人工已提交。"""
    reply = prepare_facility_issue_reply(model_reply, Language.ZH)

    assert advice in reply
    assert "**" not in reply
    assert reply.count("收到") == 1
    assert reply.endswith("我已提交管家人工处理，请您稍等。")
    assert "？" not in reply
    for forbidden in ("已出发", "已上门", "一定修好", "今天修好"):
        assert forbidden not in reply


@pytest.mark.parametrize(
    "unsafe_reply",
    [
        "请拆开洗衣机后盖检查线路。",
        "请接触电线确认是否通电。",
        "请重置房间路由器。",
        "请重启房间路由器。",
        "请反复点火测试热水器。",
        "请倒入强腐蚀疏通剂。",
        "请问故障时有什么声音",
        "已经提交管家人工处理。",
    ],
)
def test_unsafe_or_follow_up_facility_reply_falls_back(unsafe_reply: str) -> None:
    """危险操作和追问不得发给客人，只能使用通用安全降级。"""
    reply = prepare_facility_issue_reply(unsafe_reply, Language.ZH)

    assert reply == (
        "收到，请先停止使用该设施，不要拆卸或强行操作。"
        "我已提交管家人工处理，请您稍等。"
    )


def test_facility_reply_removes_promise_but_keeps_safe_advice() -> None:
    """模型夹带人员与结果承诺时，应只保留低风险排查建议。"""
    reply = prepare_facility_issue_reply(
        "请先长按童锁键三秒试试看；师傅稍后会上门并彻底修好。",
        Language.ZH,
    )

    assert "长按童锁键三秒" in reply
    assert "师傅" not in reply
    assert "修好" not in reply
    assert reply.endswith("我已提交管家人工处理，请您稍等。")


def test_english_facility_reply_uses_same_safety_boundary() -> None:
    """英文设施回复也必须保留安全建议并拦截危险操作。"""
    safe_reply = prepare_facility_issue_reply(
        "Please stop flushing to avoid overflow.",
        Language.EN,
    )
    unsafe_reply = prepare_facility_issue_reply(
        "Please restart the room router.",
        Language.EN,
    )

    assert "stop flushing" in safe_reply
    assert "restart" not in unsafe_reply
    assert "stop using the facility" in unsafe_reply


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
    """天气回复应更亲和，并把显式 Markdown 转成客人可读纯文本。"""
    raw = (
        "**天气：**武汉 2026-08-21：26～33℃，局部阵雨，"
        "建议您随身带把晴雨伞。\n\n"
        "这是我今天（8月20日）帮您查到的最新预报，主要参考了"
        "武汉市气象服务等公开信息。天气可能临时变化，"
        "出门前可以再看一眼实时情况。"
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
        "8月20日",
        "武汉市气象服务",
    ):
        assert fact in reply
    assert "**" not in reply
    assert "查询日期：" not in reply
    assert "参考来源：" not in reply
    assert "出门记得带伞" not in reply
    assert reply.count("晴雨伞") == 1
    assert prepare_guest_reply(
        reply,
        language=Language.ZH,
        question="明天天气如何？",
        requires_human=False,
    ) == reply


def test_weather_reply_does_not_duplicate_opener_without_comma() -> None:
    """模型已使用天气开场但漏写逗号时不得再次追加相同开场。"""
    reply = prepare_guest_reply(
        "我帮您看了一下武汉明天多云。",
        language=Language.ZH,
        requires_human=False,
        question="明天天气",
    )

    assert reply.count("我帮您看了一下") == 1


def test_guest_reply_normalizes_only_explicit_markdown_structures() -> None:
    """纯文本转换只处理明确 Markdown，不误删合法星号与下划线。"""
    reply = prepare_guest_reply(
        "***提醒***\n- 带好证件\n* 提前十分钟出门\n"
        "_天气会变_，房型 A* 与 code_value 保留。",
        language=Language.ZH,
        requires_human=False,
    )

    assert reply == (
        "提醒\n• 带好证件\n• 提前十分钟出门\n"
        "天气会变，房型 A* 与 code_value 保留。"
    )


@pytest.mark.parametrize(
    "existing_tip",
    [
        "建议您拿一把伞。",
        "下雨时记得打伞。",
        "建议随身备好雨衣。",
        "可以准备防雨用品。",
    ],
)
def test_weather_rain_gear_advice_is_semantically_deduplicated(
    existing_tip: str,
) -> None:
    """已有任一雨具建议时不得再追加第二条带伞提醒。"""
    reply = prepare_guest_reply(
        f"武汉明天有阵雨。{existing_tip}",
        language=Language.ZH,
        question="明天天气如何？",
        requires_human=False,
    )

    assert "出门记得带伞" not in reply


def test_english_raincoat_advice_prevents_duplicate_umbrella_tip() -> None:
    """英文已有雨衣建议时不得再追加 umbrella 提醒。"""
    reply = prepare_guest_reply(
        "Showers are likely tomorrow. Please bring a raincoat.",
        language=Language.EN,
        question="What will the weather be tomorrow?",
        requires_human=False,
    )

    assert "bring an umbrella" not in reply.lower()


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
