import re

from homestay_bot.domain.enums import Language

_ZH_HUMAN_CONTACT_REPLY = "我会立即联系管家来处理，请您稍等。"
_EN_HUMAN_CONTACT_REPLY = (
    "I’ll contact our on-duty host immediately. Please wait a moment."
)
_ZH_HIGH_RISK_ACKNOWLEDGEMENT = "您的情况我已记录。"
_ZH_HIGH_RISK_HANDOFF = "我会立即联系值班管家跟进处理，请保持联系方式畅通。"
_EN_HIGH_RISK_ACKNOWLEDGEMENT = "I’ve recorded the situation."
_EN_HIGH_RISK_HANDOFF = (
    "I’ll contact the on-duty host immediately to follow up. "
    "Please keep your phone available."
)

_ZH_WEATHER_PATTERN = re.compile(r"天气|气温|温度|下雨|降雨|阵雨|雷雨|晴天")
_EN_WEATHER_PATTERN = re.compile(
    r"\b(?:weather|temperature|rain|storm|sunny|forecast)\b",
    re.IGNORECASE,
)

# 这些模式只处理客人可见的、尚未由人工确认的执行结果承诺。
# “请立即离开房间”“拨打 119”等安全指令不在匹配范围内。
_UNSAFE_COMMITMENT_PATTERNS = (
    re.compile(
        r"(?:已经|已|马上|立即|尽快|稍后|这就|会).{0,18}"
        r"(?:安排|派|叫|通知|联系|上门|送|补|维修|修理|处理|解决|回复|"
        r"反馈|完成|查清|核实|确认|跟进|协助)"
    ),
    re.compile(
        r"(?:师傅|工作人员|员工|管家).{0,18}"
        r"(?:会|马上|立即|尽快|稍后|一定|保证).{0,18}"
        r"(?:上门|处理|解决|维修|查看|送|补|联系|完成)"
    ),
    re.compile(
        r"(?:一定|保证|肯定|彻底).{0,18}"
        r"(?:解决|处理|修好|送到|安排|完成|恢复)"
    ),
    re.compile(
        r"(?:arranged|technician\s+will|staff\s+will|will\s+(?:come|arrive|fix|"
        r"resolve|send|deliver|handle)|guarantee|make\s+sure\s+it\s+is\s+fixed)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:师傅|工作人员|员工|管家|维修人员).{0,12}(?:在路上|快到了|很快到)"),
    re.compile(
        r"(?:师傅|工作人员|员工|管家|维修人员).{0,12}"
        r"(?:正在赶来|正往.{0,6}赶|随后到|马上到|很快到|过来|上门)"
    ),
    re.compile(r"(?:今天|今晚|明天|稍后|马上|很快).{0,12}(?:修好|送到|处理好|解决好)"),
    re.compile(r"正在.{0,8}(?:安排|派|通知|联系).{0,12}(?:师傅|人员|员工|管家)"),
    re.compile(
        r"(?:technician|staff|host|someone).{0,18}"
        r"(?:is|are|'s)?\s*(?:on the way|coming|arriving|being sent)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:we(?:'re| are)|i(?:'m| am))\s+sending\s+someone", re.IGNORECASE),
)

_ZH_SAFE_HUMAN_SENTENCE = re.compile(
    r"(?:已收到|收到您的|记下|记录|抱歉|对不起|请先|请立即|不要|避免|"
    r"长按|按住|拔下|拔掉|断开|关闭|停止使用|离开房间|前往安全|拨打\s*119|"
    r"保持在安全区域|远离明火|开窗通风|切断燃气|切断电源|不要触碰|保持距离|"
    r"呼叫急救|尝试.{0,12}(?:按|关闭|重启|断电))"
)
_EN_SAFE_HUMAN_SENTENCE = re.compile(
    r"(?:thanks for letting us know|sorry|please\s+(?:leave|move|call|unplug|"
    r"disconnect|turn off|stop|avoid|press|hold|try))",
    re.IGNORECASE,
)
_ZH_HIGH_RISK_SAFETY_SENTENCE = re.compile(
    r"(?:请立即|请先|不要|避免|停止使用|离开|撤离|前往安全|"
    r"拨打\s*(?:119|110|120)|远离明火|开窗通风|切断燃气|切断电源|"
    r"不要触碰|保持距离|呼叫急救)"
)
_EN_HIGH_RISK_SAFETY_SENTENCE = re.compile(
    r"(?:please\s+(?:leave|move|call|unplug|disconnect|turn off|stop|avoid)|"
    r"call\s+(?:the\s+)?(?:police|fire department|emergency services)|"
    r"stay away|do not touch)",
    re.IGNORECASE,
)


def human_contact_reply(language: Language) -> str:
    """返回无需人工确认执行结果的统一管家联系话术。"""
    if language is Language.EN:
        return _EN_HUMAN_CONTACT_REPLY
    return _ZH_HUMAN_CONTACT_REPLY


def _contains_unsafe_commitment(sentence: str) -> bool:
    """判断单句是否声称尚未确认的人员调度或处理结果。"""
    return any(pattern.search(sentence) for pattern in _UNSAFE_COMMITMENT_PATTERNS)


def _safe_sentences(content: str) -> list[str]:
    """按句删除承诺，同时保留撤离提示和低风险自助建议。"""
    sentences = re.findall(r"[^。！？；;.!?]+[。！？；;.!?]*", content)
    return [
        sentence.strip()
        for sentence in sentences
        if sentence.strip() and not _contains_unsafe_commitment(sentence)
    ]


