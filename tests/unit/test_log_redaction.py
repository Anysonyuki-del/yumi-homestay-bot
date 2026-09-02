import logging
import sys
from io import StringIO

from uvicorn.logging import AccessFormatter

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


def test_access_log_filter_preserves_uvicorn_structured_arguments() -> None:
    """异常查询串脱敏后仍须保留 Uvicorn 格式化器需要的五元参数。"""
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        (
            "203.0.113.5:1234",
            "GET",
            (
                "/wp-admin/install.php?step=1+HTTP%2F1.1%22+404"
                "&token=secret-token"
            ),
            "1.1",
            404,
        ),
        None,
    )

    SensitiveDataFilter().filter(record)
    output = AccessFormatter(
        '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'
    ).format(record)

    assert len(record.args) == 5
    assert "secret-token" not in output
    assert "token=%5BREDACTED%5D" in output
    assert "/wp-admin/install.php" in output


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


def _record_with_exception(message: str) -> logging.LogRecord:
    """构造一条携带真实异常信息的日志记录。"""
    try:
        raise RuntimeError(message)
    except RuntimeError:
        exc_info = sys.exc_info()
    return logging.LogRecord(
        "homestay_bot.test",
        logging.ERROR,
        __file__,
        1,
        "后台任务失败",
        (),
        exc_info,
    )


def test_log_filter_redacts_secrets_inside_exception_traceback() -> None:
    """异常栈中的密钥、令牌和回调签名不得随 traceback 输出。"""
    record = _record_with_exception(
        "Hostex-Access-Token: secret-token 调用 "
        "https://example.com/cb?code=secret-code&state=secret-state 失败 "
        "deepseek_api_key=sk-secret-value"
    )

    assert SensitiveDataFilter().filter(record) is True
    rendered = logging.Formatter("%(message)s").format(record)

    assert "secret-token" not in rendered
    assert "secret-code" not in rendered
    assert "secret-state" not in rendered
    assert "sk-secret-value" not in rendered


def test_log_filter_keeps_traceback_structure_after_redaction() -> None:
    """脱敏不得破坏 traceback 的多行结构和异常类型，否则失去定位价值。"""
    record = _record_with_exception("token=secret-token 之后仍需保留定位信息")

    assert SensitiveDataFilter().filter(record) is True
    rendered = logging.Formatter("%(message)s").format(record)

    assert "Traceback (most recent call last):" in rendered
    assert "RuntimeError" in rendered
    assert "_record_with_exception" in rendered
    assert "之后仍需保留定位信息" in rendered
    assert rendered.count("\n") >= 3


def test_log_filter_redacts_already_rendered_exception_text() -> None:
    """其它 formatter 预渲染过的 exc_text 也必须经过脱敏。"""
    record = logging.LogRecord(
        "homestay_bot.test",
        logging.ERROR,
        __file__,
        1,
        "后台任务失败",
        (),
        None,
    )
    record.exc_text = 'RuntimeError: Authorization: Bearer secret-token'

    assert SensitiveDataFilter().filter(record) is True
    assert "secret-token" not in (record.exc_text or "")


def test_log_filter_redacts_stack_info_text() -> None:
    """stack_info=True 采集的调用栈同样不得泄露密钥。"""
    record = logging.LogRecord(
        "homestay_bot.test",
        logging.ERROR,
        __file__,
        1,
        "后台任务失败",
        (),
        None,
        sinfo='Stack (most recent call last):\n  wecom_kf_secret=secret-value',
    )

    assert SensitiveDataFilter().filter(record) is True
    assert "secret-value" not in (record.stack_info or "")
