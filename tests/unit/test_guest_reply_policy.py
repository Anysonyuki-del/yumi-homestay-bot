import pytest

from homestay_bot.domain.enums import Language
from homestay_bot.services.guest_reply_policy import (
    human_contact_reply,
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