def _safe_human_sentences(content: str, language: Language) -> list[str]:
    """人工场景只保留中性确认、歉意和明确的低风险安全指令。"""
    safe_pattern = (
        _EN_SAFE_HUMAN_SENTENCE if language is Language.EN else _ZH_SAFE_HUMAN_SENTENCE
    )
    return [
        sentence
        for sentence in _safe_sentences(content)
        if safe_pattern.search(sentence)
    ]


def _high_risk_reply(content: str, language: Language) -> str:
    """生成不含道歉、责任判断或结果承诺的高危转人工回复。"""
    if language is Language.EN:
        acknowledgement = _EN_HIGH_RISK_ACKNOWLEDGEMENT
        handoff = _EN_HIGH_RISK_HANDOFF
        safe_pattern = _EN_HIGH_RISK_SAFETY_SENTENCE
        separator = " "
    else:
        acknowledgement = _ZH_HIGH_RISK_ACKNOWLEDGEMENT
        handoff = _ZH_HIGH_RISK_HANDOFF
        safe_pattern = _ZH_HIGH_RISK_SAFETY_SENTENCE
        separator = ""

    # 固定确认和收尾可能已经由上游生成；先删除再提取安全动作，保证幂等。
    content_without_fixed_text = content.strip()
    for fixed_text in (acknowledgement, handoff):
        content_without_fixed_text = content_without_fixed_text.replace(fixed_text, "")
    safety_sentences: list[str] = []
    for sentence in _safe_sentences(content_without_fixed_text):
        if not safe_pattern.search(sentence):
            continue
        # 安全动作可能与道歉或责任判断写在同一句；仅删除态度片段，保留动作。
        if language is Language.EN:
            sentence = re.sub(
                r"\b(?:sorry|we apologize)(?:\s+for[^,.!?]*)?[,.:;!?]*\s*",
                "",
                sentence,
                flags=re.IGNORECASE,
            )
        else:
            sentence = re.sub(
                r"(?:真的|非常|十分|很)?(?:抱歉|对不起)(?:给您[^，。！？]*)?[，。！？]*",
                "",
                sentence,
            )
            sentence = re.sub(
                r"(?:这|此事)?是(?:我们|民宿|店里)的责任[，。！？]*",
                "",
                sentence,
            )
        if sentence.strip():
            safety_sentences.append(sentence.strip())
    safety_text = "".join(safety_sentences).strip()
    prefix = f"{safety_text}{separator}" if safety_text else ""
    return f"{prefix}{acknowledgement}{separator}{handoff}"


def _is_weather_question(question: str, language: Language) -> bool:
    """判断当前问题是否明确询问天气，避免给其他回复误加天气开场。"""
    pattern = _EN_WEATHER_PATTERN if language is Language.EN else _ZH_WEATHER_PATTERN
    return pattern.search(question) is not None


def _warm_weather_reply(content: str, language: Language) -> str:
    """为已取得的天气事实增加简短管家表达，不改写任何查询字段。"""
    if language is Language.EN:
        opener = "I checked the forecast for you. "
        if not content.startswith(opener):
            content = f"{opener}{content}"
        if re.search(r"\b(?:rain|shower|storm)\b", content, re.IGNORECASE) and not re.search(
            r"umbrella", content, re.IGNORECASE
        ):
            content = f"{content.rstrip()} It’s a good idea to bring an umbrella."
        return content

    opener = "我帮您看了一下，"
    if not content.startswith(opener):
        content = f"{opener}{content}"
    if re.search(r"下雨|降雨|阵雨|雷雨", content) and "带伞" not in content:
        content = f"{content.rstrip()}出门记得带伞。"
    return content


def prepare_guest_reply(
    content: str,
    *,
    language: Language,
    requires_human: bool,
    question: str = "",
    high_risk: bool = False,
) -> str:
    """生成唯一客人可见正文，并统一风格、承诺过滤和高危边界。"""
    if requires_human and high_risk:
        return _high_risk_reply(content, language)

    prepared = sanitize_guest_reply(
        content,
        language=language,
        requires_human=requires_human,
    )
    if not requires_human and _is_weather_question(question, language):
        return _warm_weather_reply(prepared, language)
    return prepared


def sanitize_guest_reply(
    content: str,
    *,
    language: Language,
    requires_human: bool,
) -> str:
    """清除客人侧执行承诺，并在需要人工时追加唯一管家收尾。"""
    if requires_human:
        handoff = human_contact_reply(language)
        # 同一文本可能依次经过模型适配器和会话出口；先移除既有固定收尾，
        # 再统一过滤并追加，保证重复清洗不改变正文或误删前置歉意。
        content_without_handoff = content.strip()
        if content_without_handoff.endswith(handoff):
            content_without_handoff = content_without_handoff[: -len(handoff)].rstrip()
        safe_content = "".join(
            _safe_human_sentences(content_without_handoff, language)
        ).strip()
        if not safe_content:
            acknowledgement = (
                "Thanks for letting us know."
                if language is Language.EN
                else "我已收到您的诉求。"
            )
            separator = " " if language is Language.EN else ""
            return f"{acknowledgement}{separator}{handoff}"
        separator = " " if language is Language.EN else ""
        return f"{safe_content}{separator}{handoff}"
    safe_content = "".join(_safe_sentences(content)).strip()
    if safe_content:
        return safe_content
    if language is Language.EN:
        return "I’m unable to confirm that information right now."
    return "这项信息暂时无法确认。"
