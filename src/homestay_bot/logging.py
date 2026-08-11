import logging
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SENSITIVE_KEY_PATTERN = re.compile(
    # aes_key 覆盖企业微信回调 EncodingAESKey；不放宽到裸 key，避免误伤业务幂等键。
    r"token|secret|password|api[_-]?key|aes[_-]?key|authorization",
    re.IGNORECASE,
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

# LogRecord 的标准字段不属于业务 extra，避免把日志元数据误当成业务内容。
_STANDARD_RECORD_FIELDS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }
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
        """修改消息、参数和 extra 副本并允许记录继续输出。"""
        if isinstance(record.args, tuple):
            # 令牌名称通常位于格式字符串而不是参数键中，先渲染整条消息，
            # 再替换并冻结参数，避免子 logger 的 handler 漏掉令牌正文。
            try:
                rendered = record.getMessage()
            except (TypeError, ValueError):
                rendered = None
            if rendered is not None:
                redacted = _redact_url(rendered)
                if redacted != rendered:
                    record.msg = redacted
                    record.args = ()
                else:
                    record.args = tuple(
                        _redact_url(item) if isinstance(item, str) else item
                        for item in record.args
                    )
        elif isinstance(record.args, dict):
            record.args = redact_log_fields(record.args)

        # logging 允许直接把字典或列表作为 msg；这种写法没有 args，
        # 因而必须在消息对象本身递归处理，不能只依赖 getMessage()。
        if isinstance(record.msg, (Mapping, list)):
            record.msg = _redact_value("", record.msg)

        # extra 字段会直接挂到 LogRecord 上，逐项过滤可以覆盖 token、手机号、
        # 嵌套字典等自定义字段，同时跳过标准日志元数据。
        for key, value in list(record.__dict__.items()):
            if key in _STANDARD_RECORD_FIELDS:
                continue
            redacted = _redact_value(key, value)
            if redacted != value:
                setattr(record, key, redacted)
        return True


def configure_logging_redaction() -> None:
    """把敏感信息过滤器安装到应用与 Uvicorn 访问日志。"""
    filter_instance = SensitiveDataFilter()
    for logger_name in ("uvicorn.access", "homestay_bot"):
        logger = logging.getLogger(logger_name)
        if not any(
            isinstance(item, SensitiveDataFilter) for item in logger.filters
        ):
            logger.addFilter(filter_instance)

    # 记录从子 logger 传播时不会再次执行父 logger 的过滤器，必须把过滤器
    # 安装到当前已配置的 handler；这样 SQL、路径和访问日志都能统一脱敏。
    handlers: list[logging.Handler] = list(logging.getLogger().handlers)
    for item in logging.Logger.manager.loggerDict.values():
        if isinstance(item, logging.Logger):
            handlers.extend(item.handlers)
    seen: set[int] = set()
    for handler in handlers:
        if id(handler) in seen:
            continue
        seen.add(id(handler))
        if not any(
            isinstance(item, SensitiveDataFilter) for item in handler.filters
        ):
            handler.addFilter(filter_instance)
