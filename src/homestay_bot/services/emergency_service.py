import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from homestay_bot.domain.enums import Language
from homestay_bot.services.guest_reply_policy import prepare_guest_reply


@dataclass(frozen=True)
class EmergencyClassification:
    """表示确定性紧急规则的分类结果。"""

    is_emergency: bool
    category: str | None = None
    is_possible: bool = False


_ZH_GENERIC_SAFETY_TEXT = "请先确保自身安全，不要自行处理故障。"
# 每类危险的第一步动作不同。措辞需同时满足两点：给出客人能立即执行的动作，并且
# 命中 guest_reply_policy 的高危安全句白名单（「开窗通风」「切断电源」「拨打119/
# 110/120」等词白名单里早已预留）。
_ZH_SAFETY_TEXTS = {
    "fire": "请立即离开房间并前往安全区域，如有明火或浓烟请拨打119。",
    "gas": ("请立即开窗通风并离开房间，不要开关电器、不要使用明火，到室外安全处后再联系我们。"),
    "electric": (
        "请不要触碰漏电部位和潮湿处的电器，如能安全操作请切断电源；有人触电请立即拨打120。"
    ),
    "medical": "如有生命危险请立即拨打120，急救到达前不要随意搬动伤者。",
    "violence": "如人身安全受到威胁，请立即拨打110报警并前往安全地点。",
}
_EN_GENERIC_SAFETY_TEXT = "Please move to a safe place and avoid handling the fault yourself."
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


# 豁免作用范围用到的固定句式（2026-10-07 紧急豁免范围修复）。只用来限定豁免覆盖的文字区间，
# 不扩大危险识别：识别不出边界时一律不豁免。
_CLAUSE_SEPARATOR = re.compile(r"，|\bbut\b|但是|但现在", re.IGNORECASE)
_INQUIRY_MARKER = re.compile(r"吗|么|哪|什么|还是|有没有|是不是|是否")
_GAS_LIVE_SIGNAL = re.compile(r"味|漏|闻|泄|嘶")
_ALARM_DEVICE = re.compile(
    r"smoke\s+(?:alarm|detector)|fire\s+extinguisher|(?:烟雾)?报警器|灭火器", re.IGNORECASE
)
_LOCATION_QUESTION = re.compile(r"在哪|哪里|位置|\bwhere\b", re.IGNORECASE)
_DEVICE_LIVE_SIGNAL = re.compile(r"响|叫|正在|going off|beeping|sounding", re.IGNORECASE)
_SAFETY_RULE_SUFFIX = re.compile(
    r"\s*的?\s*(?:安全规定|安全政策|规定|政策|safety\s+(?:polic(?:y|ies)|rules?)"
    r"|polic(?:y|ies)|rules?)",
    re.IGNORECASE,
)
_HYPOTHETICAL_MARKER = re.compile(
    r"如果|假如|假设|万一|\bwhat if\b|\bin case of\b", re.IGNORECASE
)
_CONSEQUENCE_QUESTION = re.compile(
    r"怎么办|该怎么|怎么处理|应该|要不要|会怎样|what should|what do|how do|how should",
    re.IGNORECASE,
)
_QUOTE_MARKER = re.compile(r"(?:说明书|手册|政策|演练).*?(?:写|说|提|要求)")
_QUOTED_TEXT = re.compile(r"[‘“「\"'][^‘’“”「」\"']+[’”」\"']")
_MEANING_QUESTION = re.compile(r"是什么意思|什么意思|怎么理解|指什么")


