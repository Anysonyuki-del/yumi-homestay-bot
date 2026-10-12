import re
from collections.abc import Sequence
from dataclasses import dataclass

from homestay_bot.domain.enums import Language
from homestay_bot.services.answer_policy import is_policy_inquiry
from homestay_bot.services.guest_reply_policy import prepare_guest_reply


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
        r"平台介入|找平台|举报|媒体曝光|曝光|投诉到|差评|平台投诉|投诉",
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
        refund = cls._refund.search(text) is not None and not is_policy_inquiry(text)
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

    # 业务优先级只在这里定义一处（修复 Spec §2.2，F4）：原因按退款 > 平台投诉 > 情绪词，
    # 风险按 critical > high。
    _REASON_ORDER = ("refund", "complaint", "agitated")
    _RISK_ORDER = ("critical", "high", "normal")

    @classmethod
    def classify_all(cls, texts: Sequence[str]) -> ComplaintClassification:
        """对全部规则文本变体分类后按业务优先级合并（修复 Spec §2.2，F4）。

        - 原因：退款 > 平台投诉 > 情绪词；只有全部命中都是情绪词时才是 agitated；
        - 风险：取全部硬理由结果中最高的一档（critical > high）；
        - 退款或赔偿标记：任一变体为真即为真；
        - 没有命中时返回 is_complaint=False，与 `classify` 的返回契约一致。
        此前取第一个非情绪词变体：「我要退款，投\n诉到平台」只记 refund/high，「我要退\n款，
        不然投诉到平台」丢了退款标记。
        """
        hits = [result for result in (cls.classify(text) for text in texts) if result.is_complaint]
        if not hits:
            return ComplaintClassification(False)
        hard = [result for result in hits if result.reason != "agitated"]
        if not hard:
            return ComplaintClassification(True, "agitated", "high", False)
        reason = min(
            (str(result.reason) for result in hard), key=cls._REASON_ORDER.index
        )
        risk = min((result.risk_level for result in hard), key=cls._RISK_ORDER.index)
        return ComplaintClassification(
            True, reason, risk, any(result.refund_or_compensation for result in hits)
        )

    @staticmethod
    def guest_acknowledgement(language: Language = Language.ZH) -> str:
        """返回客诉模式唯一中立话术，不包含道歉、责任或结果承诺。"""
        return prepare_guest_reply(
            "",
            language=language,
            requires_human=True,
            high_risk=True,
        )
