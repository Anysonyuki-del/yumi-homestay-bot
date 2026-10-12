"""模型边界与转交记录共用的文本净化（修复 Spec 2026-10-08 fix2 §2.6(0)）。

客诉复核与知识缺口转交都在把客人或管家原文交给模型、落库或展示前调用同一组规则：
手机号、邮箱、长编号、精确地址、门锁密码、验证码、二维码、入住指南与链接。转交路径还要叠加
客人敏感信息（证件、订单号、门牌地址），判断是否含凭据时净化与检测用同一组
模式，不各写一套产生差异。

ponytail: 只识别可识别的格式，不是任意隐私识别；不把任意英文单词或纯数字当密码。
「如何获取 Wi-Fi 密码」这类不含凭据值的提问不受影响。新格式出现时补规则并补正反样本。
"""

import re

from homestay_bot.services.guest_reply_policy import (
    contains_sensitive_guest_text,
    redact_sensitive_guest_text,
)

# 优先消费成对引号（转义字符成组），未闭合时只遮到当前行；ASCII 值包含标点，
# 不能在 #、! 等处留下秘密尾部（fix3 v2 R1/F3-S1）。四位下限仅适用于英文无引号值。
_QUOTED_CREDENTIAL_VALUE = (
    r'''(?:"(?:\\[^\r\n]|[^"\\\r\n])*"|'(?:\\[^\r\n]|[^'\\\r\n])*'|'''
    r'''"(?:\\[^\r\n]|[^"\\\r\n])*\\?(?=\r?\n|$)|'''
    r"'(?:\\[^\r\n]|[^'\\\r\n])*\\?(?=\r?\n|$))"
)
_CREDENTIAL_VALUE = r"(?:" + _QUOTED_CREDENTIAL_VALUE + r'''|(?!["'])[\x21-\x7e]{4,})'''
# 无赋值标记的中文「密码?」仍是提问；显式冒号/是/为后的短值同样必须遮盖。
_CHINESE_CREDENTIAL_SUFFIX = (
    r"(?![ \t]*\?(?=[^\x21-\x7e]|$))[ \t]*(?:是|为)?[ \t]*[:：]?[ \t]*"
    r"(?:" + _QUOTED_CREDENTIAL_VALUE + r'''|(?!["'])[\x21-\x7e]+)'''
)


def _redact_english_credential(match: re.Match[str]) -> str:
    """英文 is 赋值只在值含数字或符号时遮盖，避免把 needed/changed 等陈述误作凭据。"""
    value = match["value"]
    # 无引号的常见状态说明带句末标点仍是说明；冒号或有引号的实际赋值照常遮盖。
    if match["separator"].strip().lower() == "is" and re.fullmatch(
        r"(?:needed|changed|required)[.!?]+", value, re.IGNORECASE,
    ):
        return match[0]
    if value.startswith(("'", '"')):
        value = value[1:].removesuffix(value[0])
    # ponytail: is 连接的纯字母密码（sunshine）漏识别；新需求若要求覆盖，应重新确认
    # 消歧契约，不能牺牲 password is needed/changed 的正常陈述（D-H3）。
    if match["separator"].strip().lower() == "is" and not re.search(r"[^A-Za-z\s]", value):
        return match[0]
    return "[credential removed]"


# 这些规则在文本越过模型或存储边界前执行，避免凭证和可定位住址外泄。
_REDACTION_RULES = (
    (
        re.compile(
            r"(?:详细地址|地址)\s*[:：]?\s*"
            r"(?:湖北省)?武汉市?"
            r"(?:洪山区|武昌区|青山区|汉阳区|江汉区|江岸区|硚口区|"
            r"蔡甸区|东西湖区|黄陂区|新洲区|江夏区)"
            r"[^，。；;\n]{0,60}(?:路|街|大道|小区|号|栋|室)"
            r"[^，。；;\n]{0,20}"
        ),
        "[详细地址已脱敏]",
    ),
    (
        re.compile(
            r"(?:门锁|房门|开门)[ \t]*密码" + _CHINESE_CREDENTIAL_SUFFIX
        ),
        "[门锁密码已过滤]",
    ),
    (
        re.compile(
            r"验证码" + _CHINESE_CREDENTIAL_SUFFIX
        ),
        "[验证码已过滤]",
    ),
    (
        # 通用中文密码也属于客诉模型边界；不可只在转交路径额外处理。
        re.compile(
            r"(?:(?:wi-?fi)[ \t]*)?(?:密码|口令)" + _CHINESE_CREDENTIAL_SUFFIX,
            re.IGNORECASE,
        ),
        "[口令已过滤]",
    ),
    (
        re.compile(
            r"(?:二维码|QR\s*code)\s*[:：]?\s*"
            r"(?:https?://\S+|[A-Za-z0-9+/=_-]{4,})",
            re.IGNORECASE,
        ),
        "[二维码已过滤]",
    ),
    (
        re.compile(
            r"(?:入住指南|入住说明|入住凭证|开门指南)\s*[:：]?"
            r"[^\n。；;!?！？]*[。；;!?！？]?"
        ),
        "[入住凭证内容已过滤]",
    ),
    # 英文标签共用同一值边界；保留 room passcode 4455、OTP 123456 的空格赋值写法。
    (
        re.compile(
            r"\b(?:(?:(?:door|lock|room|wi-?fi)[ \t]*)?"
            r"(?:pass(?:word|code)|pin(?:[ \t]*code)?)|"
            r"(?:verification|security|access)[ \t]*code|otp)\b[ \t]*"
            r"(?P<separator>is\b[ \t]*|[=:：][ \t]*|[ \t]+)"
            r"(?P<value>" + _CREDENTIAL_VALUE + r")",
            re.IGNORECASE,
        ),
        _redact_english_credential,
    ),
    (
        re.compile(r"https?://\S+", re.IGNORECASE),
        "[链接已过滤]",
    ),
)


def redact_credentials(content: str) -> str:
    """在模型边界遮盖手机号、邮箱、长编号、凭证、地址和链接（客诉复核与转交共用）。"""
    # 先遮盖完整凭据，避免其中的长数字先变成中文占位符，截断 ASCII 值后留下尾部。
    for pattern, replacement in _REDACTION_RULES:
        content = pattern.sub(replacement, content)
    content = re.sub(r"(?<!\d)\d{11}(?!\d)", "[手机号已脱敏]", content)
    content = re.sub(
        r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",
        "[邮箱已脱敏]",
        content,
        flags=re.IGNORECASE,
    )
    content = re.sub(r"(?<!\d)\d{12,}(?!\d)", "[编号已脱敏]", content)
    return content


def sanitize_referral_text(content: str) -> str:
    """转交记录、卡片与草稿模型输入的净化：共用凭据规则加客人敏感信息。"""
    return redact_sensitive_guest_text(redact_credentials(content))


def contains_sensitive_text(content: str) -> bool:
    """文本是否含本地可识别的敏感内容：净化前后不同即视为含有。"""
    return sanitize_referral_text(content) != content or contains_sensitive_guest_text(content)
