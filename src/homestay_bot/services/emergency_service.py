import re
from dataclasses import dataclass

from homestay_bot.domain.enums import Language
from homestay_bot.services.guest_reply_policy import human_contact_reply


@dataclass(frozen=True)
class EmergencyClassification:
    """表示确定性紧急规则的分类结果。"""

    is_emergency: bool
    category: str | None = None


class EmergencyService:
    """用确定性中英文规则识别住宿安全紧急事件。"""

    _patterns: tuple[tuple[str, re.Pattern[str]], ...] = (
        (
            "fire",
            re.compile(r"着火|起火|火灾|浓烟|\bfire\b|smoke", re.IGNORECASE),
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
        """返回固定安全提示；火灾等生命危险明确提示联系公共急救服务。"""
        if language is Language.EN:
            if emergency.category == "fire":
                return (
                    "Please leave the room and move to a safe place immediately. "
                    "Call 119 if there is fire or smoke. "
                    f"{human_contact_reply(language)}"
                )
            return (
                "Please move to a safe place and avoid handling the fault yourself. "
                f"{human_contact_reply(language)}"
            )
        if emergency.category == "fire":
            return (
                "请立即离开房间并前往安全区域，如有明火或浓烟请拨打119。"
                f"{human_contact_reply(language)}"
            )
        return f"请先确保自身安全，不要自行处理故障。{human_contact_reply(language)}"
