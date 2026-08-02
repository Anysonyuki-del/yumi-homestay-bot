import logging
from io import StringIO

from homestay_bot.logging import (
    SensitiveDataFilter,
    configure_logging_redaction,
    redact_log_fields,
)


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


def test_access_log_filter_redacts_oauth_and_callback_query_values() -> None:
    """Uvicorn 访问日志不得记录 OAuth code 或企业微信签名。"""
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        (
            "127.0.0.1",
            "GET",
            "/employee/oauth/callback?code=secret-code&state=secret-state",
            "1.1",
            200,
        ),
        None,
    )

    SensitiveDataFilter().filter(record)
    rendered = record.getMessage()

    assert "secret-code" not in rendered
    assert "secret-state" not in rendered


def test_configure_logging_redaction_protects_child_logger_records() -> None:
    """子 logger 传播到父 handler 时也必须经过脱敏过滤器。"""
    parent = logging.getLogger("homestay_bot")
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    parent.addHandler(handler)
    original_propagate = parent.propagate
    parent.propagate = False
    try:
        configure_logging_redaction()
        child = logging.getLogger("homestay_bot.security_test")
        child.warning("Hostex-Access-Token: %s", "secret-token")
        assert "secret-token" not in stream.getvalue()
    finally:
        parent.removeHandler(handler)
        handler.close()
        parent.propagate = original_propagate


def test_log_filter_redacts_mapping_message_and_extra_fields() -> None:
    """字典消息和 logger.extra 中的敏感字段也不得绕过过滤器。"""
    record = logging.LogRecord(
        "homestay_bot.test",
        logging.INFO,
        __file__,
        1,
        {"token": "token-value", "nested": {"phone": "13800138000"}},
        (),
        None,
    )
    record.secret = "secret-value"
    record.guest_phone = "13900139000"

    assert SensitiveDataFilter().filter(record) is True
    assert record.msg["token"] == "[REDACTED]"
    assert record.msg["nested"]["phone"] == "138****8000"
    assert record.secret == "[REDACTED]"
    assert record.guest_phone == "139****9000"
