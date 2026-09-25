import re
from dataclasses import dataclass
from typing import Any

from homestay_bot.domain.enums import Language
from homestay_bot.services.guest_reply_policy import prepare_guest_reply


@dataclass(frozen=True)
class EmergencyClassification:
    """表示确定性紧急规则的分类结果。"""

    is_emergency: bool
    category: str | None = None


_ZH_GENERIC_SAFETY_TEXT = "请先确保自身安全，不要自行处理故障。"
# 每类危险的第一步动作不同。措辞需同时满足两点：给出客人能立即执行的动作，并且
# 命中 guest_reply_policy 的高危安全句白名单（「开窗通风」「切断电源」「拨打119/
# 110/120」等词白名单里早已预留）。
_ZH_SAFETY_TEXTS = {
    "fire": "请立即离开房间并前往安全区域，如有明火或浓烟请拨打119。",
    "gas": (
        "请立即开窗通风并离开房间，不要开关电器、不要使用明火，"
        "到室外安全处后再联系我们。"
    ),
    "electric": (
        "请不要触碰漏电部位和潮湿处的电器，如能安全操作请切断电源；"
        "有人触电请立即拨打120。"
    ),
    "medical": "如有生命危险请立即拨打120，急救到达前不要随意搬动伤者。",
    "violence": "如人身安全受到威胁，请立即拨打110报警并前往安全地点。",
}
_EN_GENERIC_SAFETY_TEXT = (
    "Please move to a safe place and avoid handling the fault yourself."
)
_EN_SAFETY_TEXTS = {
    "fire": (
        "Please leave the room and move to a safe place immediately. "
        "Call 119 if there is fire or smoke."
    ),
    # 不写成「…open flame; call us again once you are outside.」：分号是分句点，
    # 后半句不含安全动作会被白名单滤掉，只留下一个悬空的分号。
    "gas": (
        "Please leave the room immediately and open the windows on your way out. "
        "Do not switch any electrical device on or off and do not use an open flame."
    ),
    "electric": (
        "Please do not touch the wet or damaged electrical parts. "
        "Turn off the main power switch only if you can do so safely, "
        "and call emergency services if anyone has been shocked."
    ),
    "medical": (
        "Please call emergency services immediately if there is any risk to life, "
        "and do not move the injured person before help arrives."
    ),
    "violence": "Please move to a safe place and call the police immediately.",
}


# 紧急情况进行中的求助类后续：给固定处置答复，不交给模型、不联网（1.40.0）。
# 独立问题（早餐几点、附近药店）不在其中，照常回答（边界 Spec A07）。
_FOLLOW_UP_PATTERN = re.compile(
    r"怎么办|怎么处理|该怎么|怎么做|然后呢|接下来|要不要|需不需要|需要报警|报警吗|"
    r"叫救护车|还要做什么|还需要做|还能做什么|出来了|已经出来|到外面了|在外面了|"
    r"\bwhat\s+(?:should|do|can)\s+(?:i|we)\b|\bnow\s+what\b|\bshould\s+(?:i|we)\b|"
    r"\bwe(?:'re|\s+are)\s+out(?:side)?\b",
    re.IGNORECASE,
)
# 单独一句的确认也算后续；只做整句匹配，避免「好吃吗」这类句子被误认。
_ACKNOWLEDGEMENT_PATTERN = re.compile(
    r"^\s*(?:好的?|好吧|收到|知道了|明白了?|嗯+|行|ok(?:ay)?|got\s+it|thanks?)\s*[。.!！~～]*\s*$",
    re.IGNORECASE,
)
EMERGENCY_KNOWLEDGE_CATEGORY = "紧急处置"
_ZH_FOLLOW_UP_HANDOFF = "值班管家已收到通知，正在处理，请保持联系方式畅通。"
_EN_FOLLOW_UP_HANDOFF = (
    "Our on-duty host has been notified and is handling it. Please keep your phone available."
)


