from homestay_bot.logging import redact_log_fields


def test_log_filter_redacts_tokens_and_mobile_numbers() -> None:
    """日志输出不得暴露外部密钥和完整手机号。"""
    filtered = redact_log_fields(
        {
            "hostex_access_token": "secret-token",
            "mobile": "13800138000",
            "request_id": "RT-1",
        }
    )

    assert filtered["hostex_access_token"] == "[REDACTED]"
    assert filtered["mobile"] == "138****8000"
    assert filtered["request_id"] == "RT-1"


def test_log_filter_redacts_nested_secrets_and_tokens_in_text() -> None:
    """嵌套结构和普通文本中的已知密钥形式也必须脱敏。"""
    filtered = redact_log_fields(
        {
            "headers": {"Authorization": "Bearer abc-secret"},
            "message": "Hostex-Access-Token: token-value",
        }
    )

    assert filtered["headers"]["Authorization"] == "[REDACTED]"
    assert "token-value" not in filtered["message"]
