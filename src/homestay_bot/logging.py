import logging
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SENSITIVE_KEY_PATTERN = re.compile(
    r"token|secret|password|api[_-]?key|authorization", re.IGNORECASE
)
_MOBILE_KEY_PATTERN = re.compile(r"mobile|phone", re.IGNORECASE)
_TOKEN_IN_TEXT_PATTERN = re.compile(
    r"(?i)(Hostex-Access-Token|Authorization)\s*:\s*\S+"
)
_SENSITIVE_QUERY_KEYS = {
    "code",
    "state",
    "msg_signature",
    "signature",
    "echostr",
    "token",
}


def _mask_mobile(value: str) -> str:
    """保留号码首三位和末四位，其余字符替换为星号。"""
    if len(value) >= 7:
        return f"{value[:3]}{'*' * (len(value) - 7)}{value[-4:]}"
    return "[REDACTED]"


def _redact_value(key: str, value: Any) -> Any:
    """根据字段名递归脱敏，不改变可安全审计的请求编号。"""
    if _SENSITIVE_KEY_PATTERN.search(key):
        return "[REDACTED]"
    if _MOBILE_KEY_PATTERN.search(key) and isinstance(value, str):
        return _mask_mobile(value)
    if isinstance(value, Mapping):
        return {item_key: _redact_value(str(item_key), item) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value("", item) for item in value]
    if isinstance(value, str):
        return _TOKEN_IN_TEXT_PATTERN.sub(r"\1: [REDACTED]", value)
    return value


def redact_log_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """返回递归脱敏后的日志字段副本，不修改调用方原始数据。"""
    return {key: _redact_value(key, value) for key, value in fields.items()}


def _redact_url(value: str) -> str:
    """脱敏 URL 查询参数中的 OAuth code、state 和回调签名。"""
    if "?" not in value:
        return _TOKEN_IN_TEXT_PATTERN.sub(r"\1: [REDACTED]", value)
    split = urlsplit(value)
    query = [
        (key, "[REDACTED]" if key.lower() in _SENSITIVE_QUERY_KEYS else item)
        for key, item in parse_qsl(split.query, keep_blank_values=True)
    ]
    return urlunsplit(
        (split.scheme, split.netloc, split.path, urlencode(query), split.fragment)
    )


class SensitiveDataFilter(logging.Filter):
    """在日志格式化前脱敏结构化字段和访问日志 URL。"""

    def filter(self, record: logging.LogRecord) -> bool:
        """修改日志参数副本并允许记录继续输出。"""
        if isinstance(record.args, tuple):
            record.args = tuple(
                _redact_url(item) if isinstance(item, str) else item
                for item in record.args
            )
        elif isinstance(record.args, dict):
            record.args = redact_log_fields(record.args)
        return True


def configure_logging_redaction() -> None:
    """把敏感信息过滤器安装到应用与 Uvicorn 访问日志。"""
    for logger_name in ("uvicorn.access", "homestay_bot"):
        logger = logging.getLogger(logger_name)
        if not any(
            isinstance(item, SensitiveDataFilter) for item in logger.filters
        ):
            logger.addFilter(SensitiveDataFilter())
