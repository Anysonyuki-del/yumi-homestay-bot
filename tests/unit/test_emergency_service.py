import re

import pytest

from homestay_bot.domain.enums import Language
from homestay_bot.services.emergency_service import (
    EmergencyClassification,
    EmergencyService,
)


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("门锁坏了，我进不了房间", "access"),
        ("房间着火了", "fire"),
        ("洗衣机突然冒烟了", "fire"),
        ("空调冒出火花还有焦味", "fire"),
        ("台灯突然冒烟了", "fire"),
        ("冰箱有焦味", "fire"),
        ("吹风机冒火花了", "fire"),
        ("闻到燃气味", "gas"),
        ("热水器好像漏电", "electric"),
        ("有人触电了", "electric"),
        ("有人威胁要打我", "violence"),
        ("客人突然昏迷，需要急救", "medical"),
        ("The door lock is broken and I cannot get in", "access"),
        ("There is a fire in the room", "fire"),
        ("I smell gas", "gas"),
        ("Someone got an electric shock", "electric"),
        ("I am being threatened", "violence"),
        ("We need a medical emergency response", "medical"),
    ],
)
def test_classify_emergency_in_chinese_and_english(
    text: str, category: str
) -> None:
    """确定性规则应覆盖中英文入住安全紧急事件。"""
    result = EmergencyService().classify(text)

    assert result.is_emergency is True
    assert result.category == category


def test_emergency_reply_uses_fixed_safety_message() -> None:
    """紧急事件回复应来自固定模板，不能交由模型自由编写。"""
    service = EmergencyService()

    result = service.classify("房间着火了")

    zh_reply = service.safety_reply(result, Language.ZH)
    en_reply = service.safety_reply(result, Language.EN)

    assert "119" in zh_reply
    assert zh_reply.endswith(
        "我会立即联系值班管家跟进处理，请保持联系方式畅通。"
    )
    assert "抱歉" not in zh_reply
    assert "leave" in en_reply.lower()
    assert "has been alerted" not in en_reply.lower()


def test_each_dangerous_category_gets_its_own_safety_instruction() -> None:
    """每类危险必须给出针对性指令，不能都退回同一句通用文案。

    生产验收（2026-09-10）发送「房间里有煤气味」后，客人收到的是「请先确保自身安全，
    不要自行处理故障。」——分类器明明判成了 gas（审计记录 emergency:gas），文案却没
    用上这个结果。对燃气泄漏而言这句话信息量不足，客人可能就留在房间里等管家，而
    开关一次电灯就可能引爆。

    guest_reply_policy 的高危安全句白名单里早已预留「开窗通风」「切断燃气」「切断
    电源」「拨打119/110/120」「呼叫急救」等词——设计上本就打算按类别给指令，只是
    实现停在了 fire 一类。
    """
    service = EmergencyService()
    generic = service.safety_reply(
        EmergencyClassification(True, "access"), Language.ZH
    )

    expectations = {
        "gas": ("开窗通风", "离开"),
        "electric": ("切断电源", "不要触碰"),
        "medical": ("120",),
        "violence": ("110",),
        "fire": ("119", "离开"),
    }
    for category, required in expectations.items():
        reply = service.safety_reply(
            EmergencyClassification(True, category), Language.ZH
        )
        assert reply != generic, f"{category} 仍在使用通用文案"
        for fragment in required:
            assert fragment in reply, f"{category} 的指令缺少「{fragment}」：{reply}"


def test_every_safety_instruction_survives_the_high_risk_whitelist() -> None:
    """每一条安全动作都必须穿过白名单，不能只留下第一句。

    safety_reply 的输出会经 prepare_guest_reply 的高危分支逐句重组，只有命中白名单
    的句子才保留。实现时英文 gas 文案的第二句「Do not switch any electrical device
    on or off…」就被吃掉了——英文白名单只有 do not touch，没有 do not switch/use。
    这类丢失不会报错，客人只会少收到一半指令，所以判据必须逐片段断言，不能只看长度。
    """
    service = EmergencyService()
    expectations = {
        Language.ZH: {
            "gas": ("开窗通风", "不要开关电器", "不要使用明火"),
            "electric": ("不要触碰", "切断电源", "120"),
            "medical": ("120", "不要随意搬动"),
            "violence": ("110", "前往安全地点"),
            "fire": ("119", "离开房间"),
        },
        Language.EN: {
            "gas": ("leave the room", "open the windows", "do not switch",
                    "do not use an open flame"),
            "electric": ("do not touch", "power switch", "emergency services"),
            "medical": ("emergency services", "do not move"),
            "violence": ("safe place", "call the police"),
            "fire": ("119", "leave the room"),
        },
    }
    for language, per_category in expectations.items():
        for category, fragments in per_category.items():
            reply = service.safety_reply(
                EmergencyClassification(True, category), language
            )
            for fragment in fragments:
                assert fragment.lower() in reply.lower(), (
                    f"{category}/{language.value} 丢了指令片段「{fragment}」：{reply}"
                )


