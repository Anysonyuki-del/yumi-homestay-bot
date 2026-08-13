import re
from dataclasses import dataclass

from homestay_bot.domain.enums import Language
from homestay_bot.services.guest_reply_policy import human_contact_reply


@dataclass(frozen=True)
class ComplaintClassification:
    """表示本地规则识别出的客诉风险，不包含客人原文。"""

    is_complaint: bool
    reason: str | None = None
    risk_level: str = "normal"
    refund_or_compensation: bool = False


class ComplaintService:
    """用确定性规则识别客诉并提供固定安抚文案。"""

    _refund = re.compile(r"退款|退钱|退费|赔偿|赔钱|补偿|refund|compensation", re.IGNORECASE)
    _platform = re.compile(
        r"平台|介入|举报|媒体|曝光|投诉到|差评|平台投诉|投诉",
        re.IGNORECASE,
    )
    _agitated = re.compile(
        r"太离谱|气死|愤怒|必须马上|立刻解决|受够了|不接受|欺骗|"
        r"ridiculous|furious|unacceptable|!!!|！！！",
        re.IGNORECASE,
    )

    @classmethod
    def classify(cls, text: str) -> ComplaintClassification:
        """按高风险优先级识别客诉类型。"""
        refund = cls._refund.search(text) is not None
        platform = cls._platform.search(text) is not None
        agitated = cls._agitated.search(text) is not None
        if refund and platform:
            return ComplaintClassification(True, "refund", "critical", True)
        if refund:
            return ComplaintClassification(True, "refund", "high", True)
        if platform:
            return ComplaintClassification(True, "complaint", "critical", False)
        if agitated:
            return ComplaintClassification(True, "agitated", "high", False)
        return ComplaintClassification(False)

    @staticmethod
    def guest_acknowledgement() -> str:
        """返回客诉模式唯一固定安抚，不包含金额或责任承诺。"""
        return f"我已收到您的诉求。{human_contact_reply(Language.ZH)}"