def is_emergency_follow_up(text: str) -> bool:
    """判断紧急情况进行中的一条消息是否为求助类后续（怎么办、然后呢、好的）。"""
    return bool(_FOLLOW_UP_PATTERN.search(text) or _ACKNOWLEDGEMENT_PATTERN.match(text))


def emergency_follow_up_reply(category: str, language: Language, entries: Any) -> str:
    """返回紧急情况后续的固定处置答复：优先审核知识，缺失时回退为该类别安全提示。

    审核知识取类别为「紧急处置」、关键词含危险类别代码（fire、gas 等）的已启用条目，
    原样使用，本店专属的位置信息由管家维护在知识里。会话服务与回归工具共用本函数。
    """
    for entry in entries or []:
        keywords = {str(item).strip().lower() for item in (getattr(entry, "keywords", None) or [])}
        if getattr(entry, "category", "") == EMERGENCY_KNOWLEDGE_CATEGORY and category in keywords:
            answer = str(
                getattr(entry, "answer_en" if language is Language.EN else "answer_zh", "") or ""
            ).strip()
            if answer:
                break
    else:
        answer = ""
    if not answer:
        table = _EN_SAFETY_TEXTS if language is Language.EN else _ZH_SAFETY_TEXTS
        answer = table.get(
            category,
            _EN_GENERIC_SAFETY_TEXT if language is Language.EN else _ZH_GENERIC_SAFETY_TEXT,
        )
    handoff = _EN_FOLLOW_UP_HANDOFF if language is Language.EN else _ZH_FOLLOW_UP_HANDOFF
    separator = " " if language is Language.EN else ""
    return f"{answer}{separator}{handoff}"


class EmergencyService:
    """用确定性中英文规则识别住宿安全紧急事件。"""

    _patterns: tuple[tuple[str, re.Pattern[str]], ...] = (
        (
            "fire",
            re.compile(
                r"着火|起火|火灾|浓烟|冒烟|火花|焦味|"
                r"\bfire\b|smoke|sparks?|burning\s+smell",
                re.IGNORECASE,
            ),
        ),
        (
            "gas",
            re.compile(r"燃气|煤气|天然气|gas (?:leak|smell)|smell gas", re.IGNORECASE),
        ),
        (
            "electric",
            re.compile(r"触电|漏电|电击|electric shock|electrocut", re.IGNORECASE),
        ),
        (
            "violence",
            re.compile(r"暴力|威胁|打我|袭击|threaten|attack|violence", re.IGNORECASE),
        ),
        (
            "medical",
            re.compile(
                r"昏迷|急救|呼吸困难|严重受伤|医疗急症|"
                r"medical emergency|unconscious|cannot breathe|serious injury",
                re.IGNORECASE,
            ),
        ),
        (
            "access",
            re.compile(
                r"无法入住|进不去|门锁.*(?:坏|故障)|被锁在门外|"
                r"cannot get in|can't get in|lock(?: is)? broken|locked out",
                re.IGNORECASE,
            ),
        ),
    )

    def classify(self, text: str) -> EmergencyClassification:
        """按高风险优先顺序匹配消息，不调用语言模型。"""
        for category, pattern in self._patterns:
            if pattern.search(text):
                return EmergencyClassification(True, category)
        return EmergencyClassification(False)

    def safety_reply(
        self, emergency: EmergencyClassification, language: Language
    ) -> str:
        """按危险类别返回固定安全提示，非生命危险的 access 沿用通用文案。

        每句必须通过 guest_reply_policy 的安全过滤，避免关键处置指令被静默删除。
        """
        table = _EN_SAFETY_TEXTS if language is Language.EN else _ZH_SAFETY_TEXTS
        generic = (
            _EN_GENERIC_SAFETY_TEXT
            if language is Language.EN
            else _ZH_GENERIC_SAFETY_TEXT
        )
        safety_text = table.get(emergency.category or "", generic)
        return prepare_guest_reply(
            safety_text,
            language=language,
            requires_human=True,
            high_risk=True,
        )
