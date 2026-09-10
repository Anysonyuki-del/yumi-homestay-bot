import re
from dataclasses import dataclass

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
        """按危险类别返回固定安全提示，不同危险的第一步动作并不相同。

        此前只有 fire 有专属文案，gas、electric、medical、violence 全部退回同一句
        「请先确保自身安全，不要自行处理故障。」。生产验收时「房间里有煤气味」拿到
        的正是这句——分类器已判成 gas 并写进审计（emergency:gas），文案却没用上这个
        结果。对燃气泄漏而言它信息量不足，客人可能留在房间里等管家，而开关一次电灯
        就可能引爆。

        文案必须命中 guest_reply_policy 的高危安全句白名单，否则会被当成非安全句
        过滤掉、只剩固定收尾，而且不会有任何报错。白名单里早已预留「开窗通风」
        「切断电源」「拨打119/110/120」等词，说明按类别给指令本就是既定设计。

        access（进不去、被锁在门外）不涉及人身危险，继续使用通用文案。
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
