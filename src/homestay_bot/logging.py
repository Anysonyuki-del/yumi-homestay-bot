import re
from collections.abc import Mapping
from typing import Any

_SENSITIVE_KEY_PATTERN = re.compile(
    r"token|secret|password|api[_-]?key|authorization", re.IGNORECASE
)
_MOBILE_KEY_PATTERN = re.compile(r"mobile|phone", re.IGNORECASE)
_TOKEN_IN_TEXT_PATTERN = re.compile(
    r"(?i)(Hostex-Access-Token|Authorization)\s*:\s*\S+"
)


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