class EmergencyService:
    """用确定性中英文规则识别住宿安全紧急事件。"""

    _patterns: tuple[tuple[str, re.Pattern[str]], ...] = (
        (
            "fire",
            re.compile(
                r"着火|起火|火灾|浓烟|冒烟|火花|焦味|烟雾报警器[^，。！？]{0,8}响|"
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
            re.compile(
                r"暴力|威胁|打我|袭击|敲门.{0,12}骂人|threaten|attack|violence", re.IGNORECASE
            ),
        ),
        (
            "medical",
            re.compile(
                r"昏迷|急救|呼吸困难|(?:没有|无|停止)呼吸(?!困难)|无法呼吸|喘不上气|叫不醒|头破.{0,5}流血|严重受伤|医疗急症|"
                r"medical emergency|unconscious|cannot breathe|not breathing|serious injury",
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

    @staticmethod
    def _segments(text: str) -> Iterator[tuple[str, bool, int, int]]:
        """把文本切成句子与分句，返回（句子、句末是否问号、分句起点、分句终点）。

        句子按句末标点切，分句再按中文逗号与转折词切，与旧版的分句口径一致；
        额外保留句子与句末问号，供假设、引用这类需要看到整句边界的豁免使用。
        """
        position = 0
        for found in re.finditer(r"[。！？；\n.!?;]|$", text):
            sentence = text[position : found.start()]
            is_question = found.group() in {"？", "?"}
            position = found.end()
            if not sentence:
                continue
            clause_start = 0
            for separator in _CLAUSE_SEPARATOR.finditer(sentence):
                yield sentence, is_question, clause_start, separator.start()
                clause_start = separator.end()
            yield sentence, is_question, clause_start, len(sentence)

    @staticmethod
    def _noncurrent_mention(
        sentence: str,
        is_question: bool,
        clause_start: int,
        clause_end: int,
        start: int,
        end: int,
    ) -> bool:
        """只豁免能明确识别为非现场的那一段文字里的危险命中。

        每条豁免都要求命中落在它自己描述的文字区间内：设备咨询只管「燃气灶」这个词，
        报警器位置咨询只管设备名本身，假设只管条件结束边界之前的内容，引用只管引号或
        释义询问词之前的内容。同一句里其他正在发生的危险照常判定；找不到可靠边界时
        不豁免（宁可多报）。见 docs/specs/2026-10-07_emergency-exemption-scope-spec.md。
        """
        clause = sentence[clause_start:clause_end]
        hit = sentence[start:end]
        # 否定要求标记紧贴命中，作用范围本来就只覆盖这一处；「不知道是否漏电」仍按可能危险处置。
        if re.search(
            r"(?:没有|并无|无|未)(?:发生|出现|人|任何)?$|"
            r"\b(?:no|not|without)\s+(?:\w+\s+or\s+)?$",
            sentence[clause_start:start],
            re.I,
        ):
            return True
        # 设备咨询：只豁免「燃气灶」「煤气炉」里的燃气词，且整句在问、没有燃气现场信号。
        if (
            re.fullmatch(r"燃气|煤气|天然气", hit)
            and sentence[end : end + 1] in {"灶", "炉"}
            and (is_question or _INQUIRY_MARKER.search(clause))
            and not _GAS_LIVE_SIGNAL.search(clause)
        ):
            return True
        # 报警器、灭火器位置咨询：命中必须落在设备名之内，正在响等现场信号不豁免。
        for device in _ALARM_DEVICE.finditer(clause):
            if (
                clause_start + device.start() <= start
                and end <= clause_start + device.end()
                and _LOCATION_QUESTION.search(clause)
                and not _DEVICE_LIVE_SIGNAL.search(clause)
            ):
                return True
        # 安全规定或政策咨询：危险词直接修饰「安全规定、政策」，
        # 如「火灾安全规定」「fire safety policy」。
        if _SAFETY_RULE_SUFFIX.match(sentence, end):
            return True
        # 假设：条件从标记开始，到后果询问词或句末问号为止；找不到结束边界不豁免。
        for marker in _HYPOTHETICAL_MARKER.finditer(sentence, 0, start):
            consequence = _CONSEQUENCE_QUESTION.search(sentence, marker.end())
            if consequence is not None:
                boundary = consequence.start()
            else:
                boundary = len(sentence) if is_question else -1
            if marker.end() <= start and end <= boundary:
                return True
        # 引用：只豁免引号内的内容；没有引号时到释义询问词为止，两者都没有不豁免。
        for marker in _QUOTE_MARKER.finditer(sentence, 0, start):
            quoted = [
                item
                for item in _QUOTED_TEXT.finditer(sentence, marker.end())
                if item.start() <= start and end <= item.end()
            ]
            if quoted:
                return True
            if not _QUOTED_TEXT.search(sentence, marker.end()):
                meaning = _MEANING_QUESTION.search(sentence, marker.end())
                if meaning and marker.end() <= start and end <= meaning.start():
                    return True
        return False

    def classify(self, text: str) -> EmergencyClassification:
        """按危险类别优先检查分句；模型故障不能关闭明确现场危险门。"""
        segments = list(self._segments(text))
        possible = None
        for category, pattern in self._patterns:
            for sentence, is_question, clause_start, clause_end in segments:
                clause = sentence[clause_start:clause_end]
                for match in pattern.finditer(sentence, clause_start, clause_end):
                    if not self._noncurrent_mention(
                        sentence,
                        is_question,
                        clause_start,
                        clause_end,
                        match.start(),
                        match.end(),
                    ):
                        if re.search(
                            r"有点|不确定|不知道|好像|是否|a little|not sure", clause, re.I
                        ) and not re.search(
                            r"手麻|触电|烫伤|叫不醒|喘不上气|煤气味|燃气味|gas smell", text
                        ):
                            possible = category
                            continue
                        return EmergencyClassification(True, category)
        if possible or re.search(
            r"烟味|刚才摔|头.{0,3}晕|smell.{0,5}smoke|feel dizzy|just fell", text, re.I
        ):
            return EmergencyClassification(False, possible or "medical", is_possible=True)
        return EmergencyClassification(False)

    def safety_reply(self, emergency: EmergencyClassification, language: Language) -> str:
        """按危险类别返回固定安全提示，非生命危险的 access 沿用通用文案。

        每句必须通过 guest_reply_policy 的安全过滤，避免关键处置指令被静默删除。
        """
        table = _EN_SAFETY_TEXTS if language is Language.EN else _ZH_SAFETY_TEXTS
        generic = _EN_GENERIC_SAFETY_TEXT if language is Language.EN else _ZH_GENERIC_SAFETY_TEXT
        safety_text = table.get(emergency.category or "", generic)
        return prepare_guest_reply(
            safety_text,
            language=language,
            requires_human=True,
            high_risk=True,
        )
