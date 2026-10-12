import json
import logging
import os
import subprocess
import sys
from io import StringIO
from pathlib import Path

from uvicorn.logging import AccessFormatter

from homestay_bot.logging import (
    SensitiveDataFilter,
    configure_logging_redaction,
    redact_log_fields,
)


def test_log_filter_redacts_tokens_but_keeps_guest_mobile_numbers() -> None:
    """日志不得暴露外部密钥；客人手机号按用户 2026-09-26 决定原样记录（1.41.0）。"""
    filtered = redact_log_fields(
        {
            "hostex_access_token": "secret-token",
            "mobile": "13800138000",
            "request_id": "RT-1",
        }
    )

    assert filtered["hostex_access_token"] == "[REDACTED]"
    assert filtered["mobile"] == "13800138000"
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
    """字典消息和 logger.extra 中的密钥也不得绕过过滤器；手机号原样保留（1.41.0）。"""
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
    assert record.msg["nested"]["phone"] == "13800138000"
    assert record.secret == "[REDACTED]"
    assert record.guest_phone == "13900139000"


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


# 子进程按生产顺序装配：uvicorn 先配置自身日志，再导入应用模块；不添加任何测试 handler。
_PRODUCTION_ASSEMBLY_PROBE = """
import logging
import uvicorn

uvicorn.Config("homestay_bot.main:app").load()
logging.getLogger("homestay_bot.integrations.deepseek_client").info(
    "轮次规划：status=ok elapsed_ms=12 Hostex-Access-Token: probe-secret"
)
"""


def test_production_assembly_emits_redacted_application_info_logs() -> None:
    """生产装配下应用 INFO 日志必须输出到 stderr，且经过脱敏过滤器。

    此前应用日志器链上没有 handler，INFO 被根日志器 WARNING 门槛丢弃，
    子模块 WARNING 经 logging.lastResort 输出时也绕过了脱敏。
    """
    source_root = Path(__file__).resolve().parents[2] / "src"
    # 显式指向当前工作树源码，避免子进程解析到虚拟环境里安装的另一份代码。
    environment = dict(
        os.environ,
        PYTHONPATH=os.pathsep.join(
            item for item in (str(source_root), os.environ.get("PYTHONPATH")) if item
        ),
    )
    result = subprocess.run(
        [sys.executable, "-c", _PRODUCTION_ASSEMBLY_PROBE],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "INFO homestay_bot.integrations.deepseek_client: 轮次规划：status=ok" in result.stderr
    assert "probe-secret" not in result.stderr


def test_log_filter_redacts_bearer_credential_in_plain_message_idempotently() -> None:
    """普通消息里 Authorization 的方案词之后的凭证也要脱敏，重复过滤不叠加。"""
    record = logging.LogRecord(
        "homestay_bot.test",
        logging.WARNING,
        __file__,
        1,
        "Authorization: Bearer secret-xyz",
        (),
        None,
    )
    filter_instance = SensitiveDataFilter()

    assert filter_instance.filter(record) is True
    first = record.getMessage()
    assert "secret-xyz" not in first

    # 同一条记录可能同时经过 logger 和 handler 上的过滤器。
    assert filter_instance.filter(record) is True
    assert record.getMessage() == first
    assert first.count("[REDACTED]") == 1


def _filtered_twice(message: str, args: tuple[object, ...] = ()) -> tuple[str, str]:
    """同一条记录先后经过两层过滤器，返回两次过滤后的正文。"""
    record = logging.LogRecord(
        "homestay_bot.test", logging.WARNING, __file__, 1, message, args, None
    )
    filter_instance = SensitiveDataFilter()
    assert filter_instance.filter(record) is True
    first = record.getMessage()
    assert filter_instance.filter(record) is True
    return first, record.getMessage()


def test_plain_message_redacts_bearer_in_every_common_form() -> None:
    """等号、引号、与带查询参数的 URL 同行时，Bearer 令牌都不得残留，重复过滤不叠加。"""
    cases = [
        ("Authorization=Bearer secret-a1", ()),
        ("GET /oauth/callback?code=code-a2&next=/ Authorization: Bearer secret-a2", ()),
        ('Authorization: "Bearer secret-a3"', ()),
        ("请求头 Authorization: %s", ("Bearer secret-a4",)),
    ]
    for message, args in cases:
        first, second = _filtered_twice(message, args)
        assert "secret-a" not in first, first
        assert "code-a2" not in first, first
        assert second == first
        assert "[REDACTED][REDACTED]" not in first

    # URL 以外的普通文本保持原样，不被当成 URL 重新编码。
    first, _ = _filtered_twice("客人问：能看江景吗？房间 401 Authorization: Bearer secret-a5")
    assert first.startswith("客人问：能看江景吗？房间 401 ")
    assert "secret-a5" not in first


def test_actual_handler_redacts_quoted_keys_and_escaped_values() -> None:
    """真实 handler 的 JSON、repr、异常与多字段输出不能留下转义引号后的秘密尾部。"""
    from homestay_bot.logging import redact_text

    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(SensitiveDataFilter())
    logger = logging.getLogger("synthetic.fix3.quoted_secrets")
    original_handlers, original_level, original_propagate = (
        logger.handlers, logger.level, logger.propagate
    )
    logger.handlers, logger.level, logger.propagate = [handler], logging.INFO, False
    try:
        logger.info("fields=%s status=%s", json.dumps({
            "password": 'A1!"SYNTHETIC_JSON_SUFFIX',
            "Authorization": "Bearer SYNTHETIC_BEARER_SUFFIX",
            "request_id": "RT-1",
        }), 401)
        logger.warning("fields=%s status=%s", {
            "api_key": "A1!'\"SYNTHETIC_REPR_SUFFIX",
            "secret": "A1!\\\\SYNTHETIC_SLASH_SUFFIX",
        }, 403)
        try:
            raise RuntimeError(json.dumps({
                "api_key": 'A1!"SYNTHETIC_EXCEPTION_SUFFIX',
                "authorization": "Basic SYNTHETIC_BASIC_SUFFIX",
            }))
        except RuntimeError:
            logger.exception("调用失败")
    finally:
        logger.handlers, logger.level, logger.propagate = (
            original_handlers, original_level, original_propagate
        )
        handler.close()

    output = stream.getvalue()
    assert "SYNTHETIC_" not in output
    assert "A1!" not in output
    assert "RT-1" in output and "status=401" in output and "status=403" in output
    assert "Traceback (most recent call last):" in output and "RuntimeError:" in output
    assert output.count("\n") >= 5
    assert redact_text(output) == output
