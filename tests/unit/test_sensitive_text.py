"""共用文本净化（修复 Spec 2026-10-08 fix2 §2.6(0)，V7-R2）：检测与净化同一组模式。"""

import pytest

from homestay_bot.services.sensitive_text import (
    contains_sensitive_text,
    redact_credentials,
    sanitize_referral_text,
)


@pytest.mark.parametrize(
    "text",
    [
        "Door lock password: Ab12_X",
        "wifi password=abcd1234",
        "WIFI PASSWORD is Qq998877",
        "Wi-Fi password: A!b2C#d4",
        "门锁密码：1#2#3#4",
        "room passcode 4455",
        "PIN code: 8899",
        "Verification code: Q9r8",
        "OTP 123456",
        "门锁密码是 Ab12_X",
        "验证码：8812",
        "WiFi密码是abc12345",
        "口令 8848",
        "二维码 https://qr.example/abc",
        "联系我 13812345678",
    ],
)
def test_credentials_are_detected_and_removed(text: str) -> None:
    """中英文凭据（冒号、等号、空格、is 分隔，大小写不敏感）被检测并从净化结果中去掉。"""
    assert contains_sensitive_text(text)
    sanitized = sanitize_referral_text(text)
    for secret in ("Ab12_X", "abcd1234", "Qq998877", "4455", "8899", "Q9r8", "123456",
                   "8812", "abc12345", "8848", "qr.example", "13812345678"):
        if secret in text:
            assert secret not in sanitized, (text, sanitized)


@pytest.mark.parametrize(
    "text",
    [
        "What's the Wi-Fi password?",
        "How do I get the Wi-Fi password?",
        "No password is needed for the gym",
        "The wifi password is changed daily",
        "password is sunshine",
        "密码是多少",
        "密码?",
        "口令是什么",
        "验证码怎么获取",
        "如何获取 Wi-Fi 密码",
        "401房间能看到江景吗",
        "健身房几点开门",
        "早餐 38 元一位",
        "黄鹤楼离这里多远",
    ],
)
def test_questions_without_values_and_ordinary_facts_are_untouched(text: str) -> None:
    """不含凭据值的提问、房号、正常金额、公共地点不被误判、不被改写。"""
    assert not contains_sensitive_text(text)
    assert sanitize_referral_text(text) == text


def test_complaint_redaction_keeps_existing_formats() -> None:
    """客诉复核共用的凭据规则：已有中文格式结果不变，英文凭据现在也会被遮。"""
    assert redact_credentials("门锁密码是 Ab12_X") == "[门锁密码已过滤]"
    assert redact_credentials("验证码：8812") == "[验证码已过滤]"
    assert redact_credentials("详细地址：武汉市洪山区珞喻路100号") == "[详细地址已脱敏]"
    assert "Ab12_X" not in redact_credentials("Door lock password: Ab12_X")


@pytest.mark.parametrize(
    "text",
    [
        'Wi-Fi password: "A1! SYNTHETIC_VALUE_TAIL" 后续说明',
        "room passcode: 'A1! SYNTHETIC_VALUE_TAIL' 后续说明",
        r'password="A1!\"SYNTHETIC_VALUE_TAIL" 后续说明',
        r"access code: 'A1!\'SYNTHETIC_VALUE_TAIL' 后续说明",
        '验证码："A1! SYNTHETIC_VALUE_TAIL',
        "口令：'A1! SYNTHETIC_VALUE_TAIL",
        "门锁密码：A1!#SYNTHETIC_VALUE_TAIL",
        "WiFi密码是 A1!#SYNTHETIC_VALUE_TAIL",
        "密码为 A1!#SYNTHETIC_VALUE_TAIL",
        "password is A1!#SYNTHETIC_VALUE_TAIL",
        "security code：SYNTHETIC_VALUE_TAIL",
        "PIN=A1!#SYNTHETIC_VALUE_TAIL",
        "door password: 13812345678#SYNTHETIC_VALUE_TAIL",
    ],
)
def test_credential_values_are_removed_through_the_shared_complaint_boundary(text: str) -> None:
    """引号、空格、转义与标点后的秘密尾部不能通过客诉或转交的共用边界。"""
    from homestay_bot.services.complaint_review_job import SQLAlchemyComplaintMessageContext

    assert contains_sensitive_text(text)
    for redacted in (
        redact_credentials(text), sanitize_referral_text(text),
        SQLAlchemyComplaintMessageContext._sanitize(text),
    ):
        assert "SYNTHETIC_VALUE_TAIL" not in redacted
        assert "A1!" not in redacted
        if "后续说明" in text:
            assert "后续说明" in redacted


def test_unclosed_credential_quote_stops_at_line_end() -> None:
    """不闭合的凭据只保守遮盖当前行，下一行的正常说明保留。"""
    text = 'password: "A1! SYNTHETIC_VALUE_TAIL\n下一行正常说明'
    assert redact_credentials(text) == "[credential removed]\n下一行正常说明"


@pytest.mark.parametrize("text", [
    "No password is needed.", "The wifi password is changed!", "No PIN is required?",
])
def test_english_credential_status_with_sentence_punctuation_is_untouched(text: str) -> None:
    """句末标点不能把 needed/changed/required 的状态说明变成真实凭据赋值。"""
    assert redact_credentials(text) == text
    assert not contains_sensitive_text(text)
    assert redact_credentials("password: needed.") == "[credential removed]"
    assert redact_credentials("password is 'needed.'") == "[credential removed]"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("门锁密码：1#", "[门锁密码已过滤]"),
        ("验证码：7", "[验证码已过滤]"),
        ("密码是a", "[口令已过滤]"),
        ("口令为 !", "[口令已过滤]"),
    ],
)
def test_short_chinese_values_with_assignment_markers_are_credentials(
    text: str, expected: str,
) -> None:
    """中文明确赋值没有四位下限，短数字与标点同样属于凭据。"""
    assert contains_sensitive_text(text)
    assert redact_credentials(text) == expected