def test_an_english_guest_also_gets_the_category_specific_instruction() -> None:
    """英文客人同样要拿到分类指令，而不是统一的兜底句。"""
    service = EmergencyService()
    generic = service.safety_reply(
        EmergencyClassification(True, "access"), Language.EN
    )

    for category in ("gas", "electric", "medical", "violence"):
        reply = service.safety_reply(
            EmergencyClassification(True, category), Language.EN
        )
        assert reply != generic, f"{category} 英文仍在使用通用文案"


def test_english_safety_replies_are_not_glued_together() -> None:
    """英文句子之间必须有空格，也不能留下悬空的分号。

    高危回复原本用空串连接各安全句：中文正常，英文会拼成「immediately.Call 119」。
    另外分号是分句点，写在分号后的非安全句会被白名单滤掉，只留一个悬空分号。
    这两处都不会报错，只是客人读到的文案变形。
    """
    service = EmergencyService()
    for category in ("fire", "gas", "electric", "medical", "violence", "access"):
        reply = service.safety_reply(
            EmergencyClassification(True, category), Language.EN
        )
        assert not re.search(r"[a-z][.!?][A-Z]", reply), f"{category} 句子粘连：{reply}"
        assert "; " not in reply.replace("; call", "; call"), f"{category} 有悬空分号：{reply}"
        assert ";" not in reply, f"{category} 残留分号：{reply}"


@pytest.mark.parametrize(
    "text",
    ["我们现在该怎么办", "然后呢", "要不要报警", "还要做什么", "好的", "收到", "我们已经出来了"],
)
def test_help_seeking_follow_ups_are_recognized(text: str) -> None:
    """紧急情况进行中，这些后续消息给固定处置答复，不交给模型或联网。"""
    from homestay_bot.services.emergency_service import is_emergency_follow_up

    assert is_emergency_follow_up(text)


@pytest.mark.parametrize("text", ["早餐几点开始", "附近有药店吗？营业到几点", "可以延迟退房吗"])
def test_independent_questions_are_not_follow_ups(text: str) -> None:
    """独立问题照常回答（边界 Spec A07），不被固定答复挡住。"""
    from homestay_bot.services.emergency_service import is_emergency_follow_up

    assert not is_emergency_follow_up(text)


class _Entry:
    """最小审核知识条目。"""

    def __init__(self, category: str, keywords: list[str], zh: str, en: str) -> None:
        """保存字段。"""
        self.category = category
        self.keywords = keywords
        self.answer_zh = zh
        self.answer_en = en


def test_follow_up_reply_prefers_reviewed_emergency_knowledge() -> None:
    """有「紧急处置」审核知识时原样使用；本店专属位置信息由管家维护在知识里。"""
    from homestay_bot.services.emergency_service import emergency_follow_up_reply

    entries = [
        _Entry(
            "紧急处置",
            ["gas", "燃气"],
            "燃气总阀在一楼厨房门后，请关闭后到院子等候。",
            "Close the gas valve.",
        ),
        _Entry("停车", ["parking"], "无关", "unrelated"),
    ]

    zh = emergency_follow_up_reply("gas", Language.ZH, entries)
    en = emergency_follow_up_reply("gas", Language.EN, entries)
    assert "燃气总阀在一楼厨房门后" in zh
    assert "值班管家" in zh
    assert "Close the gas valve." in en
    assert not re.search(r"[一-鿿]", en)


def test_follow_up_reply_falls_back_to_the_category_template() -> None:
    """没有对应审核知识时回退为该类别的固定安全提示，中英两版。"""
    from homestay_bot.services.emergency_service import emergency_follow_up_reply

    assert "开窗通风" in emergency_follow_up_reply("gas", Language.ZH, [])
    assert "120" in emergency_follow_up_reply("medical", Language.ZH, [])
    assert "open the windows" in emergency_follow_up_reply("gas", Language.EN, [])
